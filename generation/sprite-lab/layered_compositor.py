"""Deterministic character holdout and local layered composition.

This module operates only on already-approved image layers.  It does not
render a sprite or call a provider.  The structural front mask is the
authority for *where* to cut and the weapon alpha is the authority for *how
much* to cut at anti-aliased edges.
"""
from __future__ import annotations

from collections.abc import Sequence
import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


DEFAULT_GRID = (8, 8)
OUTPUT_NAMES = {
    "character_holdout_spritesheet": "character_holdout_spritesheet.png",
    "weapon_spritesheet": "weapon_spritesheet.png",
    "holdout_cut_mask": "holdout_cut_mask.png",
    "composite_preview": "composite_preview.png",
}


def _as_array(value: Any, name: str) -> np.ndarray:
    """Convert an image/array-like input without mutating the caller's data."""
    if isinstance(value, Image.Image):
        return np.asarray(value)
    try:
        return np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} deve ser uma imagem ou array numérico") from exc


def _channel_array(value: Any, name: str, *, mask: bool = False) -> np.ndarray:
    """Extract a grayscale alpha/mask channel from 2-D or RGBA input."""
    array = _as_array(value, name)
    if array.ndim == 2:
        channel = array
    elif array.ndim == 3 and array.shape[-1] == 4:
        channel = array[..., 3]
    elif mask and array.ndim == 3 and array.shape[-1] == 3:
        # RGB masks are accepted for interoperability with structural exports
        # that use white as foreground and black as background.
        channel = np.max(array, axis=-1)
    else:
        expected = "2-D, RGB ou RGBA" if mask else "2-D ou RGBA"
        raise ValueError(f"{name} deve ser {expected}")
    if channel.ndim != 2:
        raise ValueError(f"{name} deve resultar em um canal 2-D")
    if not np.issubdtype(channel.dtype, np.number) and channel.dtype != np.bool_:
        raise ValueError(f"{name} deve ser numérico")
    return channel


def _unit_interval(channel: np.ndarray, name: str) -> np.ndarray:
    """Normalize uint8-style or unit-range alpha to finite float [0, 1]."""
    values = np.asarray(channel)
    if values.dtype == np.bool_:
        return values.astype(np.float64)
    values = values.astype(np.float64, copy=False)
    values = np.nan_to_num(values, nan=0.0, posinf=255.0, neginf=0.0)
    # Float inputs from image arrays are commonly either [0, 1] or [0, 255].
    # Values outside [0, 1] therefore use the uint8 convention.
    if np.issubdtype(channel.dtype, np.integer) or np.max(values, initial=0.0) > 1.0:
        values = values / 255.0
    return np.clip(values, 0.0, 1.0)


def _grid_shape(shape: tuple[int, int], grid: Sequence[int]) -> tuple[int, int, int, int]:
    if isinstance(grid, (str, bytes)) or len(grid) != 2:
        raise ValueError("grade deve possuir linhas e colunas")
    rows, columns = grid
    if type(rows) is not int or type(columns) is not int or rows <= 0 or columns <= 0:
        raise ValueError("grade deve possuir linhas e colunas inteiras positivas")
    height, width = shape
    if height % rows or width % columns:
        raise ValueError("dimensões devem ser divisíveis pela grade")
    return rows, columns, height // rows, width // columns


def _validate_dilation(dilation: int) -> int:
    if type(dilation) is not int or dilation < 0:
        raise ValueError("dilation deve ser um inteiro maior ou igual a zero")
    return dilation


