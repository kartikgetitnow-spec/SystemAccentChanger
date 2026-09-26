import asyncio
import signal
import time

import numpy as np

from config import Settings
from config import settings as global_settings
from src.audio_io import (
    MicrophoneInput,
    NoiseGate,
    VirtualMicOutput,
    float32_to_int16,
    resample,
)
from src.gemini_sts import GeminiSTS
from src.logger import logger


class AIMicPipeline:
    """Real-time pipeline: Physical Mic → Gemini STS (accent convert) → Virtual Mic."""

    def __init__(
        self,
        settings: Settings | None = None,
        dry_run: bool = False,
    ):
        self.settings = settings or global_settings
        self.dry_run = dry_run

        # Backpressure queues
        self.mic_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
        self.ai_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)

        # Metrics
        self.frames_in: int = 0
        self.frames_out: int = 0
        self.frames_dropped: int = 0
        self.frames_suppressed: int = 0
        self.bytes_out: int = 0
        self._last_logged_in: int = 0
        self._last_logged_out: int = 0
        self._last_drop_log: float = 0.0

        # Components
        self.mic = MicrophoneInput(
            device=self.settings.resolved_input_device,
            sample_rate=self.settings.sample_rate,
            channels=self.settings.channels,
            chunk_size=self.settings.chunk_size,
            callback=self._on_mic_audio,
        )
        self.gemini = GeminiSTS(
            api_key=self.settings.gemini_api_key,
            model=self.settings.effective_model,
            input_rate=self.settings.sample_rate,
            output_rate=24000,
            voice=self.settings.effective_voice,
            dry_run=self.dry_run,
        )
        self.virtual_mic = VirtualMicOutput(
            device=self.settings.resolved_virtual_mic_device,
            sample_rate=self.settings.virtual_mic_sample_rate,
            channels=self.settings.channels,
            chunk_size=self.settings.chunk_size,
        )

        # Noise gate (VAD) — filters fan hum / background noise
        self._noise_gate: NoiseGate | None = (
            NoiseGate(
                sample_rate=self.settings.sample_rate,
                threshold_db=self.settings.noise_gate_threshold_db,
                hangover_ms=self.settings.noise_gate_hangover_ms,
            )
            if getattr(self.settings, "enable_noise_gate", False)
            else None
        )

        self._is_running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Mic callback → queue (thread-safe, drop-oldest on backpressure)
    # ------------------------------------------------------------------
    def _push_mic_frame_threadsafe(self, pcm_bytes: bytes) -> None:
        if not self._is_running:
            return

        # Noise gate: drop non-speech frames (fan noise, background hum)
        if self._noise_gate is not None:
            if not self._noise_gate.process(pcm_bytes):
                self.frames_suppressed += 1
                return

        if self.mic_queue.full():
            try:
                self.mic_queue.get_nowait()
                self.frames_dropped += 1
                now = time.monotonic()
                if now - self._last_drop_log > 5.0:
                    logger.warning(
                        f"Backpressure: mic_queue full ({self.mic_queue.maxsize}). "
                        f"Dropped {self.frames_dropped} frames so far."
                    )
                    self._last_drop_log = now
            except asyncio.QueueEmpty:
                pass

        try:
            self.mic_queue.put_nowait(pcm_bytes)
        except asyncio.QueueFull:
            self.frames_dropped += 1

    def _on_mic_audio(self, data: np.ndarray) -> None:
        if not self._is_running:
            return

        pcm_bytes = (
            data.tobytes()
            if data.dtype == np.int16
            else float32_to_int16(data).tobytes()
        )
        self.frames_in += 1

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(
                self._push_mic_frame_threadsafe, pcm_bytes
            )

    # ------------------------------------------------------------------
    # Consumer: ai_queue → resample → VirtualMicOutput
    # ------------------------------------------------------------------
    async def _consumer_loop(self) -> None:
        logger.info("Starting consumer task (ai_queue → VirtualMicOutput)...")
        try:
            while self._is_running:
                try:
                    chunk = await asyncio.wait_for(self.ai_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue

                if chunk is None:
                    break

                src_rate = (
                    self.settings.sample_rate if self.dry_run else self.gemini.output_rate
                )
                dst_rate = self.virtual_mic.sample_rate
                if src_rate != dst_rate:
                    chunk = resample(chunk, src_rate=src_rate, dst_rate=dst_rate)

                self.virtual_mic.write(chunk)
                self.frames_out += 1
                self.bytes_out += len(chunk)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            logger.debug("Consumer task cancelled.")
            raise
        except Exception as e:
            logger.error(f"Error in consumer loop: {e}")
            raise

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------
    async def _metrics_loop(self) -> None:
        try:
            while self._is_running:
                await asyncio.sleep(15)
                if (
                    self.frames_in - self._last_logged_in > 0
                    or self.frames_out != self._last_logged_out
                ):
                    logger.info(
                        f"Pipeline Metrics | In: +{self.frames_in - self._last_logged_in} "
                        f"(total {self.frames_in}) | "
                        f"Out: +{self.frames_out - self._last_logged_out} "
                        f"(total {self.frames_out}, {self.bytes_out} bytes) | "
                        f"Suppressed(noise): {self.frames_suppressed} | "
                        f"Dropped: {self.frames_dropped} | "
                        f"Queues: mic={self.mic_queue.qsize()}, ai={self.ai_queue.qsize()}"
                    )
                    self._last_logged_in = self.frames_in
                    self._last_logged_out = self.frames_out
                else:
                    logger.debug(
                        f"Pipeline Metrics (idle) | In: {self.frames_in} | "
                        f"Out: {self.frames_out} | Dropped: {self.frames_dropped}"
                    )
        except asyncio.CancelledError:
            logger.debug("Metrics loop cancelled.")
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._is_running:
            return

        logger.info("Starting AIMicPipeline...")
        self._loop = asyncio.get_running_loop()
        self._is_running = True
        self._stop_event.clear()

        if getattr(self.settings, "enable_accent_conversion", False):
            logger.info(
                f"Accent conversion ACTIVE | {self.settings.source_accent} → "
                f"{self.settings.target_accent} | gender={self.settings.voice_gender} | "
                f"tone={self.settings.accent_mode} | lang={self.settings.target_language} | "
                f"voice={self.settings.effective_voice}"
            )
        else:
            logger.info(
                f"Accent conversion DISABLED — verbatim relay mode "
                f"(gender={self.settings.voice_gender}, voice={self.settings.effective_voice})."
            )

        self.virtual_mic.start()

        consumer_task = asyncio.create_task(self._consumer_loop(), name="pipeline_consumer")
        gemini_task = asyncio.create_task(
            self.gemini.run(self.mic_queue, self.ai_queue),
            name="pipeline_gemini_sts",
        )
        metrics_task = asyncio.create_task(self._metrics_loop(), name="pipeline_metrics")
        self._tasks = [consumer_task, gemini_task, metrics_task]

        self.mic.start()
        logger.info("AIMicPipeline started successfully.")

    async def stop(self) -> None:
        if not self._is_running:
            return

        logger.info("Stopping AIMicPipeline...")
        self._is_running = False
        self._stop_event.set()

        self.mic.stop()

        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        while not self.mic_queue.empty():
            try:
                self.mic_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        while not self.ai_queue.empty():
            try:
                self.ai_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        await self.gemini.close()
        self.virtual_mic.stop()

        logger.info(
            f"AIMicPipeline stopped. Final stats: In={self.frames_in}, "
            f"Out={self.frames_out} ({self.bytes_out} bytes), "
            f"Dropped={self.frames_dropped}"
        )

    async def run_forever(self) -> None:
        await self.start()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except (ValueError, NotImplementedError, RuntimeError):
                pass

        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            logger.info("Pipeline run_forever cancelled.")
        finally:
            await self.stop()


STSPipeline = AIMicPipeline  # backwards-compat alias

__all__ = ["AIMicPipeline", "STSPipeline"]