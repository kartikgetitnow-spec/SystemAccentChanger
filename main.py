import os

# Cap math/BLAS background worker threads to keep CPU utilization and fan noise low
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import argparse
import asyncio
import sys

from config import Settings, settings
from src.audio_io import (
    VIRTUAL_MIC_INSTALL_HELP,
    find_device,
    list_audio_devices,
)
from src.logger import logger, setup_logger
from src.pipeline import AIMicPipeline


def show_devices() -> None:
    """Print available audio devices + current configuration."""
    devices = list_audio_devices()
    print("\n" + "=" * 65)
    print("AVAILABLE AUDIO DEVICES")
    print("=" * 65)
    for idx, dev in enumerate(devices):
        in_ch = dev.get("max_input_channels", 0)
        out_ch = dev.get("max_output_channels", 0)
        flags = []
        if in_ch > 0:
            flags.append(f"{in_ch} in")
        if out_ch > 0:
            flags.append(f"{out_ch} out")
        channels_str = ", ".join(flags) if flags else "no channels"
        print(f"[{idx:2d}] {dev['name']} ({dev.get('hostapi', '')}) - {channels_str}")

    print("\n" + "-" * 65)
    print("CURRENT CONFIGURATION:")
    print(f"  Input Device:       {settings.input_device} (resolved: {settings.resolved_input_device})")
    print(f"  Output Device:      {settings.output_device} (resolved: {settings.resolved_output_device})")
    print(f"  Virtual Mic Device: {settings.virtual_mic_device} (resolved: {settings.resolved_virtual_mic_device})")
    print("=" * 65 + "\n")


def validate_virtual_mic_device(virtual_device: str | int | None) -> bool:
    idx = find_device(virtual_device, is_input=False)
    if idx is None and virtual_device not in (None, "", "default"):
        print("\n" + "=" * 65, file=sys.stderr)
        print(f"ERROR: Virtual microphone device '{virtual_device}' was not found!", file=sys.stderr)
        print("=" * 65, file=sys.stderr)
        print(VIRTUAL_MIC_INSTALL_HELP, file=sys.stderr)
        print("=" * 65 + "\n", file=sys.stderr)
        return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "AI Virtual Microphone using Gemini Live STS with accent/grammar conversion."
        )
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List all audio devices and exit",
    )
    parser.add_argument(
        "-i", "--input",
        dest="input_device",
        type=str,
        default=None,
        help="Physical microphone name or index",
    )
    parser.add_argument(
        "-v", "--virtual-mic",
        dest="virtual_mic_device",
        type=str,
        default=None,
        help="Virtual mic loopback device (e.g. 'CABLE Input', 'ai_mic')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Echo mic directly to virtual mic without calling Gemini",
    )
    parser.add_argument(
        "--voice",
        type=str,
        default=None,
        help="Gemini voice (Puck, Charon, Aoede, Fenrir, Kore)",
    )
    parser.add_argument(
        "--system-prompt",
        type=str,
        default=None,
        help="Override the auto-generated accent-conversion system prompt",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Gemini Live model name",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )

    # ---- Accent conversion flags ----
    parser.add_argument(
        "--mode",
        dest="accent_mode",
        type=str,
        choices=["professional", "friendly", "casual", "formal", "empathetic"],
        default=None,
        help="Tone/register for rewritten speech (default: professional)",
    )
    parser.add_argument(
        "--target-accent",
        dest="target_accent",
        type=str,
        choices=["american", "british", "australian", "indian", "neutral"],
        default=None,
        help="Target output accent (default: american)",
    )
    parser.add_argument(
        "--source-accent",
        dest="source_accent",
        type=str,
        default=None,
        help="Speaker's accent hint (indian, chinese, spanish, ...)",
    )
    parser.add_argument(
        "--target-language",
        dest="target_language",
        type=str,
        default=None,
        help="Output language (english, hindi, spanish, ...). Default: english",
    )
    parser.add_argument(
        "--no-accent-conversion",
        dest="no_accent_conversion",
        action="store_true",
        help="Disable accent/grammar rewriting and relay speech verbatim",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> None:
    # 1. Logging
    log_level = args.log_level or settings.log_level
    setup_logger(log_level=log_level)

    # 2. --list-devices
    if args.list_devices:
        show_devices()
        return

    # 3. Apply CLI overrides
    override_kwargs: dict = {}
    if args.input_device is not None:
        override_kwargs["input_device"] = args.input_device
    if args.virtual_mic_device is not None:
        override_kwargs["virtual_mic_device"] = args.virtual_mic_device
    if args.voice is not None:
        override_kwargs["gemini_voice"] = args.voice
    if args.model is not None:
        override_kwargs["gemini_model"] = args.model
        override_kwargs["gemini_live_model"] = args.model
    if args.log_level is not None:
        override_kwargs["log_level"] = args.log_level
    if args.accent_mode is not None:
        override_kwargs["accent_mode"] = args.accent_mode
    if args.target_accent is not None:
        override_kwargs["target_accent"] = args.target_accent
    if args.source_accent is not None:
        override_kwargs["source_accent"] = args.source_accent
    if args.target_language is not None:
        override_kwargs["target_language"] = args.target_language
    if args.no_accent_conversion:
        override_kwargs["enable_accent_conversion"] = False

    app_settings = settings.model_copy(update=override_kwargs)

    # 4. Validate virtual mic
    target_vmic = app_settings.resolved_virtual_mic_device
    if not validate_virtual_mic_device(target_vmic):
        logger.error(
            f"Cannot start pipeline: Virtual microphone device '{target_vmic}' "
            "is not available."
        )
        sys.exit(1)

    # 5. Banner
    logger.info("Initializing AI Virtual Microphone Pipeline...")
    if args.dry_run:
        logger.info("Mode: DRY RUN (local echo, no API calls)")
    else:
        logger.info(
            f"Model: {app_settings.effective_model}, "
            f"Voice: {app_settings.effective_voice}"
        )
        if app_settings.enable_accent_conversion:
            logger.info(
                f"Accent conversion: {app_settings.source_accent} → "
                f"{app_settings.target_accent} | tone={app_settings.accent_mode} | "
                f"lang={app_settings.target_language}"
            )
        else:
            logger.info("Accent conversion: DISABLED (verbatim relay)")
        if args.system_prompt:
            logger.info(f"System Prompt override: '{args.system_prompt}'")

    logger.info(
        f"Audio Config: rate={app_settings.sample_rate}Hz, "
        f"channels={app_settings.channels}, chunk_size={app_settings.chunk_size}"
    )
    logger.info(
        f"Input: '{app_settings.input_device}' → "
        f"Output: '{app_settings.virtual_mic_device}'"
    )

    # 6. Build pipeline
    pipeline = AIMicPipeline(settings=app_settings, dry_run=args.dry_run)
    if args.system_prompt:
        pipeline.gemini.system_instruction = args.system_prompt

    # 7. Run
    try:
        await pipeline.run_forever()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown requested by user.")
    except Exception as e:
        logger.exception(f"Fatal error in pipeline: {e}")
        sys.exit(1)
    finally:
        await pipeline.stop()
        logger.info("AI Virtual Microphone Pipeline closed cleanly.")


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nProcess terminated by user.")


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()