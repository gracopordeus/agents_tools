"""Derive fallback silhouette and segmentation channels from rendered RGBA frames.

This is a bridge for existing Sprite Lab renders that predate the Blender
conditioning exporter. It is intentionally marked as a fallback: it preserves
the character contour but cannot recover semantic body-part roles that were not
rendered into the source image.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import conditioning_image


FALLBACK_SEGMENTATION_COLOR = (231, 76, 60, 255)


def derive(source_dir: Path, output_dir: Path, row: int | None = None) -> int:
    pattern = f"row{row}_col*.png" if row is not None else "row*_col*.png"
    sources = sorted(source_dir.glob(pattern))
    if not sources:
        raise FileNotFoundError(f"nenhum render row{{n}}_col{{n}}.png em {source_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        with Image.open(source) as opened:
            image = opened.convert("RGBA")
        mask = conditioning_image.foreground_mask(image)
        rgba = np.zeros((image.height, image.width, 4), dtype=np.uint8)
        rgba[mask] = (255, 255, 255, 255)
        silhouette = Image.fromarray(rgba, "RGBA")
        segmentation = Image.fromarray(
            np.where(mask[..., None], np.array(FALLBACK_SEGMENTATION_COLOR, dtype=np.uint8), 0),
            "RGBA",
        )
        for channel, result in (
            ("beauty", image),
            ("silhouette", silhouette),
            ("segmentation", segmentation),
        ):
            destination = output_dir / channel / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if channel == "beauty":
                shutil.copy2(source, destination)
            else:
                result.save(destination, format="PNG")
            result.close()
        image.close()
    (output_dir / "DERIVATION_NOTICE.txt").write_text(
        "silhouette e segmentation foram derivados do alpha do render; "
        "não representam papéis semânticos individuais.\n",
        encoding="utf-8",
    )
    return len(sources)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--row", type=int, default=None, help="linha/direção específica; ex.: 0 para r1")
    args = parser.parse_args(argv)
    print(f"frames derivados: {derive(args.source_dir, args.output_dir, args.row)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
