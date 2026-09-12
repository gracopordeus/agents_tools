"""Local quality gates for layered holdout cells and spritesheets."""
from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


SCHEMA = "sprite_lab.holdout_validation/v1"
COMPOSITION_SCHEMA = "sprite_lab.layered_composition_validation/v2"
DEFAULT_GRID = (8, 8)
DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "alpha_threshold": 16,
    # Two pixels keeps the gate meaningful for the small synthetic cells used
    # by the POC while remaining configurable for production cell sizes.
    "border_margin_px": 2,
    "max_border_pixels": 0,
    "min_iou": 0.55,
    "warning_iou": 0.80,
    "max_center_error_px": 8.0,
    "warning_center_error_px": 4.0,
    "max_attachment_error_px": 8.0,
    "warning_attachment_error_px": 4.0,
    "max_outside_structural_pixels": 0,
    "max_invisible_rgb_pixels": 0,
}


def _validated_grid(grid: Sequence[int]) -> tuple[int, int]:
    if isinstance(grid, (str, bytes)) or len(grid) != 2:
        raise ValueError("grade deve possuir linhas e colunas")
    rows, columns = grid
    if type(rows) is not int or type(columns) is not int or rows <= 0 or columns <= 0:
        raise ValueError("grade deve possuir linhas e colunas inteiras positivas")
    return rows, columns


def _thresholds(overrides: dict[str, Any] | None) -> dict[str, float | int]:
    values: dict[str, float | int] = dict(DEFAULT_THRESHOLDS)
    if overrides is not None:
        if not isinstance(overrides, dict):
            raise ValueError("thresholds deve ser um objeto")
        unknown = sorted(set(overrides) - set(values))
        if unknown:
            raise ValueError(f"threshold desconhecido: {unknown[0]}")
        values.update(overrides)
    integer_keys = {
        "alpha_threshold",
        "border_margin_px",
        "max_border_pixels",
        "max_outside_structural_pixels",
        "max_invisible_rgb_pixels",
    }
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"threshold inválido: {key}")
        if not math.isfinite(float(value)):
            raise ValueError(f"threshold deve ser finito: {key}")
        if key in integer_keys:
            if type(value) is not int or value < 0:
                raise ValueError(f"threshold deve ser inteiro não negativo: {key}")
        elif float(value) < 0:
            raise ValueError(f"threshold deve ser não negativo: {key}")
    if int(values["alpha_threshold"]) > 255:
        raise ValueError("alpha_threshold deve estar entre 0 e 255")
    for key in ("min_iou", "warning_iou"):
        if float(values[key]) > 1:
            raise ValueError(f"threshold deve estar entre 0 e 1: {key}")
    if float(values["warning_iou"]) < float(values["min_iou"]):
        raise ValueError("warning_iou não pode ser menor que min_iou")
    if float(values["warning_center_error_px"]) > float(values["max_center_error_px"]):
        raise ValueError("warning_center_error_px não pode exceder max_center_error_px")
    if float(values["warning_attachment_error_px"]) > float(values["max_attachment_error_px"]):
        raise ValueError(
            "warning_attachment_error_px não pode exceder max_attachment_error_px"
        )
    return values


def _bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def _center(bbox: list[int] | None) -> tuple[float, float] | None:
    if bbox is None:
        return None
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _iou(first: np.ndarray, second: np.ndarray) -> float:
    union = np.logical_or(first, second).sum()
    if not union:
        return 1.0
    return float(np.logical_and(first, second).sum() / union)


def _load(path: Path, label: str, alpha_threshold: int) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"arquivo ausente: {label}: {path}")
    try:
        with Image.open(path) as opened:
            image_format = opened.format
            original_mode = opened.mode
            bands = opened.getbands()
            rgba = np.asarray(opened.convert("RGBA"), dtype=np.uint8).copy()
            if "A" in bands:
                alpha = rgba[..., 3]
            else:
                alpha = np.asarray(opened.convert("L"), dtype=np.uint8)
    except OSError as exc:
        raise ValueError(f"arquivo inválido: {label}: {path}: {exc}") from None
    return {
        "path": path,
        "format": image_format,
        "mode": original_mode,
        "rgba": rgba,
        "alpha": alpha,
        "mask": alpha > alpha_threshold,
        "size": (int(rgba.shape[1]), int(rgba.shape[0])),
    }


