import asyncio
from functools import lru_cache
import math
import queue
import threading
from collections.abc import Callable
from typing import Any

import numpy as np
import sounddevice as sd
from scipy import signal

from config import settings
from src.logger import logger


def list_audio_devices() -> list[dict[str, Any]]:
    """Return list of all audio devices on the system."""
    return list(sd.query_devices())


def find_device(
    name_or_index: int | str | None,
    is_input: bool = True,
) -> int | None:
    """Resolve a device index by int id or case-insensitive substring match."""
    if name_or_index is None or str(name_or_index).strip().lower() in ("default", ""):
        return None

    if isinstance(name_or_index, int) or (
        isinstance(name_or_index, str) and name_or_index.strip().isdigit()
    ):
        return int(name_or_index)

    query = str(name_or_index).strip().lower()
    devices = sd.query_devices()

    for idx, dev in enumerate(devices):
        channels = (
            dev.get("max_input_channels", 0)
            if is_input
            else dev.get("max_output_channels", 0)
        )
        if channels > 0 and query in dev.get("name", "").lower():
            logger.debug(
                f"Matched audio device: [{idx}] '{dev['name']}' "
                f"(type: {'input' if is_input else 'output'})"
            )
            return idx

    return None


find_device_index = find_device  # backwards compat


# ---------------------------------------------------------------------------
# Audio utilities
# ---------------------------------------------------------------------------

def float32_to_int16(x: np.ndarray) -> np.ndarray:
    """Convert float32 [-1.0, 1.0] audio to int16 PCM."""
    x = np.asarray(x, dtype=np.float32)
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16)


def int16_to_float32(x: np.ndarray) -> np.ndarray:
    """Convert int16 PCM to float32 [-1.0, 1.0]."""
    x = np.asarray(x, dtype=np.int16)
    return (x.astype(np.float32) / 32767.0).astype(np.float32)


@lru_cache(maxsize=16)
def _resample_filter(up: int, down: int, half_len: int = 10) -> np.ndarray:
    """Cache precomputed FIR filter for a given up/down polyphase ratio."""
    max_rate = max(up, down)
    return signal.firwin(
        2 * half_len * max_rate + 1, 1.0 / max_rate, window=("kaiser", 5.0)
    ).astype(np.float32)


def resample(
    pcm: np.ndarray | bytes,
    src_rate: int,
    dst_rate: int,
) -> np.ndarray | bytes:
    """Resample audio between rates using cached polyphase filtering. Preserves dtype."""
    if src_rate == dst_rate:
        return pcm

    is_bytes = isinstance(pcm, bytes)
    arr = np.frombuffer(pcm, dtype=np.int16) if is_bytes else np.asarray(pcm)

    gcd = math.gcd(src_rate, dst_rate)
    up = dst_rate // gcd
    down = src_rate // gcd

    is_int16 = arr.dtype == np.int16
    float_data = arr.astype(np.float32)
    filt = _resample_filter(up, down)
    resampled = signal.resample_poly(float_data, up, down, window=filt)

    if is_int16:
        resampled = np.clip(resampled, -32768, 32767).astype(np.int16)

    return resampled.tobytes() if is_bytes else resampled


def silence(duration: float, sample_rate: int = 16000) -> np.ndarray:
    """Generate silent int16 array of given duration in seconds."""
    return np.zeros(int(duration * sample_rate), dtype=np.int16)


# ---------------------------------------------------------------------------
# MicrophoneInput
# ---------------------------------------------------------------------------

