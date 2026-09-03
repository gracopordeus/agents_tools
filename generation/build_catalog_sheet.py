#!/usr/bin/env python3
"""Compose raw Blender catalog cells into sheets, frames and previews."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent / "sprite-lab"))
from direction_contract import DIRECTION_ROWS, direction_contract_for

DEFAULT_ROWS = list(DIRECTION_ROWS)
CELL = 128
FILL_FRAC = 0.82


def detect_grid(cells: Path) -> tuple[int, int]:
    rows, cols = set(), set()
    for path in cells.glob("row*_col*.png"):
        try:
            row_text, col_text = path.stem.split("_col")
            rows.add(int(row_text.removeprefix("row")))
            cols.add(int(col_text))
        except (ValueError, IndexError):
            continue
    if not rows or not cols:
        raise FileNotFoundError(f"no catalog cells found in {cells}")
    return max(rows) + 1, max(cols) + 1


def normalize(frame: Image.Image, cell: int = CELL, fill_frac: float = FILL_FRAC) -> tuple[Image.Image, list[int]]:
    frame = frame.convert("RGBA")
    alpha = frame.getchannel("A")
    bbox = alpha.point(lambda value: 255 if value > 10 else 0).getbbox()
    if bbox is None:
        return Image.new("RGBA", (cell, cell), (0, 0, 0, 0)), [0, 0, 0, 0]
    crop = frame.crop(bbox)
    scale = fill_frac * cell / max(crop.height, 1)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    crop = crop.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    x = (cell - crop.width) // 2
    y = cell - crop.height
    canvas.paste(crop, (x, y), crop)
    return canvas, [x, y, x + crop.width - 1, y + crop.height - 1]


def compose_job(cells: Path, output: Path, fps: int = 10) -> dict:
    """Compose one rendered job and return its sheet metadata."""
    cells = Path(cells)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows, columns = detect_grid(cells)
    metadata_path = cells / "render_metadata.json"
    render_metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    directions = render_metadata.get("directions", DEFAULT_ROWS[:rows])
    directions = directions[:rows]
    try:
        direction_contract = direction_contract_for(directions)
    except ValueError:
        # Preserve historical catalogs with the old short labels, but make the
        # missing contract visible instead of silently claiming canonical rows.
        direction_contract = None
    frames: dict[str, list[Image.Image]] = {}
    boxes: dict[str, list[list[int]]] = {}
    for row, direction in enumerate(directions):
        frames[direction] = []
        boxes[direction] = []
        for column in range(columns):
            path = cells / f"row{row}_col{column}.png"
            if not path.is_file():
                raise FileNotFoundError(path)
            normalized, bbox = normalize(Image.open(path))
            frames[direction].append(normalized)
            boxes[direction].append(bbox)

    sheet = Image.new("RGBA", (columns * CELL, rows * CELL), (0, 0, 0, 0))
    for row, direction in enumerate(directions):
        for column, frame in enumerate(frames[direction]):
            sheet.paste(frame, (column * CELL, row * CELL), frame)
    sheet_path = output / f"sheet_{rows}x{columns}.png"
    sheet.save(sheet_path)

    frames_dir = output / "frames"
    preview_dir = output / "preview"
    frames_dir.mkdir(exist_ok=True)
    preview_dir.mkdir(exist_ok=True)
    for direction in directions:
        gif_frames = []
        for index, frame in enumerate(frames[direction]):
            frame_path = frames_dir / f"{direction}_{index:02d}.png"
            frame.save(frame_path)
            preview = Image.new("RGB", (CELL, CELL), (0, 0, 0))
            preview.paste(frame, (0, 0), frame)
            gif_frames.append(preview)
        gif_frames[0].save(
            preview_dir / f"{direction}.gif",
            format="GIF",
            save_all=True,
            append_images=gif_frames[1:],
            duration=max(1, 1000 // max(fps, 1)),
            loop=0,
            disposal=2,
        )

    result = {
        "schema": "mixamo_sheet/v1",
        "source": render_metadata,
        "directions": directions,
        "direction_contract": direction_contract,
        "rows": rows,
        "columns": columns,
        "cell": [CELL, CELL],
        "fps": fps,
        "loop": True,
        "sheet": sheet_path.name,
        "frames": {direction: [str(Path("frames") / f"{direction}_{i:02d}.png") for i in range(columns)]
                   for direction in directions},
        "boxes": boxes,
        "transparent_background": True,
    }
    (output / "sheet_metadata.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"SHEET_OK {sheet_path}")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compose one Mixamo catalog item")
    parser.add_argument("--cells", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--fps", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    compose_job(args.cells, args.out, fps=args.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