def _dilate_cell(mask: np.ndarray, radius: int) -> np.ndarray:
    """Dilate one cell with a square footprint, never reading a neighbour."""
    if radius == 0:
        return mask.copy()
    height, width = mask.shape
    # A radius larger than the cell has the same result as a radius covering
    # the whole cell.  Capping it also keeps malformed metadata inexpensive.
    radius = min(radius, max(height, width))
    result = np.zeros_like(mask)
    for offset_y in range(-radius, radius + 1):
        source_top = max(0, -offset_y)
        source_bottom = min(height, height - offset_y)
        target_top = max(0, offset_y)
        target_bottom = min(height, height + offset_y)
        for offset_x in range(-radius, radius + 1):
            source_left = max(0, -offset_x)
            source_right = min(width, width - offset_x)
            target_left = max(0, offset_x)
            target_right = min(width, width + offset_x)
            result[target_top:target_bottom, target_left:target_right] = np.maximum(
                result[target_top:target_bottom, target_left:target_right],
                mask[source_top:source_bottom, source_left:source_right],
            )
    return result


def _cell_limited_mask(
    front_mask: Any,
    shape: tuple[int, int],
    grid: Sequence[int],
    dilation: int,
) -> tuple[np.ndarray, int, int]:
    mask_channel = _channel_array(front_mask, "front_mask", mask=True)
    if mask_channel.shape != shape:
        raise ValueError("character_alpha, weapon_alpha e front_mask devem ter a mesma dimensão")
    normalized = _unit_interval(mask_channel, "front_mask")
    rows, columns, cell_height, cell_width = _grid_shape(shape, grid)
    limited = np.zeros(shape, dtype=np.float64)
    before = int(np.count_nonzero(normalized > 0.0))
    for row in range(rows):
        top = row * cell_height
        bottom = top + cell_height
        for column in range(columns):
            left = column * cell_width
            right = left + cell_width
            limited[top:bottom, left:right] = _dilate_cell(
                normalized[top:bottom, left:right], dilation
            )
    after = int(np.count_nonzero(limited > 0.0))
    return limited, before, after


def _calculate_holdout(
    character_alpha: Any,
    weapon_alpha: Any,
    front_mask: Any,
    *,
    grid: Sequence[int],
    dilation: int,
) -> tuple[np.ndarray, dict[str, Any]]:
    character_channel = _channel_array(character_alpha, "character_alpha")
    weapon_channel = _channel_array(weapon_alpha, "weapon_alpha")
    if character_channel.shape != weapon_channel.shape:
        raise ValueError("character_alpha, weapon_alpha e front_mask devem ter a mesma dimensão")
    shape = tuple(int(part) for part in character_channel.shape)
    _grid_shape(shape, grid)
    dilation = _validate_dilation(dilation)
    mask, before, after = _cell_limited_mask(front_mask, shape, grid, dilation)
    character = _unit_interval(character_channel, "character_alpha")
    weapon = _unit_interval(weapon_channel, "weapon_alpha")
    # Keep the arithmetic in float until the final conversion.  This avoids
    # uint8 underflow and preserves the fractional alpha at silhouette edges.
    result = np.clip(np.rint(character * (1.0 - weapon * mask) * 255.0), 0.0, 255.0)
    report = {
        "grid": [int(grid[0]), int(grid[1])],
        "size": [shape[1], shape[0]],
        "dilation": dilation,
        "dilation_applied": bool(dilation),
        "front_pixel_count": before,
        "dilated_front_pixel_count": after,
        "mask_limited_to_cells": True,
    }
    return result.astype(np.uint8), report


def calculate_holdout_alpha(
    character_alpha: Any,
    weapon_alpha: Any,
    front_mask: Any,
    *,
    grid: Sequence[int] = DEFAULT_GRID,
    dilation: int = 0,
) -> np.ndarray:
    """Return the character alpha after applying the structural front mask.

    The equation is ``character × (1 - weapon × front_mask)``.  All three
    channels may be uint8 arrays or float arrays in either the [0, 1] or
    [0, 255] convention; output is always clipped uint8.
    """
    result, _report = _calculate_holdout(
        character_alpha,
        weapon_alpha,
        front_mask,
        grid=grid,
        dilation=dilation,
    )
    return result


