#!/usr/bin/env python3
"""Generate the isolated Pixel Asset Gen proof of concept."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from path_config import PROJECT_ROOT, TOOLS_ROOT


ROOT = PROJECT_ROOT
GENERATOR_ROOT = TOOLS_ROOT / "pixel-asset-gen"
GENERATOR = GENERATOR_ROOT / "generate_assets.py"
DEFAULT_OUTPUT = ROOT / "assets/generated/pixel_asset_gen_poc"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20260820)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output = args.output if args.output.is_absolute() else ROOT / args.output
    command = [
        sys.executable,
        str(GENERATOR),
        "--only",
        "player",
        "--types",
        "player_walk",
        "--scales",
        "4",
        "--seed",
        str(args.seed),
        "--output",
        str(output),
        "--gif",
        "--gif-scale",
        "4",
        "--no-atlas",
        "--clean",
        "--verbose",
    ]

    print("RUN:", " ".join(command))
    subprocess.run(command, cwd=GENERATOR_ROOT, check=True)
    print(f"PASS: Pixel Asset Gen POC generated at {output}")


if __name__ == "__main__":
    main()