class MicrophoneInput:
    """Capture microphone PCM audio using sounddevice.InputStream."""

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
        callback: Callable[[np.ndarray], None] | queue.Queue | asyncio.Queue | None = None,
    ):
        self.device_raw = device if device is not None else settings.resolved_input_device
        self.device_index = find_device(self.device_raw, is_input=True)
        self.sample_rate = sample_rate or settings.sample_rate
        self.channels = channels or settings.channels
        self.chunk_size = chunk_size or settings.chunk_size

        self.stream: sd.InputStream | None = None
        self.queue: queue.Queue[np.ndarray] = queue.Queue()
        self._async_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._user_callback: Callable[[np.ndarray], None] | None = None
        self._is_running = False

        if callable(callback):
            self._user_callback = callback
        elif isinstance(callback, queue.Queue):
            self.queue = callback
        elif isinstance(callback, asyncio.Queue):
            self._async_queue = callback

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        devices = list_audio_devices()
        print("\n=== Audio Input & Output Devices ===")
        for idx, dev in enumerate(devices):
            in_ch = dev.get("max_input_channels", 0)
            out_ch = dev.get("max_output_channels", 0)
            print(f"[{idx:2d}] {dev['name']} - In: {in_ch}, Out: {out_ch}")
        print("=====================================\n")
        return devices

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.warning(f"Microphone input status flag: {status}")
        if not self._is_running:
            return

        data = indata.copy() if indata.dtype == np.int16 else float32_to_int16(indata)

        # Remove hardware DC offset (common on laptop built-in microphones)
        samples_f = data.astype(np.float32)
        samples_f -= np.mean(samples_f)
        data = np.clip(samples_f, -32768.0, 32767.0).astype(np.int16)

        if self._user_callback:
            try:
                self._user_callback(data)
            except Exception as e:
                logger.error(f"Error in user audio callback: {e}")

        try:
            self.queue.put_nowait(data)
        except queue.Full:
            pass

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._async_queue.put_nowait, data.tobytes())

    def start(
        self,
        callback: Callable[[np.ndarray], None] | queue.Queue | asyncio.Queue | None = None,
    ) -> None:
        if self._is_running:
            return

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

        if callable(callback):
            self._user_callback = callback
        elif isinstance(callback, queue.Queue):
            self.queue = callback
        elif isinstance(callback, asyncio.Queue):
            self._async_queue = callback

        logger.info(
            f"Starting MicrophoneInput on device={self.device_index} "
            f"(target='{self.device_raw}'), rate={self.sample_rate}Hz, "
            f"channels={self.channels}, chunk_size={self.chunk_size}"
        )

        self.stream = sd.InputStream(
            device=self.device_index,
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=self.chunk_size,
            dtype="int16",
            callback=self._audio_callback,
        )
        self.stream.start()
        self._is_running = True

    def stop(self) -> None:
        self._is_running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error stopping MicrophoneInput stream: {e}")
            finally:
                self.stream = None
        logger.info("MicrophoneInput stopped.")

    async def read_chunk(self) -> bytes:
        return await self._async_queue.get()

    def __enter__(self) -> "MicrophoneInput":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    async def __aenter__(self) -> "MicrophoneInput":
        self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# SpeakerOutput
# ---------------------------------------------------------------------------

class SpeakerOutput:
    """Write PCM frames to an output device via sounddevice.OutputStream."""

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
    ):
        self.device_raw = device if device is not None else settings.resolved_output_device
        self.device_index = find_device(self.device_raw, is_input=False)
        self.sample_rate = sample_rate or settings.sample_rate
        self.channels = channels or settings.channels
        self.chunk_size = chunk_size or settings.chunk_size

        self.stream: sd.OutputStream | None = None
        self._is_running = False

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        return list_audio_devices()

    def start(self) -> None:
        if self._is_running:
            return

        logger.info(
            f"Starting SpeakerOutput on device={self.device_index} "
            f"(target='{self.device_raw}'), rate={self.sample_rate}Hz, "
            f"channels={self.channels}, chunk_size={self.chunk_size}"
        )

        self.stream = sd.OutputStream(
            device=self.device_index,
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=self.chunk_size,
            dtype="int16",
        )
        self.stream.start()
        self._is_running = True

    def write(self, data: np.ndarray | bytes) -> None:
        if not self._is_running or not self.stream:
            raise RuntimeError("SpeakerOutput is not running. Call start() first.")

        if isinstance(data, bytes):
            arr = np.frombuffer(data, dtype=np.int16).reshape(-1, self.channels)
        elif isinstance(data, np.ndarray):
            arr = (
                float32_to_int16(data).reshape(-1, self.channels)
                if data.dtype == np.float32
                else data.reshape(-1, self.channels)
            )
        else:
            raise TypeError("Audio data must be np.ndarray or bytes")

        self.stream.write(arr)

    def stop(self) -> None:
        self._is_running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error stopping SpeakerOutput stream: {e}")
            finally:
                self.stream = None
        logger.info("SpeakerOutput stopped.")

    def __enter__(self) -> "SpeakerOutput":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# VirtualMicOutput
