"""Remove chroma spill from masked sprite edges and rebuild sprite artifacts."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, label

import sprite_render
from waifu2x_cunet_scale import alpha_bleed


def estimate_chroma_color(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Estimate the screen color from transparent border pixels."""
    border_rgb = np.concatenate(
        [rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]],
        axis=0,
    )
    border_alpha = np.concatenate(
        [alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]],
        axis=0,
    )
    samples = border_rgb[border_alpha == 0]
    if not len(samples):
        samples = rgb[alpha == 0]
    if not len(samples):
        raise ValueError("não há pixels transparentes para estimar o chroma key")
    return np.median(samples, axis=0).astype(np.float32)


def remove_key_islands(
    rgba: np.ndarray,
    key_color: np.ndarray,
    *,
    distance_limit: float,
    max_component_size: int,
) -> int:
    """Remove small opaque islands that are unmistakably the screen color."""
    rgb = rgba[..., :3].astype(np.float32)
    foreground = rgba[..., 3] > 0
    distance = np.sqrt(np.sum((rgb - key_color) ** 2, axis=-1))
    key_channel = int(np.argmax(key_color))
    other_channels = [channel for channel in range(3) if channel != key_channel]
    dominance = rgb[..., key_channel] - np.max(rgb[..., other_channels], axis=-1)
    candidates = foreground & (distance <= distance_limit) & (dominance >= 24.0)
    components, count = label(candidates, structure=np.ones((3, 3), dtype=np.uint8))
    if count == 0:
        return 0
    sizes = np.bincount(components.ravel())
    remove = candidates & (sizes[components] <= max_component_size)
    rgba[..., 3][remove] = 0
    return int(remove.sum())


