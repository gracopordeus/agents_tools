"""Build one full-grid spritesheet per rendered conditioning channel."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


CHANNELS = {
    "beauty": "",
    "silhouette": "semantic/silhouette",
    "segmentation": "semantic/segmentation",
    "mesh": "mesh",
    "lineart": "lineart",
    "depth": "depth",
    "bones": "bones",
    "heatmap": "heatmap",
}


def build_channel(root: Path, channel: str, rows: int, columns: int, size: int) -> Path:
    directory = root / CHANNELS[channel]
    mode = "RGBA" if channel in {"beauty", "silhouette", "segmentation"} else "L"
    sheet = Image.new(mode, (columns * size, rows * size), 0)
    for row in range(rows):
        for column in range(columns):
            source = directory / f"row{row}_col{column}.png"
            if not source.is_file():
                raise FileNotFoundError(source)
            with Image.open(source) as image:
                if channel == "lineart":
                    frame = image.convert("RGBA").getchannel("A")
                else:
                    frame = image.convert(mode)
            sheet.paste(frame, (column * size, row * size))
    destination = root / f"spritesheet_{channel}.png"
    sheet.save(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--columns", type=int, default=8)
    parser.add_argument("--size", type=int, default=256)
    args = parser.parse_args()
    for channel in CHANNELS:
        print(build_channel(args.root, channel, args.rows, args.columns, args.size))


if __name__ == "__main__":
    main()
