#!/usr/bin/env python3
"""Convert a light checkerboard generation background into real PNG alpha."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def remove_checkerboard(source: Path, destination: Path, threshold: int = 224) -> None:
    image = Image.open(source).convert("RGBA")
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, alpha = pixels[x, y]
            low = min(red, green, blue)
            high = max(red, green, blue)
            if low >= threshold and high - low <= 12:
                # Preserve anti-aliased edges by deriving partial alpha from luminance.
                edge_alpha = max(0, min(alpha, (255 - low) * 8))
                pixels[x, y] = (red, green, blue, edge_alpha)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--threshold", type=int, default=224)
    args = parser.parse_args()
    remove_checkerboard(args.source, args.destination, args.threshold)


if __name__ == "__main__":
    main()

