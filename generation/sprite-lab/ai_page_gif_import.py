"""Rebuild eight-direction Sprite Lab GIFs from high-resolution AI pages."""
from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

from PIL import Image

import sprite_render


SOURCE_DIRECTIONS = ("r1", "r2", "r5", "r6", "r7")
MIRRORED_DIRECTIONS = {"r3": "r1", "r4": "r2", "r8": "r6"}


def _clear_magenta(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    pending = deque(
        [(x, 0) for x in range(width)]
        + [(x, height - 1) for x in range(width)]
        + [(0, y) for y in range(height)]
        + [(width - 1, y) for y in range(height)]
    )
    visited: set[tuple[int, int]] = set()
    while pending:
        x, y = pending.popleft()
        if (x, y) in visited:
            continue
        visited.add((x, y))
        red, green, blue, _alpha = pixels[x, y]
        if not (red >= 170 and blue >= 170 and green <= 100):
            continue
        pixels[x, y] = (red, green, blue, 0)
        if x:
            pending.append((x - 1, y))
        if x + 1 < width:
            pending.append((x + 1, y))
        if y:
            pending.append((x, y - 1))
        if y + 1 < height:
            pending.append((x, y + 1))
    return rgba


def import_pages(source: Path, output: Path, fps: float = 10.0) -> dict[str, object]:
    pages: dict[str, Image.Image] = {}
    for direction in SOURCE_DIRECTIONS:
        path = source / f"ai_{direction}.png"
        if not path.is_file():
            raise FileNotFoundError(path)
        page = Image.open(path).convert("RGB")
        if page.size != (2048, 2048):
            raise ValueError(f"{path.name} deve medir 2048x2048")
        pages[direction] = page

    output.mkdir(parents=True, exist_ok=True)
    directions = sprite_render.DIRECTION_ROWS
    for row, direction in enumerate(directions):
        source_direction = MIRRORED_DIRECTIONS.get(direction, direction)
        page = pages[source_direction]
        mirrored = direction in MIRRORED_DIRECTIONS
        for phase in range(8):
            x = (phase % 4) * 512
            y = (phase // 4) * 512
            cell = _clear_magenta(page.crop((x, y, x + 512, y + 512)))
            if mirrored:
                cell = cell.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            cell.save(output / f"row{row}_col{phase}.png", format="PNG")

    for page in pages.values():
        page.close()
    sprite_render._build_sheet(output, 8, 8, 512)
    gifs = sprite_render._build_gifs(output, 8, 8, fps, directions)
    legacy = sprite_render._build_gif(output, 8, fps)
    diagonal, sequence = sprite_render._build_upscaled_diagonal_gif(
        output, 8, 8, fps, upscale=2
    )
    metadata: dict[str, object] = {
        "schema": "sprite_lab.ai_page_gif_import/v1",
        "source": str(source.resolve()),
        "grid": [8, 8],
        "cell_size": [512, 512],
        "fps": fps,
        "loop": True,
        "unique_directions": list(SOURCE_DIRECTIONS),
        "mirrors": MIRRORED_DIRECTIONS,
        "gifs": {key: value.name for key, value in gifs.items()},
        "legacy_gif": legacy.name if legacy else None,
        "diagonal_gif": diagonal.name if diagonal else None,
        "diagonal_sequence": sequence,
    }
    (output / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps", type=float, default=10.0)
    args = parser.parse_args()
    print(json.dumps(import_pages(args.source, args.output, args.fps), indent=2))


if __name__ == "__main__":
    main()
