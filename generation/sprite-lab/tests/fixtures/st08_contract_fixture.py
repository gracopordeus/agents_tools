"""Deterministic 8x8 local fixture for the layered holdout contract.

The fixture deliberately uses tiny RGBA cells so contract tests exercise the
real compositor and bundle validator without Blender, a GPU, or a provider
request.  ``create_st08_fixture`` writes fresh PNGs under the requested root;
it never mutates a caller-owned image and returns only local paths/metadata.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


GRID = (8, 8)
CELL_SIZE = (8, 8)
CHARACTER_COLOR = (35, 120, 210, 255)
WEAPON_COLOR = (220, 160, 35, 255)
EMPTY_CELL = (7, 7)


def _character_cell(path: Path, *, empty: bool) -> None:
    image = Image.new("RGBA", CELL_SIZE, (0, 0, 0, 0))
    if not empty:
        ImageDraw.Draw(image).rectangle((1, 1, 6, 6), fill=CHARACTER_COLOR)
    image.save(path, format="PNG")


def _weapon_cell(path: Path, *, empty: bool) -> None:
    image = Image.new("RGBA", CELL_SIZE, (0, 0, 0, 0))
    if not empty:
        ImageDraw.Draw(image).rectangle((1, 2, 6, 5), fill=WEAPON_COLOR)
        # A fractional edge is intentional: the holdout equation must keep
        # antialiasing rather than turning every cut into binary alpha.
        image.putpixel((1, 2), (*WEAPON_COLOR[:3], 128))
    image.save(path, format="PNG")


def _front_mask_cell(path: Path, *, front: bool) -> None:
    image = Image.new("L", CELL_SIZE, 0)
    if front:
        ImageDraw.Draw(image).rectangle((1, 2, 6, 5), fill=255)
        image.putpixel((1, 2), 128)
    image.save(path, format="PNG")


def create_st08_fixture(root: Path | str) -> dict[str, Any]:
    """Write and describe the canonical 64-cell ST08 fixture."""
    destination = Path(root).expanduser().resolve()
    cells_root = destination / "cells"
    cells_root.mkdir(parents=True, exist_ok=True)
    cells: list[dict[str, Any]] = []
    front_cells: list[dict[str, int]] = []
    rear_cells: list[dict[str, int]] = []
    for row in range(GRID[0]):
        for column in range(GRID[1]):
            empty = (row, column) == EMPTY_CELL
            front = not empty and (row + column) % 2 == 0
            character_path = cells_root / f"character_{row:02d}_{column:02d}.png"
            weapon_path = cells_root / f"weapon_{row:02d}_{column:02d}.png"
            front_mask_path = cells_root / f"front_mask_{row:02d}_{column:02d}.png"
            _character_cell(character_path, empty=empty)
            _weapon_cell(weapon_path, empty=empty)
            _front_mask_cell(front_mask_path, front=front)
            coordinate = {"row": row, "column": column}
            if not empty:
                (front_cells if front else rear_cells).append(coordinate)
            cells.append(
                {
                    "row": row,
                    "column": column,
                    "character_path": character_path,
                    "weapon_path": weapon_path,
                    "front_mask_path": front_mask_path,
                }
            )
    return {
        "grid": list(GRID),
        "cell_size": list(CELL_SIZE),
        "cells": cells,
        "front_cells": front_cells,
        "rear_cells": rear_cells,
        "empty_cell": {"row": EMPTY_CELL[0], "column": EMPTY_CELL[1]},
        "edge_alpha": {"row": 0, "column": 0, "x": 1, "y": 2},
    }


__all__ = ["CELL_SIZE", "CHARACTER_COLOR", "GRID", "WEAPON_COLOR", "create_st08_fixture"]