def despill_rgba(
    image: Image.Image,
    *,
    edge_radius: float = 4.0,
    tolerance: float = 8.0,
    strength: float = 1.0,
    key_color: tuple[int, int, int] | None = None,
    scope: str = "edge",
) -> tuple[Image.Image, dict[str, Any]]:
    """Suppress the dominant chroma channel at the edge or all foreground."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3]
    foreground = alpha > 0
    if not foreground.any():
        raise ValueError("o frame não possui foreground")
    if (
        edge_radius <= 0
        or not 0.0 <= strength <= 1.0
        or tolerance < 0
        or scope not in {"edge", "foreground"}
    ):
        raise ValueError("parâmetros de despill inválidos")

    estimated = (
        np.asarray(key_color, dtype=np.float32)
        if key_color is not None
        else estimate_chroma_color(rgb, alpha)
    )
    key_channel = int(np.argmax(estimated))
    other_channels = [channel for channel in range(3) if channel != key_channel]
    key_dominance = float(
        estimated[key_channel] - np.max(estimated[other_channels])
    )
    if key_dominance < 24.0:
        raise ValueError(
            f"chroma estimado não é suficientemente saturado: {estimated.tolist()}"
        )

    if scope == "foreground":
        edge_weight = foreground.astype(np.float32)
    else:
        distance_inside = distance_transform_edt(foreground)
        edge_weight = (foreground & (distance_inside <= edge_radius)).astype(np.float32)
    neutral_limit = np.max(rgb[..., other_channels], axis=-1) + tolerance
    excess = np.maximum(rgb[..., key_channel] - neutral_limit, 0.0)
    correction = excess * edge_weight * strength
    changed = correction >= 0.5
    rgb[..., key_channel] = np.maximum(
        rgb[..., key_channel] - correction,
        0.0,
    )
    rgba[..., :3] = np.round(rgb).clip(0, 255).astype(np.uint8)
    result = Image.fromarray(rgba, mode="RGBA")
    report = {
        "estimated_key_color": [round(float(value), 3) for value in estimated],
        "dominant_channel": ("red", "green", "blue")[key_channel],
        "key_dominance": round(key_dominance, 3),
        "scope": scope,
        "changed_pixels": int(changed.sum()),
        "removed_channel_total": round(float(correction.sum()), 3),
    }
    return result, report


def process_frame(
    image: Image.Image,
    *,
    edge_radius: float,
    tolerance: float,
    strength: float,
    bleed_radius: int,
    key_color: tuple[int, int, int] | None,
    scope: str = "edge",
    remove_islands: bool = False,
    key_distance: float = 96.0,
    max_island_size: int = 2048,
) -> tuple[Image.Image, dict[str, Any]]:
    working_rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    estimated_key = (
        np.asarray(key_color, dtype=np.float32)
        if key_color is not None
        else estimate_chroma_color(working_rgba[..., :3], working_rgba[..., 3])
    )
    removed = (
        remove_key_islands(
            working_rgba,
            estimated_key,
            distance_limit=key_distance,
            max_component_size=max_island_size,
        )
        if remove_islands
        else 0
    )
    working = Image.fromarray(working_rgba, mode="RGBA")
    despilled, report = despill_rgba(
        working,
        edge_radius=edge_radius,
        tolerance=tolerance,
        strength=strength,
        key_color=key_color,
        scope=scope,
    )
    despilled_rgba = np.asarray(despilled, dtype=np.uint8).copy()
    despilled_rgba[..., 3] = np.where(despilled_rgba[..., 3] > 0, 255, 0)
    despilled = Image.fromarray(despilled_rgba, mode="RGBA")
    alpha = despilled.getchannel("A")
    result = alpha_bleed(despilled, bleed_radius)
    result.putalpha(alpha)
    report["alpha_bleed_radius"] = bleed_radius
    report["removed_key_island_pixels"] = removed
    working.close()
    return result, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--phases", type=int, default=8)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--edge-radius", type=float, default=4.0)
    parser.add_argument("--tolerance", type=float, default=8.0)
    parser.add_argument("--strength", type=float, default=1.0)
    parser.add_argument("--bleed-radius", type=int, default=8)
    parser.add_argument("--key-color", type=int, nargs=3)
    parser.add_argument("--scope", choices=("edge", "foreground"), default="edge")
    parser.add_argument("--remove-key-islands", action="store_true")
    parser.add_argument("--key-distance", type=float, default=96.0)
    parser.add_argument("--max-island-size", type=int, default=2048)
    args = parser.parse_args()

    if not args.source.is_dir():
        raise FileNotFoundError(args.source)
    if (
        args.rows < 1
        or args.phases < 1
        or args.bleed_radius < 0
        or args.key_distance < 0
        or args.max_island_size < 1
    ):
        raise ValueError("grade ou bleed-radius inválido")
    if args.key_color and any(not 0 <= value <= 255 for value in args.key_color):
        raise ValueError("key-color deve usar canais entre 0 e 255")
    args.output.mkdir(parents=True, exist_ok=True)

    source_metadata_path = args.source / "render_metadata.json"
    source_metadata = (
        json.loads(source_metadata_path.read_text(encoding="utf-8"))
        if source_metadata_path.is_file()
        else {}
    )
    inputs = [
        args.source / f"row{row}_col{column}.png"
        for row in range(args.rows)
        for column in range(args.phases)
    ]
    started = time.monotonic()
    frame_reports: dict[str, dict[str, Any]] = {}
    frame_size: tuple[int, int] | None = None
    for source in inputs:
        if not source.is_file():
            raise FileNotFoundError(source)
        with Image.open(source) as opened:
            original = opened.convert("RGBA")
        if frame_size is None:
            frame_size = original.size
        if original.size != frame_size:
            raise ValueError("todas as células precisam ter a mesma dimensão")
        result, report = process_frame(
            original,
            edge_radius=args.edge_radius,
            tolerance=args.tolerance,
            strength=args.strength,
            bleed_radius=args.bleed_radius,
            key_color=tuple(args.key_color) if args.key_color else None,
            scope=args.scope,
            remove_islands=args.remove_key_islands,
            key_distance=args.key_distance,
            max_island_size=args.max_island_size,
        )
        result_alpha = np.asarray(result.getchannel("A"), dtype=np.uint8)
        if not set(np.unique(result_alpha)).issubset({0, 255}):
            raise RuntimeError(f"o alpha final não é binário em {source.name}")
        result.save(args.output / source.name, format="PNG")
        frame_reports[source.name] = report
        original.close()
        result.close()

    assert frame_size is not None
    if frame_size[0] != frame_size[1]:
        raise ValueError("as células precisam ser quadradas")
    sprite_render._build_sheet(args.output, args.rows, args.phases, frame_size[0])
    directions = sprite_render.DIRECTION_ROWS[: args.rows]
    gifs = sprite_render._build_gifs(
        args.output, args.rows, args.phases, args.fps, directions
    )
    legacy = sprite_render._build_gif(args.output, args.phases, args.fps)
    diagonal, diagonal_sequence = sprite_render._build_upscaled_diagonal_gif(
        args.output, args.rows, args.phases, args.fps
    )
    changed_pixels = sum(
        int(report["changed_pixels"]) for report in frame_reports.values()
    )
    metadata = {
        "schema": "sprite_lab.chroma_despill/v1",
        "source": str(args.source.resolve()),
        "source_schema": source_metadata.get("schema"),
        "grid": [args.phases, args.rows],
        "cell_size": list(frame_size),
        "fps": float(args.fps),
        "foot_anchor": source_metadata.get("foot_anchor"),
        "pipeline": [
            *source_metadata.get("pipeline", []),
            "foreground_chroma_cleanup"
            if args.scope == "foreground"
            else "edge_chroma_despill",
            "alpha_bleed",
        ],
        "despill": {
            "key_color": list(args.key_color) if args.key_color else "auto_per_frame",
            "edge_radius": args.edge_radius,
            "edge_profile": "hard_inner_band" if args.scope == "edge" else "all_foreground",
            "tolerance": args.tolerance,
            "strength": args.strength,
            "alpha_bleed_radius": args.bleed_radius,
            "remove_key_islands": args.remove_key_islands,
            "key_distance": args.key_distance,
            "max_island_size": args.max_island_size,
            "alpha_preserved": not args.remove_key_islands,
            "changed_pixels": changed_pixels,
            "removed_key_island_pixels": sum(
                int(report["removed_key_island_pixels"])
                for report in frame_reports.values()
            ),
            "images": len(inputs),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        },
        "frame_reports": frame_reports,
        "cells": [source.name for source in inputs],
        "spritesheet": "spritesheet.png",
        "gifs": {direction: path.name for direction, path in gifs.items()},
        "legacy_gif": legacy.name if legacy else None,
        "diagonal_gif": diagonal.name if diagonal else None,
        "diagonal_sequence": diagonal_sequence,
    }
    sprite_render.write_json_atomic(args.output / "render_metadata.json", metadata)
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
