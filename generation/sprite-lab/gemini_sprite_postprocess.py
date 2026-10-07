"""Turn an approved 2048px Gemini sheet into the four Sprite Lab variants."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import huggingface_realesrgan
import semantic_preview
import sprite_render
import asset_manifest
from source_alpha_intersection import intersect_with_source_alpha
from PIL import Image
from direction_contract import direction_contract_for
from postprocess_runtime import (
    add_runtime_arguments,
    runtime_batch_size,
    runtime_cpu_workers,
    validate_parallelism,
)


MASK_CACHE_ROOT = Path(__file__).resolve().parent / "work" / "mask-cache"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _source_render_info(structural_dir: Path) -> dict[str, Any]:
    """Collect timing and contract data from the structural render source."""
    source_manifest_path = structural_dir / "asset_manifest.json"
    source_manifest = _read_json(source_manifest_path)
    metadata_path = structural_dir / "render_metadata.json"
    metadata = _read_json(metadata_path)
    report = _read_json(structural_dir / "render.json")
    source_asset = source_manifest.get("asset") or report.get("asset_spec") or metadata.get("asset")
    try:
        asset_spec = asset_manifest.normalize_asset_spec(source_asset)
    except ValueError:
        asset_spec = asset_manifest.normalize_asset_spec({})
    worker = report.get("worker") if isinstance(report.get("worker"), dict) else metadata

    animation_source = worker.get("animation_source") or metadata.get("animation_source")
    if not isinstance(animation_source, dict):
        animation_source = {}
    relationship_id = report.get("relationship_id")
    if relationship_id:
        relationship = next(
            (
                item
                for item in sprite_render._relationships()
                if str(item.get("id")) == str(relationship_id)
            ),
            None,
        )
        if relationship:
            _, animations_catalog = semantic_preview.load_manifests()
            animation = next(
                (
                    item
                    for item in animations_catalog.get("animations", [])
                    if str(item.get("id")) == str(relationship.get("animation_id"))
                ),
                None,
            )
            if animation:
                animation_source = {
                    key: animation.get(key)
                    for key in (
                        "id",
                        "action_name",
                        "clip_name",
                        "category",
                        "frame_start",
                        "frame_end",
                        "frame_count",
                        "fps",
                        "duration_seconds",
                        "loop",
                        "loop_recommended",
                        "loop_name_hint",
                        "root_motion",
                    )
                }

    source_fps = animation_source.get("fps")
    if source_fps is None:
        source_fps = worker.get("source_fps") or metadata.get("source_fps")
    try:
        source_fps = float(source_fps) if source_fps is not None else None
    except (TypeError, ValueError):
        source_fps = None
    frame_range = worker.get("frame_range") or metadata.get("frame_range")
    cycle_period = worker.get("cycle_period")
    if cycle_period is None:
        cycle_period = metadata.get("cycle_period")
    try:
        cycle_period = int(cycle_period) if cycle_period is not None else None
    except (TypeError, ValueError):
        cycle_period = None
    catalog_duration = animation_source.get("duration_seconds")
    source_duration = worker.get("source_duration_seconds") or metadata.get("source_duration_seconds")
    if source_duration is None and source_fps and isinstance(frame_range, list) and len(frame_range) == 2:
        source_duration = (float(frame_range[1]) - float(frame_range[0])) / source_fps
    try:
        source_duration = float(source_duration) if source_duration is not None else None
    except (TypeError, ValueError):
        source_duration = None
    try:
        catalog_duration = float(catalog_duration) if catalog_duration is not None else None
    except (TypeError, ValueError):
        catalog_duration = None
    cycle_duration = cycle_period / source_fps if cycle_period and source_fps else source_duration
    output_fps = worker.get("output_fps") or worker.get("fps") or metadata.get("fps")
    try:
        output_fps = float(output_fps) if output_fps is not None else None
    except (TypeError, ValueError):
        output_fps = None
    phases = worker.get("phases") or metadata.get("phases")
    cycle_detected = bool(
        worker.get("cycle_detected", worker.get("looping", metadata.get("looping", False)))
    )
    source_loop = animation_source.get("loop")
    loop_recommended = animation_source.get("loop_recommended")
    playback_loop = worker.get("playback_loop")
    if not isinstance(playback_loop, bool):
        if isinstance(loop_recommended, bool):
            playback_loop = loop_recommended
        elif isinstance(source_loop, bool):
            playback_loop = source_loop
        else:
            playback_loop = cycle_detected
    playback_duration = cycle_duration if playback_loop and cycle_duration else source_duration
    timing = {
        "source_frame_range": frame_range,
        "source_fps": round(source_fps, 6) if source_fps else None,
        "source_duration_seconds": round(source_duration, 6) if source_duration else None,
        "catalog_duration_seconds": round(catalog_duration, 6) if catalog_duration else None,
        "cycle_period_frames": cycle_period,
        "cycle_duration_seconds": round(cycle_duration, 6) if cycle_duration else None,
        "playback_duration_seconds": round(playback_duration, 6) if playback_duration else None,
        "output_fps": round(output_fps, 6) if output_fps else None,
        "phase_count": int(phases) if phases is not None else None,
        "phase_duration_seconds": (
            round(cycle_duration / int(phases), 6)
            if cycle_duration and phases
            else None
        ),
        "sampled_frames": worker.get("sampled_frames") or metadata.get("sampled_frames"),
        "cycle_detected": cycle_detected,
        "source_loop": source_loop if isinstance(source_loop, bool) else None,
        "loop_recommended": loop_recommended if isinstance(loop_recommended, bool) else None,
        "playback_loop": playback_loop,
        "loop": playback_loop,
        "source_fps_origin": (
            "animation_catalog"
            if animation_source.get("fps") is not None
            else "render_metadata"
            if source_fps
            else None
        ),
    }
    render_properties = {}
    for key in (
        "camera",
        "render_profile",
        "effective_render_profile",
        "cell",
        "cell_fit",
        "ortho_fit",
        "root_motion_removed",
        "root_motion_lock",
        "bounds",
        "depth",
        "transparent_background",
        "components",
        "weapon",
    ):
        value = worker.get(key)
        if value is None:
            value = metadata.get(key)
        if value is not None:
            render_properties[key] = value
    return {
        "metadata_path": str(metadata_path.resolve()) if metadata_path.is_file() else None,
        "manifest_path": str(source_manifest_path.resolve()) if source_manifest_path.is_file() else None,
        "asset_id": source_asset.get("id") if isinstance(source_asset, dict) else None,
        "asset_name": source_asset.get("name") if isinstance(source_asset, dict) else None,
        "asset_spec": asset_spec,
        "animation_source": animation_source or None,
        "animation_timing": timing,
        "direction_contract": worker.get("direction_contract") or metadata.get("direction_contract"),
        "render_properties": render_properties,
    }


def _direction_contract(rows: int) -> dict[str, Any]:
    return direction_contract_for(sprite_render.DIRECTION_ROWS[:rows])


def _runtime_asset_contract(
    asset_spec: dict[str, Any],
    direction_contract: dict[str, Any],
    *,
    rows: int,
    phases: int,
    cell_size: int,
    foot_anchor: list[int] | list[float],
    timing: dict[str, Any],
    texture: str,
    lineart_texture: str | None = None,
) -> dict[str, Any]:
    """Build the engine-neutral runtime section consumed by game adapters."""
    runtime = {
        "schema": "sprite_lab.runtime_asset/v1",
        "representation": asset_spec["representation"],
        "atlas": {
            "texture": texture,
            "rows": rows,
            "columns": phases,
            "cell_size": [cell_size, cell_size],
            "phase_order": "columns_0_to_phases_minus_1",
        },
        "directions": direction_contract,
        "animation": {
            "fps": timing.get("output_fps"),
            "loop": timing.get("playback_loop", False),
            "duration_seconds": timing.get("playback_duration_seconds"),
        },
        "pivot": {
            "foot_anchor": list(foot_anchor),
            "normalized": [
                float(foot_anchor[0]) / max(cell_size, 1),
                float(foot_anchor[1]) / max(cell_size, 1),
            ],
        },
        "background": {"mode": "transparent"},
    }
    if lineart_texture:
        runtime["lineart"] = {
            "texture": lineart_texture,
            "rows": rows,
            "columns": phases,
            "cell_size": [cell_size, cell_size],
            "phase_order": "columns_0_to_phases_minus_1",
            "color": "white",
            "background": "transparent",
            "composite": "runtime_layer",
        }
    return runtime


def _write_postprocess_asset_manifest(
    output: Path,
    root_report: dict[str, Any],
    source_render: dict[str, Any],
) -> dict[str, Any]:
    """Write the canonical bundle manifest for the final Gemini export."""
    output = output.expanduser().resolve()
    rows = int(root_report.get("rows", 8))
    phases = int(root_report.get("phases", 8))
    output_cell = int(root_report.get("output_cell", 512))
    foot_anchor = root_report.get("foot_anchor") or [output_cell // 2, round(output_cell * 0.86)]
    direction_contract = _direction_contract(rows)
    timing = source_render["animation_timing"]
    asset_spec = source_render.get("asset_spec") or asset_manifest.normalize_asset_spec({})
    lineart_export = root_report.get("lineart_export") or {}
    runtime = _runtime_asset_contract(
        asset_spec,
        direction_contract,
        rows=rows,
        phases=phases,
        cell_size=output_cell,
        foot_anchor=foot_anchor,
        timing=timing,
        texture="variants/original/spritesheet.png",
        lineart_texture=(
            lineart_export.get("spritesheet")
            if lineart_export.get("enabled")
            else None
        ),
    )
    artifact_entries: list[tuple[str, Path | str | None]] = [
        ("generated_sheet", root_report.get("generated_sheet")),
        ("source_render_metadata", source_render.get("metadata_path")),
        ("source_asset_manifest", source_render.get("manifest_path")),
        ("render_metadata", output / "render_metadata.json"),
    ]
    for variant_name in root_report.get("variants", {}):
        variant = output / "variants" / str(variant_name)
        artifact_entries.extend(
            [
                (f"{variant_name}_spritesheet", variant / "spritesheet.png"),
                (f"{variant_name}_ordered_gif", variant / "animation_all_directions_1-2-5-4-3-8-7-6.gif"),
                (f"{variant_name}_metadata", variant / "render_metadata.json"),
            ]
        )
    if lineart_export.get("enabled"):
        lineart = output / str(lineart_export.get("directory") or "lineart")
        artifact_entries.extend(
            [
                ("lineart_spritesheet", lineart / "spritesheet.png"),
                (
                    "lineart_ordered_gif",
                    lineart / "animation_all_directions_1-2-5-4-3-8-7-6.gif",
                ),
            ]
        )
    manifest = asset_manifest.build_manifest(
        asset_spec,
        asset_id=str(
            source_render.get("asset_id")
            or
            (source_render.get("animation_source") or {}).get("id")
            or root_report.get("generated_sheet")
            or output.name
        ),
        name=str(
            source_render.get("asset_name")
            or
            (source_render.get("animation_source") or {}).get("clip_name")
            or (source_render.get("animation_source") or {}).get("action_name")
            or output.name
        ),
        contract={
            "direction_contract": direction_contract,
            "camera": (source_render.get("render_properties") or {}).get("camera", {}),
            "background": "transparent",
        },
        source={
            "generated_sheet": str(Path(str(root_report.get("generated_sheet"))).resolve()),
            "structural_render": str(Path(str(root_report.get("structural_dir"))).resolve()),
            "render_metadata": source_render.get("metadata_path"),
            "asset_manifest": source_render.get("manifest_path"),
            "animation": source_render.get("animation_source"),
        },
        generation={
            "renderer": "gemini_postprocess",
            "pipeline": root_report.get("pipeline", []),
            "model_profile": root_report.get("model_profile"),
            "mask_model_profile": root_report.get("mask_model_profile"),
        },
        layout={
            "rows": rows,
            "columns": phases,
            "cell_size": [output_cell, output_cell],
            "variants": list(root_report.get("variants", {})),
        },
        animation={
            "source": source_render.get("animation_source"),
            "timing": timing,
            "output_fps": root_report.get("fps"),
        },
        placement={
            "pivot": runtime["pivot"],
            "render": source_render.get("render_properties", {}),
        },
        gameplay={"capabilities": asset_spec["capabilities"]},
        runtime=runtime,
        artifacts=asset_manifest.collect_artifacts(output, artifact_entries),
        validation={
            "status": "postprocessed",
            "variants": list(root_report.get("variants", {})),
            "expected_cells": rows * phases,
        },
        provenance={
            "pipeline": "sprite_lab.gemini_sprite_postprocess/v1",
            "source_manifest": source_render.get("manifest_path"),
        },
    )
    asset_manifest.write_manifest(output / "asset_manifest.json", manifest)
    return manifest


def enrich_postprocess_exports(output: Path, structural_dir: Path) -> None:
    """Backfill shared animation metadata into an already completed export."""
    root_path = output / "render_metadata.json"
    root_report = _read_json(root_path)
    if not root_report:
        raise FileNotFoundError(root_path)
    source_render = _source_render_info(structural_dir)
    rows = int(root_report.get("rows", 8))
    source_cell = int(root_report.get("source_cell", 256))
    output_cell = int(root_report.get("output_cell", source_cell * 2))
    foot_anchor = root_report.get("foot_anchor") or [128, 220]
    direction_contract = _direction_contract(rows)
    lineart_export = root_report.get("lineart_export") or {}
    shared = {
        "asset_spec": source_render["asset_spec"],
        "animation_source": source_render["animation_source"],
        "animation_timing": source_render["animation_timing"],
        "source_render_metadata": source_render["metadata_path"],
        "source_render_properties": source_render["render_properties"],
        "direction_contract": direction_contract,
        "runtime_contract": {
            "schema": "sprite_lab.godot_sprite_runtime/v1",
            "atlas": {
                "rows": rows,
                "columns": int(root_report.get("phases", 8)),
                "cell_size": [output_cell, output_cell],
                "phase_order": "columns_0_to_phases_minus_1",
            },
            "directions": direction_contract,
            "animation_source": source_render["animation_source"],
            "animation_timing": source_render["animation_timing"],
            "pivot": {
                "foot_anchor": foot_anchor,
                "normalized": [foot_anchor[0] / output_cell, foot_anchor[1] / output_cell],
            },
            "render": source_render["render_properties"],
            "background_mode": "transparent",
        },
        "runtime_asset_contract": _runtime_asset_contract(
            source_render["asset_spec"],
            direction_contract,
            rows=rows,
            phases=int(root_report.get("phases", 8)),
            cell_size=output_cell,
            foot_anchor=foot_anchor,
            timing=source_render["animation_timing"],
            texture="variants/original/spritesheet.png",
            lineart_texture=(
                lineart_export.get("spritesheet")
                if lineart_export.get("enabled")
                else None
            ),
        ),
        "asset_manifest": "asset_manifest.json",
        "lineart_export": lineart_export,
    }
    if lineart_export.get("enabled"):
        shared["runtime_contract"]["lineart"] = {
            "texture": lineart_export.get("spritesheet"),
            "rows": rows,
            "columns": int(root_report.get("phases", 8)),
            "cell_size": [output_cell, output_cell],
            "phase_order": "columns_0_to_phases_minus_1",
            "color": "white",
            "background_mode": "transparent",
            "composite": "runtime_layer",
        }
    root_report.update(shared)
    sprite_render.write_json_atomic(root_path, root_report)
    for variant_path in sorted((output / "variants").glob("*/render_metadata.json")):
        variant_report = _read_json(variant_path)
        if not variant_report:
            continue
        variant_report.update(shared)
        variant_report["asset_manifest"] = "../../asset_manifest.json"
        if root_report.get("direction_contract"):
            variant_report["direction_contract"] = root_report["direction_contract"]
        if "fps" not in variant_report or variant_report["fps"] is None:
            variant_report["fps"] = root_report.get("fps")
        sprite_render.write_json_atomic(variant_path, variant_report)
    _write_postprocess_asset_manifest(output, root_report, source_render)


def _mask_cache_key(generated_sheet: Path, rows: int, phases: int) -> str:
    digest = hashlib.sha256()
    with generated_sheet.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    digest.update(f"rows={rows};phases={phases};mask_model=anime_x4plus_6b;threshold=0.50;input=1024".encode())
    return digest.hexdigest()[:32]


def _load_cached_mask_pass(mask_pass: Path, cache_key: str, rows: int, phases: int) -> dict[str, Any] | None:
    cache_dir = MASK_CACHE_ROOT / cache_key
    cached_masks = cache_dir / "foreground_cleanup_masks"
    cached_report = cache_dir / "mask_report.json"
    expected = rows * phases
    if not cached_report.is_file() or len(list(cached_masks.glob("row*_col*.png"))) != expected:
        local_masks = mask_pass / "foreground_cleanup_masks"
        local_metadata = mask_pass / "render_metadata.json"
        if local_metadata.is_file() and len(list(local_masks.glob("row*_col*.png"))) == expected:
            return {
                "schema": "sprite_lab.realesrgan_birefnet/v1",
                "resumed_from_local_output": True,
                "source_metadata": str(local_metadata),
            }
        # A failed run can finish Real-ESRGAN and BiRefNet, then abort during
        # optional chroma cleanup. Reuse the already approved BiRefNet masks
        # instead of paying for both heavyweight passes again.
        local_cells = list(mask_pass.glob("row*_col*.png"))
        birefnet_masks = mask_pass / "birefnet_masks"
        available_masks = list(birefnet_masks.glob("row*_col*.png"))
        if len(local_cells) == expected and len(available_masks) == expected:
            local_masks.mkdir(parents=True, exist_ok=True)
            for source in available_masks:
                shutil.copy2(source, local_masks / source.name)
            return {
                "schema": "sprite_lab.realesrgan_birefnet/v1",
                "resumed_from_local_output": True,
                "source_masks": "birefnet_masks",
                "chroma_cleanup": {
                    "mode": "auto",
                    "applied_images": 0,
                    "skipped_images": expected,
                    "reason": "neutral_or_transparent_background",
                },
            }
        return None
    mask_pass.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cached_masks, mask_pass / "foreground_cleanup_masks", dirs_exist_ok=True)
    return json.loads(cached_report.read_text(encoding="utf-8"))


def _save_cached_mask_pass(mask_report: dict[str, Any], mask_pass: Path, cache_key: str) -> None:
    cached_dir = MASK_CACHE_ROOT / cache_key
    temporary = MASK_CACHE_ROOT / f".{cache_key}.tmp"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        mask_pass / "foreground_cleanup_masks",
        temporary / "foreground_cleanup_masks",
        dirs_exist_ok=True,
    )
    (temporary / "mask_report.json").write_text(
        json.dumps(mask_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if cached_dir.exists():
        shutil.rmtree(cached_dir)
    temporary.replace(cached_dir)


def _run(command: list[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} falhou:\n{completed.stdout}\n{completed.stderr}")
    lines = completed.stdout.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip().startswith("{"))
        return json.loads("\n".join(lines[start:]))
    except (StopIteration, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} não produziu JSON:\n{completed.stdout}") from error


def _build_ordered_gif(output: Path, rows: int, phases: int, fps: float) -> Path:
    direction_order = tuple(
        index + 1
        for index, _row_id in enumerate(sprite_render.DIRECTION_ROWS)
        if index < rows
    )
    frame_paths = [
        output / f"row{direction - 1}_col{phase}.png"
        for direction in direction_order
        for phase in range(phases)
    ]
    if not all(path.is_file() for path in frame_paths):
        raise RuntimeError("frames insuficientes para o GIF unificado original")
    return sprite_render._write_gif(
        frame_paths,
        output / "animation_all_directions_1-2-5-4-3-8-7-6.gif",
        fps,
    )


_LAYER_ORDER = ("character", "weapon")


def _normalize_layer_specs(
    layers: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
) -> dict[str, tuple[Path, Path]]:
    """Normalize layered inputs without changing the single-sheet contract."""
    if isinstance(layers, Mapping):
        items: dict[str, Any] = dict(layers)
    elif isinstance(layers, Sequence) and not isinstance(layers, (str, bytes)):
        items = {}
        for item in layers:
            if not isinstance(item, Mapping):
                raise ValueError("cada camada deve ser um objeto")
            layer_id = str(item.get("layer_id") or item.get("id") or "").strip()
            if not layer_id:
                raise ValueError("layer_id é obrigatório")
            items[layer_id] = item
    else:
        raise ValueError("layers deve ser um objeto ou sequência")

    if set(items) != set(_LAYER_ORDER):
        raise ValueError("layers deve conter exatamente character e weapon")

    normalized: dict[str, tuple[Path, Path]] = {}
    for layer_id in _LAYER_ORDER:
        value = items[layer_id]
        if isinstance(value, Mapping):
            generated = (
                value.get("generated_sheet")
                or value.get("source")
                or value.get("input")
            )
            structural = value.get("structural_dir") or value.get("structural")
        elif (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes))
            and len(value) == 2
        ):
            generated, structural = value
        else:
            raise ValueError(
                f"camada {layer_id} deve declarar generated_sheet e structural_dir"
            )
        if generated is None or structural is None:
            raise ValueError(
                f"camada {layer_id} deve declarar generated_sheet e structural_dir"
            )
        normalized[layer_id] = (Path(generated), Path(structural))
    return normalized


def _layer_spritesheet(layer_output: Path) -> Path:
    """Resolve the canonical final spritesheet emitted by the current pipeline."""
    candidates = (
        layer_output / "variants" / "original" / "spritesheet.png",
        layer_output / "spritesheet.png",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"spritesheet final ausente em {layer_output}")


def _layer_layout(
    report: Mapping[str, Any],
    *,
    rows: int,
    phases: int,
    source_cell: int,
    output_cell: int | None,
) -> dict[str, int]:
    """Read and validate layout metadata produced by one layer."""
    expected = {
        "rows": rows,
        "phases": phases,
        "cell_size": output_cell or source_cell * 2,
    }
    values: dict[str, Any] = {
        "rows": report.get("rows", expected["rows"]),
        "phases": report.get("phases", expected["phases"]),
        "cell_size": report.get(
            "output_cell",
            report.get("cell_size", expected["cell_size"]),
        ),
    }
    if isinstance(values["cell_size"], Sequence) and not isinstance(
        values["cell_size"], (str, bytes)
    ):
        if (
            len(values["cell_size"]) != 2
            or values["cell_size"][0] != values["cell_size"][1]
        ):
            raise ValueError("cada camada deve usar células quadradas")
        values["cell_size"] = values["cell_size"][0]
    try:
        layout = {key: int(value) for key, value in values.items()}
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata de layout da camada inválida") from exc
    if any(value <= 0 for value in layout.values()):
        raise ValueError("metadata de layout da camada inválida")
    if layout != expected:
        raise ValueError(
            "camadas devem compartilhar o layout solicitado "
            f"(esperado={expected}, recebido={layout})"
        )
    return layout


def _layer_foot_anchor(report: Mapping[str, Any], layer_id: str) -> list[float]:
    """Require a finite two-coordinate anchor from every processed layer."""
    value = report.get("foot_anchor")
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) != 2
        or any(isinstance(coordinate, bool) for coordinate in value)
    ):
        raise ValueError(
            f"foot_anchor ausente ou inválido na camada {layer_id}; "
            "são necessárias duas coordenadas numéricas"
        )
    try:
        anchor = [float(coordinate) for coordinate in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"foot_anchor inválido na camada {layer_id}; "
            "são necessárias duas coordenadas numéricas"
        ) from exc
    if not all(math.isfinite(coordinate) for coordinate in anchor):
        raise ValueError(f"foot_anchor deve conter coordenadas finitas: {layer_id}")
    return anchor


def _write_layer_metadata(
    layer_output: Path,
    report: Mapping[str, Any],
    *,
    layer_id: str,
    profile_id: str,
    foot_anchor: list[float],
) -> dict[str, Any]:
    """Annotate the root report after final resolution, atomically."""
    result = dict(report)
    result.update(
        {
            "layer_id": layer_id,
            "profile_id": profile_id,
            "holdout_stage": "pending_final_holdout",
            "foot_anchor": foot_anchor,
        }
    )
    metadata_path = layer_output / "render_metadata.json"
    sprite_render.write_json_atomic(metadata_path, result)
    return result


def process_layer_bundle(
    layers: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    output: Path,
    *,
    profile_id: str = "bicubic",
    process_fn: Callable[..., dict[str, Any]] | None = None,
    rows: int = 8,
    phases: int = 8,
    source_cell: int = 256,
    output_cell: int | None = None,
    **process_kwargs: Any,
) -> dict[str, Any]:
    """Post-process character and weapon in isolated trees and publish atomically.

    The existing :func:`process` API remains the single-input path.  This
    executor only coordinates two calls to that pipeline, enforcing one
    profile/layout and promoting the complete bundle after both calls finish.
    """
    normalized = _normalize_layer_specs(layers)
    profile_id = str(profile_id or "").strip()
    if not profile_id:
        raise ValueError("profile_id é obrigatório")
    if not isinstance(rows, int) or isinstance(rows, bool) or rows <= 0:
        raise ValueError("rows deve ser um inteiro positivo")
    if not isinstance(phases, int) or isinstance(phases, bool) or phases <= 0:
        raise ValueError("phases deve ser um inteiro positivo")
    if (
        not isinstance(source_cell, int)
        or isinstance(source_cell, bool)
        or source_cell <= 0
    ):
        raise ValueError("source_cell deve ser um inteiro positivo")
    if output_cell is not None and (
        not isinstance(output_cell, int)
        or isinstance(output_cell, bool)
        or output_cell <= 0
    ):
        raise ValueError("output_cell deve ser um inteiro positivo")
    if (
        "model_profile" in process_kwargs
        and str(process_kwargs["model_profile"]) != profile_id
    ):
        raise ValueError("model_profile deve ser igual ao profile_id comum")

    destination = Path(output).expanduser().resolve()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"saída layered já existe: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=str(destination.parent),
        )
    )
    runner = process if process_fn is None else process_fn
    expected_layout = {
        "rows": rows,
        "phases": phases,
        "cell_size": output_cell or source_cell * 2,
    }
    layer_reports: dict[str, dict[str, Any]] = {}
    try:
        for layer_id in _LAYER_ORDER:
            generated_sheet, structural_dir = normalized[layer_id]
            layer_output = staging / layer_id
            call_kwargs = dict(process_kwargs)
            call_kwargs.update(
                {
                    "rows": rows,
                    "phases": phases,
                    "source_cell": source_cell,
                    "model_profile": profile_id,
                    "layer_id": layer_id,
                }
            )
            report = runner(
                generated_sheet,
                structural_dir,
                layer_output,
                **call_kwargs,
            )
            if not isinstance(report, Mapping):
                raise ValueError(f"pipeline da camada {layer_id} não retornou relatório")
            foot_anchor = _layer_foot_anchor(report, layer_id)
            layout = _layer_layout(
                report,
                rows=rows,
                phases=phases,
                source_cell=source_cell,
                output_cell=output_cell,
            )
            spritesheet = _layer_spritesheet(layer_output)
            with Image.open(spritesheet) as opened:
                dimensions = [int(opened.width), int(opened.height)]
            expected_dimensions = [
                expected_layout["phases"] * expected_layout["cell_size"],
                expected_layout["rows"] * expected_layout["cell_size"],
            ]
            if dimensions != expected_dimensions:
                raise ValueError(
                    f"spritesheet da camada {layer_id} possui dimensões {dimensions}; "
                    f"esperado {expected_dimensions}"
                )
            layer_report = _write_layer_metadata(
                layer_output,
                report,
                layer_id=layer_id,
                profile_id=profile_id,
                foot_anchor=foot_anchor,
            )
            layer_report["layout"] = layout
            layer_report["spritesheet"] = str(spritesheet.relative_to(staging).as_posix())
            layer_report["dimensions"] = dimensions
            layer_reports[layer_id] = layer_report

        anchors = [layer_reports[layer_id]["foot_anchor"] for layer_id in _LAYER_ORDER]
        if anchors[0] != anchors[1]:
            raise ValueError("camadas devem compartilhar o mesmo foot_anchor")
        common_foot_anchor = list(anchors[0])
        bundle_report: dict[str, Any] = {
            "schema": "sprite_lab.layered_postprocess/v1",
            "status": "processed",
            "profile_id": profile_id,
            "holdout_stage": "pending_final_holdout",
            "layout": expected_layout,
            "foot_anchor": common_foot_anchor,
            "layers": {
                layer_id: {
                    "layer_id": layer_id,
                    "profile_id": profile_id,
                    "directory": layer_id,
                    "spritesheet": layer_reports[layer_id]["spritesheet"],
                    "dimensions": layer_reports[layer_id]["dimensions"],
                    "layout": layer_reports[layer_id]["layout"],
                    "foot_anchor": list(layer_reports[layer_id]["foot_anchor"]),
                    "holdout_stage": "pending_final_holdout",
                }
                for layer_id in _LAYER_ORDER
            },
        }
        manifest_path = staging / "layered_postprocess.json"
        temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
        temporary.write_text(
            json.dumps(
                bundle_report,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(manifest_path)
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    bundle_report["output"] = str(destination)
    bundle_report["manifest"] = str((destination / "layered_postprocess.json").resolve())
    return bundle_report


process_layers = process_layer_bundle


def process(
    generated_sheet: Path,
    structural_dir: Path,
    output: Path,
    *,
    rows: int = 8,
    phases: int = 8,
    source_cell: int = 256,
    fps: float = 10.0,
    foot_anchor: tuple[int, int] = (128, 220),
    realesrgan_repo: Path,
    python_executable: str,
    model_profile: str = "anime_x4plus_6b",
    layer_id: str | None = None,
    lineart_mode: str = "blender",
    device: str | None = None,
    precision: str | None = None,
    batch_size: int | None = None,
    cpu_workers: int | None = None,
    progress_callback: Callable[[str, int, int | None], None] | None = None,
) -> dict[str, Any]:
    if not generated_sheet.is_file() or not structural_dir.is_dir():
        raise FileNotFoundError("generated_sheet ou structural_dir ausente")
    device = device or os.environ.get("SPRITE_LAB_POSTPROCESS_DEVICE", "auto")
    precision = precision or os.environ.get("SPRITE_LAB_POSTPROCESS_PRECISION", "fp32")
    batch_size = runtime_batch_size() if batch_size is None else batch_size
    cpu_workers = runtime_cpu_workers() if cpu_workers is None else cpu_workers
    validate_parallelism(batch_size, cpu_workers)
    if device not in ("auto", "cpu", "cuda") or precision not in ("fp32", "fp16"):
        raise ValueError("Dispositivo/precisão inválidos")
    if lineart_mode not in ("blender", "lineart_standard", "lineart_coarse", "lineart_anime", "none"):
        raise ValueError("Modo de lineart inválido")
    output.mkdir(parents=True, exist_ok=True)
    source_render = _source_render_info(structural_dir)
    started = time.monotonic()
    mask_pass = output / "mask_pass_realesrgan_birefnet"
    mask_cache_key = _mask_cache_key(generated_sheet, rows, phases)
    if progress_callback:
        progress_callback("mask_pass_realesrgan", 8, 1800)
    mask_report = _load_cached_mask_pass(mask_pass, mask_cache_key, rows, phases)
    mask_cache_hit = mask_report is not None
    if mask_report is None:
        mask_report = _run(
            [
            python_executable,
            str(Path(__file__).with_name("realesrgan_birefnet_pipeline.py")),
            str(generated_sheet),
            str(mask_pass),
            "--device", device,
            "--precision", precision,
            "--batch-size", str(batch_size),
            "--cpu-workers", str(cpu_workers),
            "--rows",
            str(rows),
            "--phases",
            str(phases),
            "--fps",
            str(fps),
            "--realesrgan-python",
            python_executable,
            "--realesrgan-repo",
            str(realesrgan_repo),
            "--model-profile",
            "anime_x4plus_6b",
            "--birefnet-python",
            python_executable,
            "--birefnet-threshold",
            "0.50",
            "--birefnet-input-size",
            "1024",
            "--realesrgan-tile-size",
            "256",
            "--realesrgan-tile-pad",
            "32",
            "--foot-anchor",
            str(foot_anchor[0]),
            str(foot_anchor[1]),
            "--chroma-cleanup",
            # AI providers can return either chroma green or a transparent/
            # neutral matte. `auto` preserves the cleanup for green renders
            # and skips despill when the background is not a valid chroma key.
            "auto",
            "--chroma-edge-radius",
            "6",
            "--chroma-tolerance",
            "2",
            "--chroma-strength",
            "1",
            "--chroma-bleed-radius",
            "8",
            "--chroma-key-distance",
            "96",
            "--chroma-max-island-size",
            "2048",
            ],
            "passe de máscara Real-ESRGAN + BiRefNet-Lite",
        )
        MASK_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        _save_cached_mask_pass(mask_report, mask_pass, mask_cache_key)
    mask_source = mask_pass / "foreground_cleanup_masks"
    source_alpha_report = intersect_with_source_alpha(
        generated_sheet, mask_pass, mask_source, rows, phases
    )
    mask_report = {
        **mask_report,
        "cache": {"hit": mask_cache_hit, "key": mask_cache_key},
        "source_alpha_intersection": source_alpha_report,
    }
    expected_masks = list(mask_source.glob("row*_col*.png"))
    if len(expected_masks) != rows * phases:
        raise RuntimeError(
            f"passe BiRefNet produziu {len(expected_masks)} de {rows * phases} máscaras"
        )

    official = output / "variants" / "original"
    if progress_callback:
        progress_callback("quality_pass_realesrgan", 74, 360)
    pregan_command = [
            python_executable,
            str(Path(__file__).with_name("pregan_realesrgan_reuse_mask_pipeline.py")),
            str(generated_sheet),
            str(mask_source),
            str(official),
            "--device", device,
            "--precision", precision,
            "--batch-size", str(batch_size),
            "--cpu-workers", str(cpu_workers),
            "--realesrgan-repo",
            str(realesrgan_repo),
            "--model-profile",
            model_profile,
            "--rows",
            str(rows),
            "--phases",
            str(phases),
            "--fps",
            str(fps),
            "--tile-size",
            "256",
            "--tile-pad",
            "32",
            "--tolerance",
            "2",
            "--final-bleed-radius",
            "8",
            "--foot-anchor",
            str(foot_anchor[0]),
            str(foot_anchor[1]),
        ]
    if lineart_mode == "blender":
        pregan_command.extend(
            [
                "--lineart-mode",
                "blender",
                "--lineart-dir",
                str(structural_dir),
                "--lineart-strength",
                "0.85",
            ]
        )
    elif lineart_mode in ("lineart_standard", "lineart_coarse", "lineart_anime"):
        pregan_command.extend(
            [
                "--lineart-mode",
                lineart_mode,
                "--lineart-strength",
                "0.85",
            ]
        )
    else:
        pregan_command.extend(["--lineart-mode", "none"])
    official_report = _run(
        pregan_command,
        "pipeline original",
    )
    lineart_export = official_report.get("lineart_export") or {
        "enabled": False,
        "mode": lineart_mode,
        "color": "white",
        "background": "transparent",
        "composite": "separate_runtime_layer",
        "directory": None,
        "spritesheet": None,
        "gifs": {},
        "legacy_gif": None,
    }
    _build_ordered_gif(official, rows, phases, fps)
    if progress_callback:
        progress_callback("building_color_variants", 90, 240)

    variants = {"original": official}
    refine_script = Path(__file__).with_name("temporal_palette_refine.py")
    variant_specs = (
        ("frame_adjustment", 0),
        ("color_cohesion_256", 256),
        ("color_cohesion_128", 128),
    )

    def run_variant(spec: tuple[str, int]) -> tuple[str, Path]:
        name, colors = spec
        variant = output / "variants" / name
        command = [
            python_executable,
            str(refine_script),
            str(official),
            str(variant),
            "--rows",
            str(rows),
            "--phases",
            str(phases),
            "--fps",
            str(fps),
            "--outlier-distance",
            "3.25",
            "--max-shift",
            "1.5",
            "--bleed-radius",
            "8",
        ]
        if colors:
            command.extend(["--colors", str(colors)])
        _run(command, f"variante {name}")
        return name, variant

    with ThreadPoolExecutor(max_workers=min(cpu_workers, len(variant_specs))) as executor:
        completed_variants = list(executor.map(run_variant, variant_specs))
    for name, variant in completed_variants:
        variants[name] = variant
        if progress_callback:
            completed_percent = {
                "frame_adjustment": 93,
                "color_cohesion_256": 96,
                "color_cohesion_128": 99,
            }[name]
            progress_callback(
                "building_color_variants",
                completed_percent,
                max(0, 180 - completed_percent),
            )

    for name, variant in variants.items():
        if name == "original":
            continue
        # Ensure each variant exposes the same deterministic deliverables.
        ordered = variant / "animation_all_directions_1-2-5-4-3-8-7-6.gif"
        if not ordered.is_file():
            raise RuntimeError(f"GIF unificado ausente na variante {name}")
    direction_contract = _direction_contract(rows)
    runtime_contract = {
        "schema": "sprite_lab.godot_sprite_runtime/v1",
        "atlas": {
            "rows": rows,
            "columns": phases,
            "cell_size": [source_cell * 2, source_cell * 2],
            "phase_order": "columns_0_to_phases_minus_1",
        },
        "directions": direction_contract,
        "animation_source": source_render["animation_source"],
        "animation_timing": source_render["animation_timing"],
        "pivot": {
            "foot_anchor": [foot_anchor[0] * 2, foot_anchor[1] * 2],
            "normalized": [
                foot_anchor[0] / source_cell,
                foot_anchor[1] / source_cell,
            ],
        },
        "render": source_render["render_properties"],
        "background_mode": "transparent",
    }
    if lineart_export.get("enabled"):
        runtime_contract["lineart"] = {
            "texture": lineart_export.get("spritesheet"),
            "rows": rows,
            "columns": phases,
            "cell_size": [source_cell * 2, source_cell * 2],
            "phase_order": "columns_0_to_phases_minus_1",
            "color": "white",
            "background_mode": "transparent",
            "composite": "runtime_layer",
        }
    metadata = {
        "schema": "sprite_lab.gemini_sprite_postprocess/v1",
        "layer_id": layer_id,
        "profile_id": model_profile,
        "holdout_stage": "after_final_resolution",
        "generated_sheet": str(generated_sheet.resolve()),
        "structural_dir": str(structural_dir.resolve()),
        "source_render_metadata": source_render["metadata_path"],
        "source_asset_manifest": source_render["manifest_path"],
        "asset_spec": source_render["asset_spec"],
        "animation_source": source_render["animation_source"],
        "animation_timing": source_render["animation_timing"],
        "runtime_contract": runtime_contract,
        "runtime_asset_contract": _runtime_asset_contract(
            source_render["asset_spec"],
            direction_contract,
            rows=rows,
            phases=phases,
            cell_size=source_cell * 2,
            foot_anchor=[foot_anchor[0] * 2, foot_anchor[1] * 2],
            timing=source_render["animation_timing"],
            texture="variants/original/spritesheet.png",
            lineart_texture=(
                lineart_export.get("spritesheet")
                if lineart_export.get("enabled")
                else None
            ),
        ),
        "source_render_properties": source_render["render_properties"],
        "rows": rows,
        "phases": phases,
        "source_cell": source_cell,
        "output_cell": source_cell * 2,
        "fps": fps,
        "foot_anchor": [foot_anchor[0] * 2, foot_anchor[1] * 2],
        "direction_contract": direction_contract,
        "variants": {
            name: f"variants/{name}" for name in variants
        },
        "pipeline": [
            "realesrgan_2x_mask_pass",
            "birefnet_lite_512_binary_threshold_0.5",
            "foreground_chroma_cleanup_and_island_removal",
            "approved_birefnet_mask_512",
            "pregan_chroma_cleanup",
            "super_resolution_2x_quality_pass",
            "reapply_exact_approved_birefnet_mask",
            "four_output_variants",
        ],
        "structural_channels": ["beauty", "bones", "lineart"],
        "structural_postprocess": "validation_only_no_scale_transform",
        "mask_report": mask_report,
        "official_report": official_report,
        "mask_model_profile": "anime_x4plus_6b",
        "model_profile": model_profile,
        "lineart_mode": lineart_mode,
        "lineart_export": lineart_export,
        "runtime_requested": {"device": device, "precision": precision},
        "parallelism": {
            "gpu_batch_size": batch_size,
            "cpu_workers": cpu_workers,
            "variants_parallel": True,
            "variant_workers": min(cpu_workers, len(variant_specs)),
        },
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "asset_manifest": "asset_manifest.json",
    }
    variant_metadata = {
        "layer_id": layer_id,
        "profile_id": model_profile,
        "holdout_stage": "after_final_resolution",
        "asset_spec": source_render["asset_spec"],
        "animation_source": source_render["animation_source"],
        "animation_timing": source_render["animation_timing"],
        "source_render_metadata": source_render["metadata_path"],
        "direction_contract": metadata["direction_contract"],
        "runtime_contract": runtime_contract,
        "runtime_asset_contract": metadata["runtime_asset_contract"],
        "source_render_properties": source_render["render_properties"],
        "lineart_export": lineart_export,
    }
    for variant in variants.values():
        variant_path = variant / "render_metadata.json"
        variant_report = _read_json(variant_path)
        if not variant_report:
            continue
        variant_report.update(variant_metadata)
        variant_report["asset_manifest"] = "../../asset_manifest.json"
        sprite_render.write_json_atomic(variant_path, variant_report)
    (output / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_postprocess_asset_manifest(output, metadata, source_render)
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(parser)
    parser.add_argument("generated_sheet", type=Path)
    parser.add_argument("structural_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--realesrgan-repo", type=Path, required=True)
    parser.add_argument(
        "--model-profile",
        choices=tuple(huggingface_realesrgan.MODEL_PROFILES),
        default="anime_x4plus_6b",
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument(
        "--lineart-mode",
        choices=("blender", "lineart_standard", "lineart_coarse", "lineart_anime", "none"),
        default="blender",
    )
    parser.add_argument("--foot-anchor", type=int, nargs=2, default=(128, 220))
    args = parser.parse_args()
    report = process(
        args.generated_sheet,
        args.structural_dir,
        args.output,
        fps=args.fps,
        foot_anchor=tuple(args.foot_anchor),
        realesrgan_repo=args.realesrgan_repo,
        python_executable=args.python,
        model_profile=args.model_profile,
        lineart_mode=args.lineart_mode,
        device=args.device,
        precision=args.precision,
        batch_size=args.batch_size,
        cpu_workers=args.cpu_workers,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
