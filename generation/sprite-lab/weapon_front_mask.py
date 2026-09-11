"""Extract deterministic per-cell front-weapon masks from Blender segmentation."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter


WEAPON_COLOR = (255, 255, 255, 255)
BACKGROUND_COLOR = (0, 0, 0, 255)
DEFAULT_COLOR_TOLERANCE = 96
MAX_FOREGROUND_CHROMA = 4


def front_mask_palette() -> dict[str, tuple[int, int, int, int]]:
    """Return the binary production palette used by the integrated pass."""
    return {"weapon": WEAPON_COLOR, "background": BACKGROUND_COLOR}


def front_mask_palette_key(
    component_identifier: str | None,
    selected_weapon_identifier: str,
) -> str:
    """Resolve only the selected weapon to foreground; every other role is background."""
    return (
        "weapon"
        if component_identifier == selected_weapon_identifier
        else "background"
    )


def front_mask_metadata(
    render_metadata: dict[str, Any],
    cell_reports: list[dict[str, Any]],
    dilation: int,
) -> dict[str, Any]:
    """Copy primary-pass alignment and detached per-cell mask reports."""
    return {
        "enabled": True,
        "authority": "combined_semantic_segmentation_with_depth_test",
        "dilation": dilation,
        "directions": list(render_metadata["directions"]),
        "sampled_frames": list(render_metadata["sampled_frames"]),
        "camera": dict(render_metadata["camera"]),
        "cell": list(render_metadata["cell"]),
        "cells": copy.deepcopy(cell_reports),
        "transparent_background": True,
    }


def _inclusive_bbox(mask: Image.Image) -> list[int] | None:
    bbox = mask.getbbox()
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    return [left, top, right - 1, bottom - 1]


def extract_weapon_front_mask(
    segmentation_path: Path,
    output_path: Path,
    *,
    dilation: int = 0,
    color_tolerance: int = DEFAULT_COLOR_TOLERANCE,
) -> dict[str, Any]:
    """Write one RGBA mask; combined-render depth determines visible weapon pixels."""
    if type(dilation) is not int or dilation < 0:
        raise ValueError("dilation deve ser um inteiro maior ou igual a zero")
    if type(color_tolerance) is not int or not 0 <= color_tolerance <= 255:
        raise ValueError("color_tolerance deve ser um inteiro entre 0 e 255")
    with Image.open(segmentation_path) as source:
        rgba = source.convert("RGBA")
        alpha = Image.new("L", rgba.size, 0)
        source_pixels = rgba.load()
        mask_pixels = alpha.load()
        for y in range(rgba.height):
            for x in range(rgba.width):
                red, green, blue, source_alpha = source_pixels[x, y]
                if source_alpha == 0:
                    continue
                if max(
                    abs(red - WEAPON_COLOR[0]),
                    abs(green - WEAPON_COLOR[1]),
                    abs(blue - WEAPON_COLOR[2]),
                ) <= color_tolerance and max(red, green, blue) - min(
                    red, green, blue
                ) <= MAX_FOREGROUND_CHROMA:
                    mask_pixels[x, y] = 255

    front_pixel_count = sum(alpha.histogram()[1:])
    bounding_box = _inclusive_bbox(alpha)
    dilated = (
        alpha.filter(ImageFilter.MaxFilter(dilation * 2 + 1))
        if dilation
        else alpha.copy()
    )
    dilated_area = sum(dilated.histogram()[1:])
    mask = Image.new("RGBA", dilated.size, (255, 255, 255, 0))
    mask.putalpha(dilated)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(output_path, format="PNG")
    return {
        "source": str(segmentation_path),
        "path": str(output_path),
        "size": list(dilated.size),
        "dilation": dilation,
        "area": front_pixel_count,
        "bounding_box": bounding_box,
        "front_pixel_count": front_pixel_count,
        "dilated_area": dilated_area,
        "dilated_bounding_box": _inclusive_bbox(dilated),
    }
