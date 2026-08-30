"""Reconstruct and validate an AI-generated spritesheet against 3D controls."""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


SCHEMA = "generation.structural_postprocess/v1"
DEFAULT_DIRECTIONS = ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8")


@dataclass(frozen=True)
class Alignment:
    scale: float
    x: int
    y: int
    silhouette_iou: float


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    if not xs.size:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _iou(first: np.ndarray, second: np.ndarray) -> float:
    union = np.logical_or(first, second).sum()
    if not union:
        return 1.0
    return float(np.logical_and(first, second).sum() / union)


def _paste_mask(
    subject: np.ndarray,
    canvas_size: tuple[int, int],
    x: int,
    y: int,
) -> np.ndarray:
    width, height = canvas_size
    canvas = np.zeros((height, width), dtype=bool)
    source_height, source_width = subject.shape
    left = max(0, x)
    top = max(0, y)
    right = min(width, x + source_width)
    bottom = min(height, y + source_height)
    if right <= left or bottom <= top:
        return canvas
    canvas[top:bottom, left:right] = subject[
        top - y : bottom - y,
        left - x : right - x,
    ]
    return canvas


def reconstruct_alpha(
    image: Image.Image,
    *,
    background_threshold: float = 5.0,
    alpha_softness: float = 18.0,
) -> Image.Image:
    """Recover soft alpha from a near-uniform JPEG background."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    background = np.median(border, axis=0)
    distance = np.sqrt(np.square(rgb - background).sum(axis=2))
    alpha = np.clip(
        (distance - background_threshold) / max(alpha_softness, 0.001),
        0.0,
        1.0,
    )
    rgba = np.dstack((rgb.astype(np.uint8), np.rint(alpha * 255).astype(np.uint8)))
    return Image.fromarray(rgba, "RGBA")


def _structural_mask(beauty: Image.Image, lineart: Image.Image) -> np.ndarray:
    beauty_rgba = beauty.convert("RGBA")
    alpha = np.asarray(beauty_rgba.getchannel("A")) > 16
    if not alpha.any():
        rgb = np.asarray(beauty_rgba.convert("RGB"), dtype=np.int16)
        alpha = np.max(rgb, axis=2) > 16
    line = _lineart_mask(lineart)
    line_image = Image.fromarray(np.where(line, 255, 0).astype(np.uint8), "L")
    dilated = np.asarray(line_image.filter(ImageFilter.MaxFilter(5))) > 0
    return np.logical_or(alpha, dilated)


def _resize_subject_mask(mask: np.ndarray, scale: float) -> np.ndarray:
    height, width = mask.shape
    resized = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), "L").resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.Resampling.NEAREST,
    )
    return np.asarray(resized) > 127


def estimate_alignment(
    generated: Image.Image,
    target_mask: np.ndarray,
    *,
    max_translation: int = 16,
    scale_span: float = 0.12,
    scale_steps: int = 9,
    min_scale: float = 0.70,
    max_scale: float = 1.35,
) -> Alignment:
    """Find a conservative similarity transform maximizing silhouette IoU."""
    generated_mask = np.asarray(generated.getchannel("A")) > 32
    generated_bbox = _bbox(generated_mask)
    target_bbox = _bbox(target_mask)
    if generated_bbox is None or target_bbox is None:
        raise ValueError("foreground ausente durante alinhamento estrutural")
    gx0, gy0, gx1, gy1 = generated_bbox
    tx0, ty0, tx1, ty1 = target_bbox
    subject = generated_mask[gy0:gy1, gx0:gx1]
    generated_height = max(1, gy1 - gy0)
    generated_width = max(1, gx1 - gx0)
    target_height = max(1, ty1 - ty0)
    target_width = max(1, tx1 - tx0)
    base_scale = math.sqrt(
        (target_height / generated_height) * (target_width / generated_width)
    )
    if min_scale <= 0 or max_scale < min_scale:
        raise ValueError("limites de escala inválidos")
    base_scale = min(max_scale, max(min_scale, base_scale))
    scales = np.linspace(
        max(min_scale, base_scale * (1.0 - scale_span)),
        min(max_scale, base_scale * (1.0 + scale_span)),
        scale_steps,
    )
    target_center = ((tx0 + tx1) / 2.0, (ty0 + ty1) / 2.0)
    best: Alignment | None = None
    for scale_value in scales:
        scale = float(scale_value)
        resized = _resize_subject_mask(subject, scale)
        center_x = round(target_center[0] - resized.shape[1] / 2.0)
        center_y = round(target_center[1] - resized.shape[0] / 2.0)
        for dy in range(-max_translation, max_translation + 1, 2):
            for dx in range(-max_translation, max_translation + 1, 2):
                x = center_x + dx
                y = center_y + dy
                candidate = _paste_mask(resized, (generated.width, generated.height), x, y)
                score = _iou(candidate, target_mask)
                if best is None or score > best.silhouette_iou:
                    best = Alignment(scale=scale, x=x, y=y, silhouette_iou=score)
    assert best is not None
    return best


def apply_alignment(image: Image.Image, alignment: Alignment) -> Image.Image:
    alpha_mask = np.asarray(image.getchannel("A")) > 32
    bounds = _bbox(alpha_mask)
    if bounds is None:
        raise ValueError("foreground ausente durante aplicação do alinhamento")
    subject = image.crop(bounds)
    subject = subject.resize(
        (
            max(1, round(subject.width * alignment.scale)),
            max(1, round(subject.height * alignment.scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", image.size, (0, 0, 0, 0))
    canvas.alpha_composite(subject, (alignment.x, alignment.y))
    subject.close()
    return canvas


def _bone_points(cell: dict[str, Any], names: set[str]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for bone in cell.get("bones", []):
        if bone.get("name") not in names:
            continue
        for key in ("head", "tail"):
            value = bone.get(key)
            if isinstance(value, list) and len(value) == 2:
                points.append((float(value[0]), float(value[1])))
    return points


def _foot_anchor_error(mask: np.ndarray, cell: dict[str, Any]) -> float | None:
    feet = _bone_points(
        cell,
        {"foot_l", "ball_l", "ball_leaf_l", "foot_r", "ball_r", "ball_leaf_r"},
    )
    if not feet:
        return None
    errors: list[float] = []
    height, width = mask.shape
    for x, y in feet:
        left = max(0, round(x) - 12)
        right = min(width, round(x) + 13)
        ys, xs = np.where(mask[:, left:right])
        if ys.size:
            nearest = np.square(ys - y) + np.square(xs + left - x)
            errors.append(float(np.sqrt(nearest.min())))
    return min(errors) if errors else None


def _lineart_coverage(mask: np.ndarray, lineart: Image.Image) -> float:
    line = _lineart_mask(lineart)
    if not line.any():
        return 0.0
    expanded = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), "L").filter(
        ImageFilter.MaxFilter(7)
    )
    return float(np.logical_and(line, np.asarray(expanded) > 0).sum() / line.sum())


def _lineart_mask(lineart: Image.Image) -> np.ndarray:
    if "A" in lineart.getbands():
        alpha = np.asarray(lineart.getchannel("A"))
        if alpha.max() > alpha.min():
            return alpha > 24
    return np.asarray(lineart.convert("L")) > 24


def _confidence(
    alignment: Alignment,
    foot_error: float | None,
    lineart_coverage: float,
) -> float:
    anchor_score = math.exp(-max(0.0, foot_error or 0.0) / 10.0)
    scale_penalty = math.exp(-abs(math.log(max(alignment.scale, 0.001))) / 0.35)
    return float(
        np.clip(
            0.60 * alignment.silhouette_iou
            + 0.20 * anchor_score
            + 0.10 * lineart_coverage
            + 0.10 * scale_penalty,
            0.0,
            1.0,
        )
    )


def _decision(confidence: float, foot_error: float | None, crop_risk: bool = False) -> str:
    if crop_risk:
        return "reject"
    if confidence >= 0.72 and (foot_error is None or foot_error <= 6.0):
        return "accept"
    if confidence >= 0.50 and (foot_error is None or foot_error <= 14.0):
        return "review"
    return "reject"


def _review_frame(
    generated: Image.Image,
    beauty: Image.Image,
    lineart: Image.Image,
    metrics: dict[str, Any],
) -> Image.Image:
    cell = Image.new("RGB", (generated.width * 3, generated.height + 28), "#171717")
    cell.paste(beauty.convert("RGB"), (0, 0))
    cell.paste(generated.convert("RGB"), (generated.width, 0))
    overlay = generated.convert("RGBA")
    guide = Image.new("RGBA", generated.size, (0, 0, 0, 0))
    line = Image.fromarray(
        np.where(_lineart_mask(lineart), 210, 0).astype(np.uint8),
        "L",
    )
    guide.paste((0, 255, 255, 210), mask=line)
    overlay.alpha_composite(guide)
    cell.paste(overlay.convert("RGB"), (generated.width * 2, 0))
    draw = ImageDraw.Draw(cell)
    draw.text(
        (4, generated.height + 7),
        f"{metrics['id']}  {metrics['decision']}  conf={metrics['confidence']:.3f}",
        fill="white",
    )
    return cell


def process_sheet(
    generated_sheet: Path,
    structural_dir: Path,
    output_dir: Path,
    *,
    rows: int = 8,
    columns: int = 8,
    cell_size: int = 256,
    fps: float | None = None,
    background_threshold: float = 5.0,
    alpha_softness: float = 18.0,
    alignment_min_scale: float = 0.94,
    alignment_max_scale: float = 1.06,
) -> dict[str, Any]:
    metadata_path = structural_dir / "render_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    cells = {(int(c["row"]), int(c["column"])): c for c in metadata["cells"]}
    directions = tuple(metadata.get("directions") or DEFAULT_DIRECTIONS[:rows])
    effective_fps = float(fps if fps is not None else metadata.get("fps", 10.0))
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "extracted_raw"
    normalized_dir = output_dir / "normalized"
    raw_dir.mkdir(exist_ok=True)
    normalized_dir.mkdir(exist_ok=True)
    with Image.open(generated_sheet) as source:
        sheet = source.convert("RGB")
    expected_size = (columns * cell_size, rows * cell_size)
    if sheet.size != expected_size:
        raise ValueError(f"spritesheet deve medir {expected_size}, recebido {sheet.size}")
    sheet_rgb = np.asarray(sheet, dtype=np.float32)
    sheet_border = np.concatenate(
        (sheet_rgb[0], sheet_rgb[-1], sheet_rgb[:, 0], sheet_rgb[:, -1]), axis=0
    )
    background_color = np.median(sheet_border, axis=0)

    report_rows: list[dict[str, Any]] = []
    reviews: list[Image.Image] = []
    for row in range(rows):
        for column in range(columns):
            frame_id = f"row{row}_col{column}"
            bounds = (
                column * cell_size,
                row * cell_size,
                (column + 1) * cell_size,
                (row + 1) * cell_size,
            )
            raw = reconstruct_alpha(
                sheet.crop(bounds),
                background_threshold=background_threshold,
                alpha_softness=alpha_softness,
            )
            raw.save(raw_dir / f"{frame_id}.png")
            beauty_path = structural_dir / f"{frame_id}.png"
            lineart_path = structural_dir / "lineart" / f"{frame_id}.png"
            with Image.open(beauty_path) as value:
                beauty = value.convert("RGBA")
            with Image.open(lineart_path) as value:
                lineart = value.convert("RGBA")
            target_mask = _structural_mask(beauty, lineart)
            alignment = estimate_alignment(
                raw,
                target_mask,
                min_scale=alignment_min_scale,
                max_scale=alignment_max_scale,
            )
            normalized = apply_alignment(raw, alignment)
            normalized.save(normalized_dir / f"{frame_id}.png")
            normalized_mask = np.asarray(normalized.getchannel("A")) > 32
            raw_bounds = _bbox(np.asarray(raw.getchannel("A")) > 32)
            normalized_bounds = _bbox(normalized_mask)
            crop_risk = bool(
                normalized_bounds
                and (
                    normalized_bounds[0] <= 0
                    or normalized_bounds[1] <= 0
                    or normalized_bounds[2] >= cell_size
                    or normalized_bounds[3] >= cell_size
                )
            )
            foot_error = _foot_anchor_error(normalized_mask, cells[(row, column)])
            lineart_coverage = _lineart_coverage(normalized_mask, lineart)
            confidence = _confidence(alignment, foot_error, lineart_coverage)
            metrics = {
                "id": frame_id,
                "row": row,
                "column": column,
                "direction": directions[row] if row < len(directions) else f"r{row + 1}",
                "scale": round(alignment.scale, 6),
                "translation": [
                    alignment.x - raw_bounds[0] if raw_bounds else 0,
                    alignment.y - raw_bounds[1] if raw_bounds else 0,
                ],
                "placement": [alignment.x, alignment.y],
                "silhouette_iou": round(alignment.silhouette_iou, 6),
                "lineart_coverage": round(lineart_coverage, 6),
                "foot_anchor_error": round(foot_error, 3) if foot_error is not None else None,
                "crop_risk": crop_risk,
                "confidence": round(confidence, 6),
                "decision": _decision(confidence, foot_error, crop_risk),
            }
            report_rows.append(metrics)
            reviews.append(_review_frame(normalized, beauty, lineart, metrics))
            raw.close()
            normalized.close()
            beauty.close()
            lineart.close()
    sheet.close()

    import sprite_render

    for row in range(rows):
        for column in range(columns):
            source = normalized_dir / f"row{row}_col{column}.png"
            (output_dir / source.name).write_bytes(source.read_bytes())
    spritesheet = sprite_render._build_sheet(output_dir, rows, columns, cell_size)
    gifs = sprite_render._build_gifs(
        output_dir,
        rows,
        columns,
        effective_fps,
        directions=directions,
    )
    review_sheet = Image.new(
        "RGB",
        (columns * cell_size * 3, rows * (cell_size + 28)),
        "#171717",
    )
    for index, review in enumerate(reviews):
        row, column = divmod(index, columns)
        review_sheet.paste(review, (column * cell_size * 3, row * (cell_size + 28)))
        review.close()
    review_path = output_dir / "review_sheet.png"
    review_sheet.save(review_path)
    review_sheet.close()

    decisions = {name: sum(r["decision"] == name for r in report_rows) for name in ("accept", "review", "reject")}
    report = {
        "schema": SCHEMA,
        "generated_sheet": str(generated_sheet.resolve()),
        "structural_dir": str(structural_dir.resolve()),
        "rows": rows,
        "columns": columns,
        "cell_size": cell_size,
        "fps": effective_fps,
        "conditioning_channels": ["beauty", "bones", "lineart"],
        "background_color": [round(float(value), 3) for value in background_color],
        "alignment_scale_limits": [alignment_min_scale, alignment_max_scale],
        "frames": report_rows,
        "summary": {
            "confidence_mean": round(float(np.mean([r["confidence"] for r in report_rows])), 6),
            "confidence_min": round(float(min(r["confidence"] for r in report_rows)), 6),
            "silhouette_iou_mean": round(float(np.mean([r["silhouette_iou"] for r in report_rows])), 6),
            "foot_anchor_error_max": round(float(max(r["foot_anchor_error"] or 0.0 for r in report_rows)), 3),
            "decisions": decisions,
        },
        "artifacts": {
            "spritesheet": str(spritesheet.resolve()),
            "review_sheet": str(review_path.resolve()),
            "gifs": {key: str(value.resolve()) for key, value in gifs.items()},
        },
    }
    (output_dir / "metrics.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generated_sheet", type=Path)
    parser.add_argument("structural_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--columns", type=int, default=8)
    parser.add_argument("--cell-size", type=int, default=256)
    parser.add_argument("--fps", type=float)
    parser.add_argument("--background-threshold", type=float, default=5.0)
    parser.add_argument("--alpha-softness", type=float, default=18.0)
    args = parser.parse_args(argv)
    report = process_sheet(
        args.generated_sheet,
        args.structural_dir,
        args.output_dir,
        rows=args.rows,
        columns=args.columns,
        cell_size=args.cell_size,
        fps=args.fps,
        background_threshold=args.background_threshold,
        alpha_softness=args.alpha_softness,
    )
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return 0 if report["summary"]["decisions"]["reject"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
