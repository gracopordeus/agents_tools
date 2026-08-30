#!/usr/bin/env python3
"""Convert source audio to runtime OGG using the system ffmpeg binary."""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def convert(source: Path, destination: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg is required for FLAC/WAV to OGG conversion")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-i", str(source), "-c:a", "libvorbis", str(destination)],
        check=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    convert(args.source, args.destination)
    print(f"PASS: converted {args.source} -> {args.destination}")