def _resolve_path(cell: dict[str, Any], requested: str, aliases: Sequence[str]) -> Path | None:
    for field in (requested, *aliases):
        value = cell.get(field)
        if value is not None and str(value).strip():
            return Path(str(value))
    return None


def _attachment(cell: dict[str, Any], fallback: Sequence[float] | None) -> tuple[float, float] | None:
    value: Any = None
    for field in ("attachment", "attachment_point", "expected_attachment"):
        if cell.get(field) is not None:
            value = cell[field]
            break
    if value is None:
        value = fallback
    if value is None:
        return None
    if isinstance(value, dict):
        value = (value.get("x"), value.get("y"))
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) != 2
        or isinstance(value[0], bool)
        or isinstance(value[1], bool)
    ):
        raise ValueError("attachment deve conter coordenadas x,y")
    try:
        coordinates = (float(value[0]), float(value[1]))
    except (TypeError, ValueError) as exc:
        raise ValueError("attachment deve conter coordenadas numéricas") from exc
    if not all(math.isfinite(coordinate) for coordinate in coordinates):
        raise ValueError("attachment deve conter coordenadas finitas")
    return coordinates


def _border_metrics(mask: np.ndarray, margin: int) -> tuple[int, list[str]]:
    height, width = mask.shape
    margin = min(margin, height, width)
    border = np.zeros_like(mask, dtype=bool)
    if margin:
        border[:margin, :] = True
        border[-margin:, :] = True
        border[:, :margin] = True
        border[:, -margin:] = True
    edges: list[str] = []
    if mask[:margin, :].any() if margin else False:
        edges.append("top")
    if mask[-margin:, :].any() if margin else False:
        edges.append("bottom")
    if mask[:, :margin].any() if margin else False:
        edges.append("left")
    if mask[:, -margin:].any() if margin else False:
        edges.append("right")
    return int(np.count_nonzero(mask & border)), edges


def _outside_bbox(mask: np.ndarray, bbox: list[int] | None) -> int:
    if not mask.any():
        return 0
    if bbox is None:
        return int(np.count_nonzero(mask))
    inside = np.zeros_like(mask, dtype=bool)
    inside[bbox[1] : bbox[3] + 1, bbox[0] : bbox[2] + 1] = True
    return int(np.count_nonzero(mask & ~inside))


def _diagnostic(
    row: int,
    column: int,
    code: str,
    severity: str,
    message: str,
    value: Any = None,
    threshold: Any = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "row": row,
        "column": column,
        "code": code,
        "severity": severity,
        "message": message,
    }
    if value is not None:
        item["value"] = value
    if threshold is not None:
        item["threshold"] = threshold
    return item