def calculate_holdout_mask(
    weapon_alpha: Any,
    front_mask: Any,
    *,
    grid: Sequence[int] = DEFAULT_GRID,
    dilation: int = 0,
) -> np.ndarray:
    """Return the effective cut mask ``alpha_weapon × front_mask`` as uint8."""
    weapon_channel = _channel_array(weapon_alpha, "weapon_alpha")
    shape = tuple(int(part) for part in weapon_channel.shape)
    _grid_shape(shape, grid)
    dilation = _validate_dilation(dilation)
    mask, _before, _after = _cell_limited_mask(front_mask, shape, grid, dilation)
    result = np.clip(
        np.rint(_unit_interval(weapon_channel, "weapon_alpha") * mask * 255.0),
        0.0,
        255.0,
    )
    return result.astype(np.uint8)


def clean_invisible_rgb(
    rgba: Any,
    *,
    return_count: bool = False,
) -> np.ndarray | tuple[np.ndarray, int]:
    """Zero RGB only where alpha is fully transparent, preventing halos."""
    array = _as_array(rgba, "rgba")
    if array.ndim != 3 or array.shape[-1] != 4:
        raise ValueError("rgba deve possuir quatro canais")
    if not np.issubdtype(array.dtype, np.number) and array.dtype != np.bool_:
        raise ValueError("rgba deve ser numérico")
    output = np.array(array, copy=True)
    invisible = output[..., 3] == 0
    changed = invisible & np.any(output[..., :3] != 0, axis=-1)
    output[invisible, :3] = 0
    count = int(np.count_nonzero(changed))
    if return_count:
        return output, count
    return output


def apply_character_holdout(
    character_rgba: Any,
    weapon_rgba: Any,
    front_mask: Any,
    *,
    grid: Sequence[int] = DEFAULT_GRID,
    dilation: int = 0,
) -> tuple[Image.Image, dict[str, Any]]:
    """Return an RGBA character layer with front-weapon pixels held out.

    ``weapon_rgba`` is read only for its alpha channel; the complete weapon
    layer is never cropped or modified by this function.  Returning a report
    alongside the image makes the optional dilation and transparency cleanup
    auditable without coupling the calculation to spritesheet preview code.
    """
    character_array = _as_array(character_rgba, "character_rgba")
    weapon_array = _as_array(weapon_rgba, "weapon_rgba")
    if character_array.ndim != 3 or character_array.shape[-1] != 4:
        raise ValueError("character_rgba deve possuir quatro canais")
    if weapon_array.ndim != 3 or weapon_array.shape[-1] != 4:
        raise ValueError("weapon_rgba deve possuir quatro canais")
    if character_array.shape != weapon_array.shape:
        raise ValueError("character_rgba e weapon_rgba devem ter a mesma dimensão")
    holdout_alpha, report = _calculate_holdout(
        character_array[..., 3],
        weapon_array[..., 3],
        front_mask,
        grid=grid,
        dilation=dilation,
    )
    output = np.array(character_array, copy=True)
    output[..., 3] = holdout_alpha
    output, cleaned = clean_invisible_rgb(output, return_count=True)
    report["transparent_rgb_cleaned"] = cleaned
    report["weapon_preserved"] = True
    return Image.fromarray(output.astype(np.uint8), mode="RGBA"), report


def _composition_grid(grid: Sequence[int]) -> tuple[int, int]:
    if isinstance(grid, (str, bytes)) or len(grid) != 2:
        raise ValueError("grade deve possuir linhas e colunas")
    rows, columns = grid
    if type(rows) is not int or type(columns) is not int or rows <= 0 or columns <= 0:
        raise ValueError("grade deve possuir linhas e colunas inteiras positivas")
    return rows, columns


def _cell_path(
    cell: dict[str, Any],
    requested: str,
    aliases: Sequence[str],
    row: int,
    column: int,
) -> Path:
    for field in (requested, *aliases):
        value = cell.get(field)
        if value is not None and str(value).strip():
            return Path(str(value))
    raise ValueError(f"célula ausente ({row}, {column}): {requested}")


