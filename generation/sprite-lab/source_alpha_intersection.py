"""Preserve authoritative provider transparency across segmentation passes."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops


def intersect_with_source_alpha(
    source: Path,
    output: Path,
    masks: Path,
    rows: int,
    phases: int,
) -> dict[str, int | bool]:
    with Image.open(source) as opened:
        if "A" not in opened.getbands() or opened.getchannel("A").getextrema()[0] == 255:
            return {"applied": False, "applied_cells": 0}
        alpha_sheet = opened.getchannel("A").copy()
    cell_width = alpha_sheet.width // phases
    cell_height = alpha_sheet.height // rows
    applied = 0
    for row in range(rows):
        for column in range(phases):
            name = f"row{row}_col{column}.png"
            image_path = output / name
            mask_path = masks / name
            with Image.open(image_path) as opened_image, Image.open(mask_path) as opened_mask:
                rgba = opened_image.convert("RGBA")
                detected = opened_mask.convert("L")
            source_alpha = alpha_sheet.crop((
                column * cell_width, row * cell_height,
                (column + 1) * cell_width, (row + 1) * cell_height,
            )).resize(detected.size, Image.Resampling.NEAREST)
            final_alpha = ImageChops.darker(detected, source_alpha)
            rgba.putalpha(final_alpha)
            invisible = final_alpha.point(lambda alpha: 255 if alpha == 0 else 0)
            rgba.paste((0, 0, 0, 0), mask=invisible)
            rgba.save(image_path, format="PNG")
            final_alpha.save(mask_path, format="PNG")
            applied += 1
    alpha_sheet.close()
    return {"applied": True, "applied_cells": applied}


__all__ = ["intersect_with_source_alpha"]