def _cell_metrics(
    cell: dict[str, Any],
    *,
    row: int,
    column: int,
    values: dict[str, float | int],
    fallback_attachment: Sequence[float] | None,
) -> dict[str, Any]:
    alpha_threshold = int(values["alpha_threshold"])
    structural_path = _resolve_path(
        cell,
        "structural_path",
        ("weapon_silhouette_path", "weapon_structural_path", "reference_path"),
    )
    generated_path = _resolve_path(
        cell,
        "generated_path",
        ("weapon_beauty_path", "weapon_path", "generated_weapon_path"),
    )
    character_path = _resolve_path(
        cell,
        "character_holdout_path",
        ("character_path", "character_beauty_path"),
    )
    metrics: dict[str, Any] = {
        "row": row,
        "column": column,
        "valid": True,
        "structural": structural_path.name if structural_path else None,
        "generated": generated_path.name if generated_path else None,
        "diagnostics": [],
    }
    if structural_path is None:
        metrics["valid"] = False
        metrics["diagnostics"].append(
            _diagnostic(row, column, "structural_missing", "error", "máscara estrutural ausente")
        )
    if generated_path is None:
        metrics["valid"] = False
        metrics["diagnostics"].append(
            _diagnostic(row, column, "generated_missing", "error", "camada gerada ausente")
        )
    if structural_path is None or generated_path is None:
        return metrics

    try:
        structural = _load(structural_path, "structural", alpha_threshold)
        generated = _load(generated_path, "generated", alpha_threshold)
    except ValueError as exc:
        metrics["valid"] = False
        metrics["diagnostics"].append(
            _diagnostic(row, column, "image_invalid", "error", str(exc))
        )
        return metrics

    metrics.update({
        "size": list(generated["size"]),
        "structural_mode": structural["mode"],
        "generated_mode": generated["mode"],
        "structural_format": structural["format"],
        "generated_format": generated["format"],
        "structural_bbox": _bbox(structural["mask"]),
        "generated_bbox": _bbox(generated["mask"]),
    })
    if structural["format"] != "PNG" or generated["format"] != "PNG":
        metrics["valid"] = False
        metrics["diagnostics"].append(
            _diagnostic(row, column, "image_format", "error", "camadas devem ser PNG")
        )
    if generated["mode"] != "RGBA":
        metrics["valid"] = False
        metrics["diagnostics"].append(
            _diagnostic(row, column, "generated_alpha_channel", "error", "camada gerada deve ser RGBA")
        )
    if structural["size"] != generated["size"]:
        metrics["valid"] = False
        metrics["diagnostics"].append(
            _diagnostic(
                row,
                column,
                "dimension_mismatch",
                "error",
                "máscara estrutural e camada gerada possuem dimensões diferentes",
            )
        )
        return metrics

    structural_bbox = metrics["structural_bbox"]
    generated_bbox = metrics["generated_bbox"]
    iou = _iou(structural["mask"], generated["mask"])
    metrics["silhouette_iou"] = round(iou, 6)
    structural_center = _center(structural_bbox)
    generated_center = _center(generated_bbox)
    center_error = None
    if structural_center is not None and generated_center is not None:
        center_error = float(np.linalg.norm(np.subtract(generated_center, structural_center)))
    metrics["structural_center"] = list(structural_center) if structural_center else None
    metrics["generated_center"] = list(generated_center) if generated_center else None
    metrics["center_error_px"] = round(center_error, 6) if center_error is not None else None
    expected_attachment = _attachment(cell, fallback_attachment)
    attachment_error = None
    if expected_attachment is not None and generated_center is not None:
        attachment_error = float(np.linalg.norm(np.subtract(generated_center, expected_attachment)))
    metrics["attachment"] = list(expected_attachment) if expected_attachment else None
    metrics["attachment_error_px"] = round(attachment_error, 6) if attachment_error is not None else None

    border_pixels, border_edges = _border_metrics(
        generated["mask"], int(values["border_margin_px"])
    )
    outside_pixels = _outside_bbox(generated["mask"], structural_bbox)
    invisible_rgb = (generated["alpha"] == 0) & np.any(generated["rgba"][..., :3] != 0, axis=-1)
    metrics["border_pixels"] = border_pixels
    metrics["border_edges"] = border_edges
    metrics["outside_structural_pixels"] = outside_pixels
    metrics["invisible_rgb_pixels"] = int(np.count_nonzero(invisible_rgb))
    character_invisible_count = 0
    character_image: dict[str, Any] | None = None
    if character_path is not None:
        try:
            character_image = _load(character_path, "character_holdout", alpha_threshold)
            if character_image["format"] != "PNG" or character_image["mode"] != "RGBA":
                diagnostics = metrics["diagnostics"]
                diagnostics.append(
                    _diagnostic(
                        row,
                        column,
                        "character_alpha_channel",
                        "error",
                        "character_holdout deve ser PNG RGBA",
                    )
                )
            character_invisible = (character_image["alpha"] == 0) & np.any(
                character_image["rgba"][..., :3] != 0, axis=-1
            )
            character_invisible_count = int(np.count_nonzero(character_invisible))
        except ValueError as exc:
            metrics["diagnostics"].append(
                _diagnostic(row, column, "character_invalid", "error", str(exc))
            )
    metrics["character_invisible_rgb_pixels"] = character_invisible_count

    diagnostics = metrics["diagnostics"]
    if structural_bbox is None and generated_bbox is not None:
        diagnostics.append(
            _diagnostic(row, column, "structural_foreground_missing", "error", "foreground estrutural ausente")
        )
    elif structural_bbox is not None and generated_bbox is None:
        diagnostics.append(
            _diagnostic(row, column, "generated_foreground_missing", "error", "foreground gerado ausente")
        )
    if iou < float(values["min_iou"]):
        diagnostics.append(
            _diagnostic(row, column, "silhouette_iou", "error", "IoU da silhueta abaixo do limite", round(iou, 6), values["min_iou"])
        )
    elif iou < float(values["warning_iou"]):
        diagnostics.append(
            _diagnostic(row, column, "silhouette_iou", "warning", "IoU da silhueta requer revisão", round(iou, 6), values["warning_iou"])
        )
    if center_error is not None:
        if center_error > float(values["max_center_error_px"]):
            diagnostics.append(
                _diagnostic(row, column, "center_offset", "error", "centro da arma deslocado", round(center_error, 6), values["max_center_error_px"])
            )
        elif center_error > float(values["warning_center_error_px"]):
            diagnostics.append(
                _diagnostic(row, column, "center_offset", "warning", "centro da arma requer revisão", round(center_error, 6), values["warning_center_error_px"])
            )
    if attachment_error is not None:
        if attachment_error > float(values["max_attachment_error_px"]):
            diagnostics.append(
                _diagnostic(row, column, "attachment_offset", "error", "attachment da arma deslocado", round(attachment_error, 6), values["max_attachment_error_px"])
            )
        elif attachment_error > float(values["warning_attachment_error_px"]):
            diagnostics.append(
                _diagnostic(row, column, "attachment_offset", "warning", "attachment da arma requer revisão", round(attachment_error, 6), values["warning_attachment_error_px"])
            )
    if border_pixels > int(values["max_border_pixels"]):
        diagnostics.append(
            _diagnostic(row, column, "border_pixels", "error", f"foreground próximo à borda ({','.join(border_edges)})", border_pixels, values["max_border_pixels"])
        )
    if outside_pixels > int(values["max_outside_structural_pixels"]):
        diagnostics.append(
            _diagnostic(row, column, "out_of_envelope", "error", "foreground gerado fora do envelope estrutural", outside_pixels, values["max_outside_structural_pixels"])
        )
    invisible_count = int(metrics["invisible_rgb_pixels"])
    if invisible_count > int(values["max_invisible_rgb_pixels"]):
        diagnostics.append(
            _diagnostic(row, column, "invisible_rgb", "error", "RGB não transparente fora do alpha", invisible_count, values["max_invisible_rgb_pixels"])
        )
    if character_invisible_count > int(values["max_invisible_rgb_pixels"]):
        character_diagnostic = _diagnostic(
            row,
            column,
            "invisible_rgb",
            "error",
            "RGB não transparente fora do alpha na camada do personagem",
            character_invisible_count,
            values["max_invisible_rgb_pixels"],
        )
        character_diagnostic["layer"] = "character_holdout"
        diagnostics.append(character_diagnostic)
    metrics["valid"] = not any(item["severity"] == "error" for item in diagnostics)
    return metrics


