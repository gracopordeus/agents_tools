#!/usr/bin/env python3
"""Build high-resolution per-direction sprite pages for AI input.

The runtime source is an 8x8 sheet with 256px cells. This POC keeps the five
unique direction rows and lays their eight phases out as a 4x2 grid of 512px
cells inside a 2048x2048 page. The source is enlarged for layout inspection;
it does not invent new visual detail.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


SOURCE_ROWS = ("r1", "r2", "r5", "r6", "r7")
MIRROR_ROWS = {
    "r3": "r1",
    "r4": "r2",
    "r8": "r6",
}
SOURCE_GRID = 8
SOURCE_CELL = 256
PAGE_SIZE = 2048
PAGE_CELL = 512
PHASE_COLUMNS = 4
PHASE_ROWS = 2


def build_page(source: Image.Image, source_row: int, background: tuple[int, int, int]) -> Image.Image:
    page = Image.new("RGB", (PAGE_SIZE, PAGE_SIZE), background)
    for phase in range(8):
        left = phase * SOURCE_CELL
        top = source_row * SOURCE_CELL
        frame = source.crop((left, top, left + SOURCE_CELL, top + SOURCE_CELL))
        frame = frame.resize((PAGE_CELL, PAGE_CELL), Image.Resampling.LANCZOS)
        x = (phase % PHASE_COLUMNS) * PAGE_CELL
        y = (phase // PHASE_COLUMNS) * PAGE_CELL
        page.paste(frame, (x, y))
    return page


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    source = Image.open(args.input).convert("RGB")
    if source.size != (SOURCE_GRID * SOURCE_CELL, SOURCE_GRID * SOURCE_CELL):
        raise ValueError("a entrada deve ter exatamente 2048x2048 px")

    args.out.mkdir(parents=True, exist_ok=True)
    background = source.getpixel((0, 0))
    rows = {}
    for row_index, row_id in enumerate(SOURCE_ROWS):
        page = build_page(source, row_index if row_id in {"r1", "r2"} else {"r5": 4, "r6": 5, "r7": 6}[row_id], background)
        filename = f"ai_{row_id}.png"
        page.save(args.out / filename, format="PNG", optimize=False)
        rows[row_id] = {
            "source_row": row_id,
            "file": filename,
            "phase_layout": "4 columns x 2 rows",
            "phases": [
                {
                    "phase": phase + 1,
                    "source_cell": {
                        "row": row_id,
                        "column": phase + 1,
                    },
                    "page_xy": [
                        (phase % PHASE_COLUMNS) * PAGE_CELL,
                        (phase // PHASE_COLUMNS) * PAGE_CELL,
                    ],
                    "page_size": [PAGE_CELL, PAGE_CELL],
                }
                for phase in range(8)
            ],
        }

    manifest = {
        "contract": "sprite_lab.ai_poc/v1",
        "source": str(args.input),
        "source_size": [source.width, source.height],
        "source_grid": {
            "rows": SOURCE_GRID,
            "columns": SOURCE_GRID,
            "cell_size": [SOURCE_CELL, SOURCE_CELL],
        },
        "output": {
            "page_size": [PAGE_SIZE, PAGE_SIZE],
            "phase_cell_size": [PAGE_CELL, PAGE_CELL],
            "phase_layout": [PHASE_COLUMNS, PHASE_ROWS],
            "resampling": "LANCZOS",
            "background": {
                "mode": "preserved_source_corner",
                "rgb": list(background),
            },
        },
        "unique_directions": list(SOURCE_ROWS),
        "mirrors": MIRROR_ROWS,
        "pages": rows,
        "limitations": [
            "A ampliação 2x reorganiza os pixels, mas não cria detalhe novo.",
            "Para detalhe real em 512px, renderizar cada pose originalmente em 512x512.",
            "A metade inferior de cada página permanece como área de fundo para manter 2048x2048.",
        ],
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(args.out), "pages": list(rows), "page_size": [PAGE_SIZE, PAGE_SIZE]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
