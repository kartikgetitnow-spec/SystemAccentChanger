#!/usr/bin/env python3
"""Build standalone executable for AI Virtual Microphone using PyInstaller.

Strictly targets main.py and compiles it into:
- Linux:   python-dist/main/main
- Windows: python-dist/main/main.exe
"""
import platform
import subprocess
import sys
from pathlib import Path


def main():
    root_dir = Path(__file__).resolve().parent.parent
    entry_point = root_dir / "main.py"

    if not entry_point.exists():
        entry_point = root_dir / "python" / "main.py"

    is_windows = platform.system() == "Windows"
    exe_name = "main.exe" if is_windows else "main"
    dist_path = root_dir / "python-dist"

    print(f"Building standalone Python executable for {platform.system()}...")
    print(f"Target entry point: {entry_point}")
    print(f"Output directory: {dist_path}")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=main",
        "--onedir",
        "--clean",
        "--noconfirm",
        f"--distpath={dist_path}",
        f"--paths={root_dir}",
        f"--paths={root_dir / 'python'}",
        "--hidden-import=sounddevice",
        "--hidden-import=scipy",
        "--hidden-import=scipy.signal",
        "--hidden-import=numpy",
        "--hidden-import=google.genai",
        "--hidden-import=google.genai.types",
        "--hidden-import=pydantic",
        "--hidden-import=pydantic_settings",
        "--hidden-import=loguru",
        "--hidden-import=dotenv",
        "--hidden-import=src",
        "--hidden-import=src.audio_io",
        "--hidden-import=src.gemini_sts",
        "--hidden-import=src.logger",
        "--hidden-import=src.pipeline",
        "--hidden-import=src.virtual_mic",
        "--hidden-import=config",
        str(entry_point),
    ]

    print("Running PyInstaller...")
    result = subprocess.run(cmd, cwd=str(root_dir))
    if result.returncode == 0:
        built_binary = dist_path / "main" / exe_name
        print(f"\n✅ Python build successful! Output located at: {built_binary}")
    else:
        print(f"\n❌ Python build failed with exit code: {result.returncode}")
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