def validate_holdout_outputs(
    cells: Sequence[dict[str, Any]] | dict[str, Any],
    *,
    grid: Sequence[int] = DEFAULT_GRID,
    thresholds: dict[str, Any] | None = None,
    attachment: Sequence[float] | None = None,
    output_path: Path | str | None = None,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Validate structural/generated weapon cells and optionally persist JSON."""
    rows, columns = _validated_grid(grid)
    values = _thresholds(thresholds)
    raw_cells = cells.get("cells") if isinstance(cells, dict) else cells
    if not isinstance(raw_cells, Sequence) or isinstance(raw_cells, (str, bytes)):
        raise ValueError("cells deve ser uma sequência")

    expected = {(row, column) for row in range(rows) for column in range(columns)}
    indexed: dict[tuple[int, int], dict[str, Any]] = {}
    global_diagnostics: list[dict[str, Any]] = []
    for item in raw_cells:
        if not isinstance(item, dict):
            global_diagnostics.append(_diagnostic(-1, -1, "cell_invalid", "error", "célula deve ser um objeto"))
            continue
        row, column = item.get("row"), item.get("column")
        key = (row, column)
        if type(row) is not int or type(column) is not int or key not in expected:
            global_diagnostics.append(_diagnostic(-1, -1, "cell_out_of_grid", "error", f"célula fora da grade: {key}"))
            continue
        if key in indexed:
            global_diagnostics.append(_diagnostic(row, column, "cell_duplicate", "error", "célula duplicada"))
            continue
        indexed[key] = item
    missing = sorted(expected - set(indexed))
    for row, column in missing:
        global_diagnostics.append(_diagnostic(row, column, "cell_missing", "error", "célula ausente"))

    cell_reports = [
        _cell_metrics(
            indexed[(row, column)],
            row=row,
            column=column,
            values=values,
            fallback_attachment=attachment,
        )
        for row, column in sorted(indexed)
    ]
    diagnostics = global_diagnostics + [item for cell in cell_reports for item in cell["diagnostics"]]
    errors = [item for item in diagnostics if item["severity"] == "error"]
    warnings = [item for item in diagnostics if item["severity"] == "warning"]
    ious = [float(cell["silhouette_iou"]) for cell in cell_reports if cell.get("silhouette_iou") is not None]
    centers = [float(cell["center_error_px"]) for cell in cell_reports if cell.get("center_error_px") is not None]
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "validated": not errors,
        "status": "error" if errors else "warning" if warnings else "pass",
        "cell_count": len(indexed),
        "expected_cell_count": rows * columns,
        "grid": [rows, columns],
        "thresholds": values,
        "violation_count": len(errors),
        "warning_count": len(warnings),
        "violations": errors,
        "warnings": warnings,
        "diagnostics": diagnostics,
        "cells": cell_reports,
        "summary": {
            "silhouette_iou_mean": round(sum(ious) / len(ious), 6) if ious else None,
            "center_error_px_max": round(max(centers), 6) if centers else None,
            "valid_cell_count": sum(bool(cell.get("valid")) for cell in cell_reports),
        },
    }
    destination: Path | None
    if output_path is not None:
        destination = Path(output_path)
    elif output_dir is not None:
        destination = Path(output_dir) / "holdout_validation.json"
    else:
        destination = None
    if destination is not None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(destination)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    return report


validate_holdout_cells = validate_holdout_outputs
validate_holdout = validate_holdout_outputs


def _load_rgba_for_composition(path: Path | str, label: str) -> np.ndarray:
    resolved = Path(path)
    if not resolved.is_file():
        raise ValueError(f"arquivo ausente: {label}: {resolved}")
    try:
        with Image.open(resolved) as opened:
            if opened.format != "PNG" or opened.mode != "RGBA":
                raise ValueError(f"{label} deve ser PNG RGBA: {resolved}")
            return np.asarray(opened, dtype=np.uint8).copy()
    except OSError as exc:
        raise ValueError(f"arquivo inválido: {label}: {resolved}: {exc}") from None


def _load_mask_for_composition(path: Path | str) -> np.ndarray:
    resolved = Path(path)
    if not resolved.is_file():
        raise ValueError(f"arquivo ausente: visibility_mask: {resolved}")
    try:
        with Image.open(resolved) as opened:
            if opened.format != "PNG":
                raise ValueError(f"visibility_mask deve ser PNG: {resolved}")
            if "A" in opened.getbands():
                return np.asarray(opened.convert("RGBA"), dtype=np.uint8)[..., 3].copy()
            return np.asarray(opened.convert("L"), dtype=np.uint8).copy()
    except OSError as exc:
        raise ValueError(f"arquivo inválido: visibility_mask: {resolved}: {exc}") from None


def _max_channel_error(actual: np.ndarray, expected: np.ndarray) -> int:
    if actual.shape != expected.shape:
        raise ValueError("imagens layered possuem dimensões incompatíveis")
    difference = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
    return int(difference.max(initial=0))


def validate_layered_composition(
    *,
    character_source: Path | str,
    character_full: Path | str,
    component_source: Path | str,
    visibility_mask: Path | str,
    component_visible: Path | str,
    preview: Path | str,
    grid: Sequence[int] = DEFAULT_GRID,
    tolerance: int = 1,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    """Block publication unless a v2 layered composition is pixel-correct.

    The contract deliberately validates the rendered artifacts rather than a
    compositor report: the base character must be byte-identical, the
    detachable layer alpha must equal ``component_alpha * visibility_mask``,
    and the preview must equal straight-alpha source-over composition.
    """
    rows, columns = _validated_grid(grid)
    if type(tolerance) is not int or not 0 <= tolerance <= 32:
        raise ValueError("tolerance deve ser um inteiro entre 0 e 32")

    source_character = _load_rgba_for_composition(character_source, "character_source")
    output_character = _load_rgba_for_composition(character_full, "character_full")
    source_component = _load_rgba_for_composition(component_source, "component_source")
    output_component = _load_rgba_for_composition(component_visible, "component_visible")
    output_preview = _load_rgba_for_composition(preview, "preview")
    mask = _load_mask_for_composition(visibility_mask)

    shape = source_character.shape
    for label, image in (
        ("character_full", output_character),
        ("component_source", source_component),
        ("component_visible", output_component),
        ("preview", output_preview),
    ):
        if image.shape != shape:
            raise ValueError(f"{label} possui dimensões incompatíveis")
    if mask.shape != shape[:2]:
        raise ValueError("visibility_mask possui dimensões incompatíveis")
    _validated_grid((rows, columns))
    if shape[0] % rows or shape[1] % columns:
        raise ValueError("dimensões layered devem ser divisíveis pela grade")

    expected_alpha = np.clip(
        np.rint(
            source_component[..., 3].astype(np.float64)
            * mask.astype(np.float64)
            / 255.0
        ),
        0.0,
        255.0,
    ).astype(np.uint8)
    expected_component = source_component.copy()
    expected_component[..., 3] = expected_alpha
    expected_component[expected_alpha == 0, :3] = 0
    character_error = _max_channel_error(output_character, source_character)
    component_alpha_error = _max_channel_error(
        output_component[..., 3], expected_component[..., 3]
    )
    component_rgb_error = _max_channel_error(
        output_component[expected_alpha > 0, :3],
        expected_component[expected_alpha > 0, :3],
    ) if np.any(expected_alpha > 0) else 0
    invisible_rgb_pixels = int(
        np.count_nonzero(
            (output_component[..., 3] == 0)
            & np.any(output_component[..., :3] != 0, axis=-1)
        )
    )

    character_image = Image.fromarray(output_character, mode="RGBA")
    component_image = Image.fromarray(output_component, mode="RGBA")
    try:
        expected_preview = np.asarray(
            Image.alpha_composite(character_image, component_image), dtype=np.uint8
        ).copy()
    finally:
        character_image.close()
        component_image.close()
    preview_error = _max_channel_error(output_preview, expected_preview)
    opaque_base = output_character[..., 3] == 255
    opacity_holes = int(np.count_nonzero(opaque_base & (output_preview[..., 3] < 255)))
    leaked_alpha = int(
        np.count_nonzero(
            output_component[..., 3].astype(np.int16)
            > source_component[..., 3].astype(np.int16) + tolerance
        )
    )

    checks = {
        "character_immutable": character_error == 0,
        "component_alpha_matches_visibility": component_alpha_error <= tolerance,
        "component_rgb_preserved": component_rgb_error <= tolerance,
        "transparent_rgb_clean": invisible_rgb_pixels == 0,
        "preview_is_alpha_over": preview_error <= tolerance,
        "opaque_character_stays_opaque": opacity_holes == 0,
        "component_alpha_does_not_expand": leaked_alpha == 0,
    }
    violations = [name for name, passed in checks.items() if not passed]
    report: dict[str, Any] = {
        "schema": COMPOSITION_SCHEMA,
        "validated": not violations,
        "status": "pass" if not violations else "error",
        "grid": [rows, columns],
        "size": [int(shape[1]), int(shape[0])],
        "tolerance": tolerance,
        "checks": checks,
        "violations": violations,
        "metrics": {
            "character_max_channel_error": character_error,
            "component_alpha_max_error": component_alpha_error,
            "component_rgb_max_error": component_rgb_error,
            "preview_max_channel_error": preview_error,
            "invisible_rgb_pixels": invisible_rgb_pixels,
            "opaque_base_hole_pixels": opacity_holes,
            "component_alpha_expansion_pixels": leaked_alpha,
        },
    }
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(destination)
        temporary = destination.with_name(f".{destination.name}.tmp")
        temporary.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    if violations:
        raise ValueError(
            "composição layered inválida: " + ", ".join(violations)
        )
    return report


__all__ = [
    "COMPOSITION_SCHEMA",
    "DEFAULT_GRID",
    "DEFAULT_THRESHOLDS",
    "SCHEMA",
    "validate_holdout",
    "validate_holdout_cells",
    "validate_holdout_outputs",
    "validate_layered_composition",
]
