"""Structural and temporal metrics for generated reference-pack frames."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

import conditioning_image
import conditioning_schema as schema


def _bbox_from_mask(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _mask_iou(first: np.ndarray, second: np.ndarray) -> float:
    if first.shape != second.shape:
        return 0.0
    union = np.logical_or(first, second).sum()
    if not union:
        return 1.0
    return float(np.logical_and(first, second).sum() / union)


def _resize_mask(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    from PIL import Image

    image = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), "L")
    resized = image.resize(size, Image.Resampling.NEAREST)
    return np.asarray(resized) > 127


def frame_metrics(
    reference: Path,
    generated: Path,
    *,
    cell_size: tuple[int, int],
    foot_anchor: tuple[float, float],
) -> dict[str, Any]:
    """Compare one generated frame with the structural source frame."""
    source_image = conditioning_image.load_rgba(reference)
    generated_image = conditioning_image.load_rgba(generated)
    source_mask = conditioning_image.foreground_mask(source_image)
    generated_mask = conditioning_image.foreground_mask(generated_image)
    source_bbox = _bbox_from_mask(source_mask)
    generated_bbox = _bbox_from_mask(generated_mask)
    source_image.close()
    generated_image.close()
    if generated_bbox is None:
        return {
            "generated": generated.name,
            "valid": False,
            "error": "nenhum foreground detectado",
        }

    x0, y0, x1, y1 = generated_bbox
    source_scaled = _resize_mask(source_mask, (cell_size[0], cell_size[1]))
    generated_scaled = _resize_mask(generated_mask, (cell_size[0], cell_size[1]))
    center_x = (x0 + x1) / 2.0
    bottom_y = float(y1 + 1)
    return {
        "generated": generated.name,
        "valid": True,
        "source_bbox": list(source_bbox) if source_bbox else None,
        "generated_bbox": list(generated_bbox),
        "source_area_ratio": round(float(source_mask.mean()), 6),
        "generated_area_ratio": round(float(generated_mask.mean()), 6),
        "bbox_width": x1 - x0 + 1,
        "bbox_height": y1 - y0 + 1,
        "foot_anchor_error": round(abs(bottom_y - foot_anchor[1]), 3),
        "foot_anchor_x_error": round(abs(center_x - foot_anchor[0]), 3),
        "silhouette_iou_diagnostic": round(_mask_iou(source_scaled, generated_scaled), 6),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _temporal_metrics(paths: list[Path]) -> dict[str, Any]:
    from PIL import Image

    if len(paths) < 2:
        return {"pair_count": 0, "mean_frame_delta": None, "max_frame_delta": None}
    deltas: list[float] = []
    previous: np.ndarray | None = None
    for path in paths:
        with Image.open(path) as image:
            current = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
        if previous is not None:
            deltas.append(float(np.abs(current - previous).mean()))
        previous = current
    return {
        "pair_count": len(deltas),
        "mean_frame_delta": round(sum(deltas) / len(deltas), 6),
        "max_frame_delta": round(max(deltas), 6),
    }


def evaluate_generated(
    manifest_path: Path,
    generated_dir: Path,
    *,
    output_path: Path | None = None,
    foot_anchor: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Evaluate generated frames and optionally write a metrics report."""
    manifest = schema.load_manifest(manifest_path)
    root = manifest_path.parent
    cell_size = tuple(manifest["cell_size"])
    anchor = foot_anchor or tuple(manifest["foot_anchor"])
    rows: list[dict[str, Any]] = []
    generated_paths: list[Path] = []
    for frame in manifest["frames"]:
        frame_id = frame["id"]
        reference_path = root / frame["channels"]["beauty"]
        generated_path = generated_dir / f"{frame_id}.png"
        generated_paths.append(generated_path)
        if not generated_path.is_file():
            rows.append({"generated": generated_path.name, "valid": False, "error": "arquivo ausente"})
            continue
        rows.append(
            frame_metrics(
                reference_path,
                generated_path,
                cell_size=cell_size,
                foot_anchor=(float(anchor[0]), float(anchor[1])),
            )
        )
    valid_rows = [row for row in rows if row.get("valid")]
    report: dict[str, Any] = {
        "schema": "generation.conditioning_metrics/v1",
        "manifest": str(manifest_path.resolve()),
        "generated_dir": str(generated_dir.resolve()),
        "frame_count": len(rows),
        "valid_frame_count": len(valid_rows),
        "frames": rows,
        "summary": {
            "foot_anchor_error_mean": _mean([float(row["foot_anchor_error"]) for row in valid_rows]),
            "foot_anchor_error_max": max((float(row["foot_anchor_error"]) for row in valid_rows), default=None),
            "foot_anchor_x_error_mean": _mean([float(row["foot_anchor_x_error"]) for row in valid_rows]),
            "generated_area_ratio_mean": _mean([float(row["generated_area_ratio"]) for row in valid_rows]),
            "silhouette_iou_diagnostic_mean": _mean([float(row["silhouette_iou_diagnostic"]) for row in valid_rows]),
        },
        "temporal": _temporal_metrics([path for path in generated_paths if path.is_file()]),
        "gate": {
            "all_frames_present": len(valid_rows) == len(rows),
            "foot_anchor_error_max_px": 4.0,
            "foot_anchor_pass": bool(valid_rows) and max(float(row["foot_anchor_error"]) for row in valid_rows) <= 4.0,
        },
    }
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("generated_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = evaluate_generated(args.manifest, args.generated_dir, output_path=args.output)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["gate"]["all_frames_present"] else 2


if __name__ == "__main__":
    sys.exit(main())
