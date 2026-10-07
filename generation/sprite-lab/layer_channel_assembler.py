"""Assemble structural render cells and describe their shared alignment."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from PIL import Image


GRID_ROWS = 8
GRID_COLUMNS = 8
LAYER_CHANNELS = {
    "character_beauty": "character_beauty_path",
    "character_lineart": "character_lineart_path",
    "weapon_beauty": "weapon_beauty_path",
    "weapon_silhouette": "weapon_silhouette_path",
    "weapon_front_mask": "weapon_front_mask_path",
}
OPTIONAL_LAYER_CHANNELS = {
    "component_visible": "component_visible_path",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assemble_channel(
    output: Path,
    channel: str,
    cells: list[dict[str, Any]],
    *,
    path_field: str,
) -> dict[str, Any]:
    """Assemble exactly one row-major 8x8 channel from detached PNG cells."""
    expected = {(row, column) for row in range(GRID_ROWS) for column in range(GRID_COLUMNS)}
    indexed: dict[tuple[int, int], Path] = {}
    for cell in cells:
        key = (cell.get("row"), cell.get("column"))
        if key in indexed:
            raise ValueError(f"célula duplicada: {key}")
        if key not in expected:
            raise ValueError(f"célula fora da grade 8x8: {key}")
        indexed[key] = Path(str(cell.get(path_field, "")))
    missing = expected - set(indexed)
    if missing:
        raise ValueError(f"célula ausente: {sorted(missing)[0]}")

    cell_size = None
    loaded = {}
    for key in sorted(indexed):
        path = indexed[key]
        if not path.is_file():
            raise ValueError(f"célula ausente: {path}")
        with Image.open(path) as source:
            if source.format != "PNG":
                raise ValueError(f"célula não é PNG: {path}")
            rgba = source.convert("RGBA")
            if cell_size is None:
                cell_size = rgba.size
            elif rgba.size != cell_size:
                raise ValueError(f"dimensão incorreta na célula {key}: {rgba.size}")
            loaded[key] = rgba.copy()
    assert cell_size is not None
    width, height = cell_size
    atlas = Image.new("RGBA", (width * GRID_COLUMNS, height * GRID_ROWS))
    for (row, column), cell in loaded.items():
        atlas.paste(cell, (column * width, row * height))
    destination = output / f"{channel}.png"
    atlas.save(destination, format="PNG")
    return {
        "path": destination.relative_to(output).as_posix(),
        "sha256": _sha256(destination),
        "cell_count": len(loaded),
        "cell_size": [width, height],
        "atlas_size": list(atlas.size),
        "order": "row_major",
    }


def assemble_layer_channels(output: Path, render_metadata: dict[str, Any]) -> dict[str, Any]:
    channels = {
        channel: assemble_channel(
            output, channel, render_metadata["cells"], path_field=path_field
        )
        for channel, path_field in LAYER_CHANNELS.items()
    }
    cells = render_metadata.get("cells", [])
    for channel, path_field in OPTIONAL_LAYER_CHANNELS.items():
        if cells and all(str(cell.get(path_field) or "").strip() for cell in cells):
            channels[channel] = assemble_channel(
                output, channel, cells, path_field=path_field
            )
    return channels


def layer_channels_metadata(
    render_metadata: dict[str, Any],
    *,
    weapon_component_id: str,
    channels: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build one detached, portable provenance block for all structural atlases."""
    return {
        "layer_channels": copy.deepcopy(channels),
        "weapon_component_id": weapon_component_id,
        "front_mask": "weapon_front_mask",
        "visible_component": (
            "component_visible" if "component_visible" in channels else None
        ),
        "directions": list(render_metadata["directions"]),
        "sampled_frames": list(render_metadata["sampled_frames"]),
        "camera": copy.deepcopy(render_metadata["camera"]),
        "grid": [GRID_ROWS, GRID_COLUMNS],
    }
