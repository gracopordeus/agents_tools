"""Run the local SwinIR 2x pass used by the ChatGPT bridge POC."""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from upscale_backend import Upscaler


def upscale(source: Path, output: Path, profile: str, device: str, precision: str) -> None:
    with Image.open(source) as opened:
        rgb = np.asarray(opened.convert("RGB"), dtype=np.uint8)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    upscaler = Upscaler(profile, tile_size=256, tile_pad=32, device=device, precision=precision)
    output_bgr, _ = upscaler.enhance(bgr, outscale=2.0)
    output_rgb = cv2.cvtColor(output_bgr, cv2.COLOR_BGR2RGB)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(output_rgb, mode="RGB").save(output, format="PNG")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--profile", default="swinir_m_classical_df2k_x2")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--precision", choices=("fp32", "fp16"), default="fp32")
    args = parser.parse_args()
    upscale(args.source, args.output, args.profile, args.device, args.precision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
