import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import sounddevice as sd

from config import settings
from src.audio_io import float32_to_int16, int16_to_float32, resample
from src.gemini_sts import GeminiSTS
from src.logger import logger


def record_audio(duration: float, sample_rate: int = 16000, device: str | int | None = None) -> np.ndarray:
    """Record mono int16 audio from microphone for specified duration."""
    print(f"\n🔴 Recording for {duration:.1f} seconds... Speak now!")
    frames = int(duration * sample_rate)
    recording = sd.rec(
        frames=frames,
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        device=device,
    )
    sd.wait()
    print("⏹️ Recording completed.")
    return recording.reshape(-1)


def playback_audio(audio: np.ndarray, sample_rate: int = 16000, device: str | int | None = None) -> None:
    """Play back mono audio array through speakers."""
    print(f"🔊 Playing back {len(audio) / sample_rate:.1f}s of audio through speakers...")
    sd.play(audio, samplerate=sample_rate, device=device)
    sd.wait()
    print("✅ Playback finished.")


async def phase1_hardware_loopback(sample_rate: int, in_dev: Any, out_dev: Any) -> None:
    """Phase 1: Record 3s from mic -> play back through speakers directly."""
    print("\n" + "=" * 65)
    print("PHASE 1: Hardware Loopback Verification (Bypass AI)")
    print("=" * 65)
    print("This will record your voice for 3 seconds and immediately play it back.")
    input("Press ENTER to start recording Phase 1...")

    audio = record_audio(duration=3.0, sample_rate=sample_rate, device=in_dev)

    # Check that audio is not completely silent
    rms = np.sqrt(np.mean(audio.astype(np.float64) ** 2))
    print(f"Recorded signal RMS level: {rms:.1f} / 32767")
    if rms < 50:
        print("⚠️ Warning: Recorded audio level is very low. Check your mic volume.")

    playback_audio(audio, sample_rate=sample_rate, device=out_dev)
    print("✅ Phase 1 passed: Microphone and speaker hardware verified.")


async def phase2_gemini_sts(sample_rate: int, in_dev: Any, out_dev: Any, dry_run: bool) -> None:
    """Phase 2: Record 3s -> send to Gemini Live STS -> play response."""
    print("\n" + "=" * 65)
    print(f"PHASE 2: End-to-End Gemini Live STS Verification ({'DRY RUN' if dry_run else 'LIVE API'})")
    print("=" * 65)
    print("Record 3 seconds of speech to send to Gemini Live STS.")
    input("Press ENTER to start recording Phase 2...")

    audio = record_audio(duration=3.0, sample_rate=sample_rate, device=in_dev)
    pcm_bytes = audio.tobytes()

    print("\n🤖 Connecting to Gemini Live STS...")
    client = GeminiSTS(
        api_key=settings.gemini_api_key,
        model=settings.effective_model,
        input_rate=sample_rate,
        output_rate=24000,
        voice=settings.gemini_voice,
        dry_run=dry_run,
        system_instruction="You are a helpful assistant. Reply with a short, friendly sentence.",
    )

    in_q = asyncio.Queue()
    out_q = asyncio.Queue()

    run_task = asyncio.create_task(client.run(in_q, out_q))

    # Feed recorded audio in 1024-byte chunks
    chunk_size = 2048
    print("📡 Streaming recorded audio to Gemini Live STS...")
    for i in range(0, len(pcm_bytes), chunk_size):
        await in_q.put(pcm_bytes[i : i + chunk_size])
        await asyncio.sleep(0.01)

    print("📥 Waiting for Gemini synthesized speech response...")
    received_pcm = bytearray()
    start_time = time.time()

    try:
        # Collect response chunks for up to 10 seconds
        while time.time() - start_time < 10.0:
            try:
                chunk = await asyncio.wait_for(out_q.get(), timeout=2.5)
                received_pcm.extend(chunk)
                print(f"  Received chunk: {len(chunk)} bytes (total: {len(received_pcm)} bytes)")
                if dry_run and len(received_pcm) >= len(pcm_bytes):
                    break
            except asyncio.TimeoutError:
                if len(received_pcm) > 0:
                    break
    finally:
        await client.close()
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass

    if len(received_pcm) == 0:
        print("❌ Error: No audio received from Gemini Live STS.")
        sys.exit(1)

    print(f"\n🎉 Received {len(received_pcm)} bytes of synthesized speech!")
    response_audio = np.frombuffer(received_pcm, dtype=np.int16)

    # Gemini native output is 24kHz; playback at 24kHz (or input_rate in dry_run)
    playback_rate = 24000 if not dry_run else sample_rate
    playback_audio(response_audio, sample_rate=playback_rate, device=out_dev)
    print("\n✅ Phase 2 passed: Gemini Live STS end-to-end pipeline verified!")


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Smoke Test for AI Virtual Microphone")
    parser.add_argument(
        "--bypass-only",
        action="store_true",
        help="Run only Phase 1 (hardware loopback without AI)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run Phase 2 in dry-run echo mode (no API key required)",
    )
    args = parser.parse_args()

    sample_rate = settings.sample_rate
    in_dev = settings.resolved_input_device
    out_dev = settings.resolved_output_device

    print("Starting AI Virtual Microphone Smoke Test...")
    print(f"Config: rate={sample_rate}Hz, in_device={in_dev}, out_device={out_dev}")

    # Run Phase 1
    await phase1_hardware_loopback(sample_rate, in_dev, out_dev)

    # Run Phase 2 unless bypass-only requested
    if not args.bypass_only:
        await phase2_gemini_sts(sample_rate, in_dev, out_dev, dry_run=args.dry_run)

    print("\n" + "=" * 65)
    print("ALL SMOKE TESTS COMPLETED SUCCESSFULLY!")
    print("=" * 65 + "\n")


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        print("\nSmoke test cancelled by user.")


if __name__ == "__main__":
    main()