def _load_rgba_png(path: Path, field: str) -> Image.Image:
    if not path.is_file():
        raise ValueError(f"célula ausente: {path}")
    try:
        with Image.open(path) as opened:
            if opened.format != "PNG":
                raise ValueError(f"{field} deve ser PNG: {path}")
            return opened.convert("RGBA").copy()
    except OSError as exc:
        raise ValueError(f"{field} não é um PNG válido: {path}: {exc}") from None


def _load_mask_png(path: Path) -> Image.Image:
    """Load masks without turning grayscale black into opaque alpha."""
    if not path.is_file():
        raise ValueError(f"célula ausente: {path}")
    try:
        with Image.open(path) as opened:
            if opened.format != "PNG":
                raise ValueError(f"front_mask deve ser PNG: {path}")
            if "A" in opened.getbands():
                return opened.convert("RGBA").copy()
            return opened.convert("L").copy()
    except OSError as exc:
        raise ValueError(f"front_mask não é um PNG válido: {path}: {exc}") from None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compose_layered_spritesheets(
    cells: Sequence[dict[str, Any]] | dict[str, Any],
    output_dir: Path | str,
    *,
    grid: Sequence[int] = DEFAULT_GRID,
    character_field: str = "character_path",
    weapon_field: str = "weapon_path",
    front_mask_field: str = "front_mask_path",
    dilation: int = 0,
) -> dict[str, Any]:
    """Compose detached character/weapon cells into layered PNG outputs.

    Cells are indexed by their explicit ``row``/``column`` metadata, so input
    ordering cannot affect the result.  The default field aliases also accept
    the names emitted by ``blender_sprite_render`` (``*_beauty_path`` and
    ``weapon_front_mask_path``).  Every cell is held out independently with a
    one-cell grid before it is pasted into the final 8×8 atlases.
    """
    rows, columns = _composition_grid(grid)
    dilation = _validate_dilation(dilation)
    expected = {(row, column) for row in range(rows) for column in range(columns)}
    raw_cells = cells.get("cells") if isinstance(cells, dict) else cells
    if not isinstance(raw_cells, Sequence) or isinstance(raw_cells, (str, bytes)):
        raise ValueError("cells deve ser uma sequência de 64 células")

    indexed: dict[tuple[int, int], dict[str, Any]] = {}
    for cell in raw_cells:
        if not isinstance(cell, dict):
            raise ValueError("cada célula deve ser um objeto")
        row, column = cell.get("row"), cell.get("column")
        if type(row) is not int or type(column) is not int or (row, column) not in expected:
            raise ValueError(f"célula fora da grade {rows}x{columns}: {(row, column)}")
        if (row, column) in indexed:
            raise ValueError(f"célula duplicada: {(row, column)}")
        indexed[(row, column)] = cell
    missing = expected - set(indexed)
    if missing:
        raise ValueError(f"célula ausente: {sorted(missing)[0]}")

    prepared: list[tuple[int, int, Image.Image, Image.Image, Image.Image, Image.Image, dict[str, Any]]] = []
    cell_size: tuple[int, int] | None = None
    for (row, column), cell in sorted(indexed.items()):
        character_path = _cell_path(
            cell,
            character_field,
            ("character_beauty_path", "character", "character_path"),
            row,
            column,
        )
        weapon_path = _cell_path(
            cell,
            weapon_field,
            ("weapon_beauty_path", "weapon", "weapon_path"),
            row,
            column,
        )
        mask_path = _cell_path(
            cell,
            front_mask_field,
            ("weapon_front_mask_path", "front_mask", "front_mask_path"),
            row,
            column,
        )
        character = _load_rgba_png(character_path, "character")
        weapon = _load_rgba_png(weapon_path, "weapon")
        mask = _load_mask_png(mask_path)
        try:
            if character.size != weapon.size or character.size != mask.size:
                raise ValueError(
                    f"dimensão incorreta na célula {(row, column)}: "
                    f"character={character.size}, weapon={weapon.size}, mask={mask.size}"
                )
            if cell_size is None:
                cell_size = character.size
            elif character.size != cell_size:
                raise ValueError(
                    f"dimensão incorreta na célula {(row, column)}: {character.size}; "
                    f"esperado {cell_size}"
                )
            character_holdout, cell_report = apply_character_holdout(
                character,
                weapon,
                mask,
                grid=(1, 1),
                dilation=dilation,
            )
            holdout_mask = Image.fromarray(
                np.dstack(
                    (
                        np.full((mask.height, mask.width), 255, dtype=np.uint8),
                        np.full((mask.height, mask.width), 255, dtype=np.uint8),
                        np.full((mask.height, mask.width), 255, dtype=np.uint8),
                        calculate_holdout_mask(
                            weapon.getchannel("A"),
                            mask,
                            grid=(1, 1),
                            dilation=dilation,
                        ),
                    )
                ),
                mode="RGBA",
            )
            preview = Image.alpha_composite(weapon, character_holdout)
            cell_report = {
                "row": row,
                "column": column,
                **cell_report,
                "mask_size": [mask.width, mask.height],
            }
            prepared.append((row, column, character_holdout, weapon, holdout_mask, preview, cell_report))
        except Exception:
            character.close()
            weapon.close()
            mask.close()
            raise
        character.close()
        mask.close()

    assert cell_size is not None
    width, height = cell_size
    sheet_size = (width * columns, height * rows)
    character_sheet = Image.new("RGBA", sheet_size, (0, 0, 0, 0))
    weapon_sheet = Image.new("RGBA", sheet_size, (0, 0, 0, 0))
    mask_sheet = Image.new("RGBA", sheet_size, (0, 0, 0, 0))
    preview_sheet = Image.new("RGBA", sheet_size, (0, 0, 0, 0))
    for row, column, character, weapon, mask, preview, _report in prepared:
        position = (column * width, row * height)
        character_sheet.paste(character, position)
        weapon_sheet.paste(weapon, position)
        mask_sheet.paste(mask, position)
        preview_sheet.paste(preview, position)
        character.close()
        weapon.close()
        mask.close()
        preview.close()

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {
        name: destination / filename for name, filename in OUTPUT_NAMES.items()
    }
    for name, image in (
        ("character_holdout_spritesheet", character_sheet),
        ("weapon_spritesheet", weapon_sheet),
        ("holdout_cut_mask", mask_sheet),
        ("composite_preview", preview_sheet),
    ):
        image.save(paths[name], format="PNG")
        image.close()

    output_metadata = {
        name: {
            "path": str(path),
            "size": [sheet_size[0], sheet_size[1]],
            "mode": "RGBA",
            "sha256": _sha256(path),
        }
        for name, path in paths.items()
    }
    reports = [report for *_images, report in prepared]
    return {
        "grid": [rows, columns],
        "cell_count": len(prepared),
        "cell_size": [width, height],
        "dilation": dilation,
        "outputs": {name: str(path) for name, path in paths.items()},
        "output_metadata": output_metadata,
        "reports": reports,
        **{name: str(path) for name, path in paths.items()},
    }


# Keep descriptive aliases available to callers that use the shorter API
# vocabulary while retaining one implementation and one rounding policy.
compute_holdout_alpha = calculate_holdout_alpha
apply_holdout = apply_character_holdout
compose_spritesheets = compose_layered_spritesheets
compose_layered_outputs = compose_layered_spritesheets


__all__ = [
    "DEFAULT_GRID",
    "apply_character_holdout",
    "apply_holdout",
    "calculate_holdout_alpha",
    "calculate_holdout_mask",
    "clean_invisible_rgb",
    "compose_layered_outputs",
    "compose_layered_spritesheets",
    "compose_spritesheets",
    "compute_holdout_alpha",
]