# ---------------------------------------------------------------------------

VIRTUAL_MIC_INSTALL_HELP = (
    "Virtual audio loopback device was not found!\n"
    "Please configure a virtual microphone:\n"
    "  • Linux (PulseAudio / PipeWire):\n"
    "      pactl load-module module-null-sink sink_name=ai_mic "
    "sink_properties=device.description=\"AI_Virtual_Mic_Sink\"\n"
    "      pactl load-module module-remap-source master=ai_mic.monitor "
    "source_name=AI_Microphone\n"
    "  • Windows: Install VB-Audio Virtual Cable (https://vb-audio.com/Cable/) "
    "and set VIRTUAL_MIC_DEVICE='CABLE Input'\n"
    "  • macOS: Install BlackHole 2ch (https://github.com/ExistentialAudio/BlackHole) "
    "and set VIRTUAL_MIC_DEVICE='BlackHole 2ch'"
)


class VirtualMicOutput:
    """Route PCM audio to a system virtual loopback device.

    Buffers PCM in a thread-safe queue drained by a background thread into
    sounddevice.OutputStream.
    """

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int | None = None,
        channels: int = 1,
        chunk_size: int = 1024,
    ):
        self.device_raw = device if device is not None else settings.resolved_virtual_mic_device
        self.sample_rate = sample_rate or getattr(
            settings, "virtual_mic_sample_rate", 48000
        )
        self.channels = channels or settings.channels
        self.chunk_size = chunk_size or settings.chunk_size

        self.device_index = find_device(self.device_raw, is_input=False)
        if self.device_index is None and self.device_raw not in (None, "default"):
            error_msg = (
                f"Virtual mic device '{self.device_raw}' not found.\n"
                f"{VIRTUAL_MIC_INSTALL_HELP}"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        self.stream: sd.OutputStream | None = None
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._drain_thread: threading.Thread | None = None
        self._is_running = False

    def _drain_worker(self) -> None:
        logger.debug("VirtualMicOutput drain worker started.")
        while self._is_running:
            try:
                chunk = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if not self._is_running or not self.stream:
                break

            try:
                self.stream.write(chunk)
            except Exception as e:
                logger.error(f"Error writing to virtual mic stream: {e}")

        logger.debug("VirtualMicOutput drain worker finished.")

    def start(self) -> None:
        if self._is_running:
            return

        if self.device_index is None and self.device_raw not in (None, "default"):
            error_msg = (
                f"Virtual mic device '{self.device_raw}' not found.\n"
                f"{VIRTUAL_MIC_INSTALL_HELP}"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        logger.info(
            f"Starting VirtualMicOutput on device={self.device_index} "
            f"(target='{self.device_raw}'), rate={self.sample_rate}Hz, "
            f"channels={self.channels}, chunk_size={self.chunk_size}"
        )

        self.stream = sd.OutputStream(
            device=self.device_index,
            channels=self.channels,
            samplerate=self.sample_rate,
            blocksize=self.chunk_size,
            dtype="int16",
        )
        self.stream.start()
        self._is_running = True

        self._drain_thread = threading.Thread(target=self._drain_worker, daemon=True)
        self._drain_thread.start()

    def write(self, data: np.ndarray | bytes) -> None:
        if not self._is_running:
            logger.warning("VirtualMicOutput is not running; dropping chunk.")
            return

        if isinstance(data, bytes):
            arr = np.frombuffer(data, dtype=np.int16).reshape(-1, self.channels)
        elif isinstance(data, np.ndarray):
            arr = (
                float32_to_int16(data).reshape(-1, self.channels)
                if data.dtype == np.float32
                else data.reshape(-1, self.channels)
            )
        else:
            raise TypeError("Audio data must be np.ndarray or bytes")

        try:
            self.queue.put(arr, timeout=0.2)
        except queue.Full:
            logger.warning("Virtual mic queue full; dropping frame.")

    async def write_chunk(self, data: np.ndarray | bytes) -> None:
        self.write(data)

    def stop(self) -> None:
        if not self._is_running:
            return

        self._is_running = False
        if self._drain_thread and self._drain_thread.is_alive():
            self._drain_thread.join(timeout=1.0)
            self._drain_thread = None

        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error stopping VirtualMicOutput stream: {e}")
            finally:
                self.stream = None
        logger.info("VirtualMicOutput stopped.")

    def __enter__(self) -> "VirtualMicOutput":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


class NoiseGate:
    """Energy-based voice activity detector with adaptive noise floor.

    Only passes audio through when RMS exceeds the estimated noise floor
    by a configurable margin. Includes hangover (tail) to avoid clipping
    word endings.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_ms: float = 30.0,
        threshold_db: float = 10.0,
        hangover_ms: float = 300.0,
        noise_floor_alpha: float = 0.97,
        min_noise_floor_db: float = -60.0,
        max_noise_floor_db: float = -28.0,
    ):
        self.sample_rate = sample_rate
        self.frame_samples = int(sample_rate * frame_ms / 1000)
        self.threshold_db = threshold_db
        self.hangover_frames = max(1, int(hangover_ms / frame_ms))
        self.noise_floor_alpha = noise_floor_alpha
        self.min_noise_floor_db = min_noise_floor_db
        self.max_noise_floor_db = max_noise_floor_db

        # Adaptive noise floor (in dBFS) - start at min floor so speech is not missed
        self._noise_floor_db: float = min_noise_floor_db
        self._hangover_remaining: int = 0

    @staticmethod
    def _rms_db(samples: np.ndarray) -> float:
        """Return RMS level in dBFS for int16 samples after DC removal."""
        if samples.size == 0:
            return -120.0
        f = samples.astype(np.float32)
        f -= np.mean(f)  # Ensure DC is removed before measuring energy
        f /= 32768.0
        rms = float(np.sqrt(np.mean(f * f)) + 1e-12)
        return 20.0 * np.log10(rms)

    def _update_noise_floor(self, level_db: float) -> None:
        """Exponential moving average, tracking quiet periods."""
        if level_db <= self._noise_floor_db + 6.0:
            self._noise_floor_db = (
                self.noise_floor_alpha * self._noise_floor_db
                + (1 - self.noise_floor_alpha) * level_db
            )
            self._noise_floor_db = max(
                self.min_noise_floor_db,
                min(self._noise_floor_db, self.max_noise_floor_db),
            )

    def process(self, chunk: bytes | np.ndarray) -> bool:
        """Return True if chunk contains speech, False if it's silence/noise."""
        if isinstance(chunk, bytes):
            samples = np.frombuffer(chunk, dtype=np.int16)
        else:
            samples = np.asarray(chunk, dtype=np.int16)

        if samples.size == 0:
            return False

        level_db = self._rms_db(samples)
        floor = self._noise_floor_db
        threshold = floor + self.threshold_db

        # If energy exceeds threshold, trigger speech + hangover
        if level_db >= threshold:
            self._hangover_remaining = self.hangover_frames
            return True

        # Only adapt noise floor during confirmed quiet (not during speech hangover)
        if self._hangover_remaining == 0:
            self._update_noise_floor(level_db)

        if self._hangover_remaining > 0:
            self._hangover_remaining -= 1
            return True

        return False

    def reset(self) -> None:
        self._hangover_remaining = 0


# Backwards compatibility aliases
AudioCapture = MicrophoneInput
AudioPlayback = SpeakerOutput

__all__ = [
    "AudioCapture",
    "AudioPlayback",
    "MicrophoneInput",
    "NoiseGate",
    "SpeakerOutput",
    "VirtualMicOutput",
    "VIRTUAL_MIC_INSTALL_HELP",
    "find_device",
    "find_device_index",
    "float32_to_int16",
    "int16_to_float32",
    "list_audio_devices",
    "resample",
    "silence",
]