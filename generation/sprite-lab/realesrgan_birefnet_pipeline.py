"""Run the official 256px -> Real-ESRGAN 2x -> BiRefNet 512px pipeline."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

import chroma_despill
import huggingface_realesrgan
import sprite_render


def _run(command: list[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} falhou:\n{completed.stdout}\n{completed.stderr}")
    try:
        lines = completed.stdout.splitlines()
        json_start = next(
            index for index, line in enumerate(lines) if line.strip().startswith("{")
        )
        return json.loads("\n".join(lines[json_start:]))
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"{label} não produziu relatório JSON:\n{completed.stdout}\n{completed.stderr}"
        ) from error
    except StopIteration as error:
        raise RuntimeError(
            f"{label} não produziu relatório JSON:\n{completed.stdout}\n{completed.stderr}"
        ) from error


def _apply_chroma_cleanup(
    output: Path,
    rows: int,
    phases: int,
    mode: str,
    edge_radius: float,
    tolerance: float,
    strength: float,
    bleed_radius: int,
    key_distance: float,
    max_island_size: int,
) -> dict[str, Any]:
    """Polish generated foregrounds while keeping raw BiRefNet masks intact."""
    if mode == "none":
        return {"mode": mode, "images": rows * phases, "applied_images": 0}

    cleanup_scope = "foreground" if mode == "auto" else mode
    cleanup_masks = output / "foreground_cleanup_masks"
    cleanup_masks.mkdir(parents=True, exist_ok=True)
    reports: dict[str, dict[str, Any]] = {}
    skipped = 0
    for row in range(rows):
        for column in range(phases):
            name = f"row{row}_col{column}.png"
            path = output / name
            with Image.open(path) as opened:
                original = opened.convert("RGBA")
            try:
                result, report = chroma_despill.process_frame(
                    original,
                    edge_radius=edge_radius,
                    tolerance=tolerance,
                    strength=strength,
                    bleed_radius=bleed_radius,
                    key_color=None,
                    scope=cleanup_scope,
                    remove_islands=cleanup_scope == "foreground",
                    key_distance=key_distance,
                    max_island_size=max_island_size,
                )
            except ValueError as error:
                if mode != "auto" or "chroma estimado" not in str(error):
                    raise
                result = original.copy()
                report = {"applied": False, "reason": str(error)}
                skipped += 1
            else:
                report["applied"] = True
            result.save(path, format="PNG")
            result.getchannel("A").save(cleanup_masks / name, format="PNG")
            reports[name] = report
            original.close()
            result.close()

    return {
        "mode": mode,
        "scope": cleanup_scope,
        "images": rows * phases,
        "applied_images": rows * phases - skipped,
        "skipped_images": skipped,
        "edge_radius": edge_radius,
        "tolerance": tolerance,
        "strength": strength,
        "bleed_radius": bleed_radius,
        "key_distance": key_distance,
        "max_island_size": max_island_size,
        "remove_key_islands": cleanup_scope == "foreground",
        "reports": reports,
    }
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--phases", type=int, default=8)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--scale", type=int, choices=(2,), default=2)
    parser.add_argument(
        "--realesrgan-python", type=Path, required=True
    )
    parser.add_argument("--realesrgan-repo", type=Path, required=True)
    parser.add_argument(
        "--model-profile",
        choices=tuple(huggingface_realesrgan.MODEL_PROFILES),
        default="anime_x4plus_6b",
    )
    parser.add_argument("--birefnet-python", type=Path, required=True)
    parser.add_argument("--birefnet-model", default="ZhengPeng7/BiRefNet_lite")
    parser.add_argument(
        "--birefnet-revision",
        default="7838f1c3472f827cd8ce13ab5ccc2ce48077360f",
    )
    parser.add_argument("--birefnet-threshold", type=float, default=0.5)
    parser.add_argument("--birefnet-input-size", type=int, default=1024)
    parser.add_argument("--realesrgan-tile-size", type=int, default=256)
    parser.add_argument("--realesrgan-tile-pad", type=int, default=32)
    parser.add_argument("--foot-anchor", type=int, nargs=2, default=(128, 220))
    parser.add_argument(
        "--chroma-cleanup",
        choices=("auto", "none", "edge", "foreground"),
        default="auto",
        help="limpeza generativa; auto usa foreground somente com chroma saturado",
    )
    parser.add_argument("--chroma-edge-radius", type=float, default=6.0)
    parser.add_argument("--chroma-tolerance", type=float, default=2.0)
    parser.add_argument("--chroma-strength", type=float, default=1.0)
    parser.add_argument("--chroma-bleed-radius", type=int, default=8)
    parser.add_argument("--chroma-key-distance", type=float, default=96.0)
    parser.add_argument("--chroma-max-island-size", type=int, default=2048)
    args = parser.parse_args()

    if not args.source.is_file():
        raise FileNotFoundError(args.source)
    if not 1 <= args.rows <= 8 or args.phases < 1:
        raise ValueError("grade inválida")
    args.output.mkdir(parents=True, exist_ok=True)

    with Image.open(args.source) as opened:
        sheet = opened.convert("RGB")
    if sheet.width % args.phases or sheet.height % args.rows:
        raise ValueError("as dimensões da imagem devem ser divisíveis pela grade")
    source_cell = sheet.width // args.phases
    if source_cell != sheet.height // args.rows:
        raise ValueError("as células da spritesheet precisam ser quadradas")

    with tempfile.TemporaryDirectory(prefix="sprite-lab-esrgan-birefnet-") as temporary:
        raw_dir = Path(temporary) / "raw"
        raw_dir.mkdir()
        for row in range(args.rows):
            for column in range(args.phases):
                box = (
                    column * source_cell,
                    row * source_cell,
                    (column + 1) * source_cell,
                    (row + 1) * source_cell,
                )
                sheet.crop(box).save(raw_dir / f"row{row}_col{column}.png")

        upscaled_dir = args.output / "realesrgan_512"
        esrgan_report = _run(
            [
                str(args.realesrgan_python),
                str(Path(__file__).with_name("realesrgan_anime_scale.py")),
                str(raw_dir),
                str(upscaled_dir),
                "--realesrgan-repo",
                str(args.realesrgan_repo),
                "--model-profile",
                args.model_profile,
                "--rows",
                str(args.rows),
                "--phases",
                str(args.phases),
                "--fps",
                str(args.fps),
                "--scale",
                str(args.scale),
                "--bleed-radius",
                "0",
                "--tile-size",
                str(args.realesrgan_tile_size),
                "--tile-pad",
                str(args.realesrgan_tile_pad),
                "--alpha-filter",
                "nearest",
                "--foot-anchor",
                str(args.foot_anchor[0]),
                str(args.foot_anchor[1]),
            ],
            "Real-ESRGAN",
        )

        masks_dir = args.output / "birefnet_masks"
        birefnet_report = _run(
            [
                str(args.birefnet_python),
                str(Path(__file__).with_name("birefnet_lite_remove.py")),
                str(upscaled_dir),
                str(args.output),
                "--mask-output",
                str(masks_dir),
                "--model",
                args.birefnet_model,
                "--revision",
                args.birefnet_revision,
                "--threshold",
                str(args.birefnet_threshold),
                "--input-size",
                str(args.birefnet_input_size),
            ],
            "BiRefNet-Lite",
        )

    chroma_report = _apply_chroma_cleanup(
        args.output,
        args.rows,
        args.phases,
        args.chroma_cleanup,
        args.chroma_edge_radius,
        args.chroma_tolerance,
        args.chroma_strength,
        args.chroma_bleed_radius,
        args.chroma_key_distance,
        args.chroma_max_island_size,
    )

    output_cell = source_cell * args.scale
    sprite_render._build_sheet(args.output, args.rows, args.phases, output_cell)
    directions = sprite_render.DIRECTION_ROWS[: args.rows]
    gifs = sprite_render._build_gifs(
        args.output, args.rows, args.phases, args.fps, directions
    )
    legacy = sprite_render._build_gif(args.output, args.phases, args.fps)
    diagonal, diagonal_sequence = sprite_render._build_upscaled_diagonal_gif(
        args.output, args.rows, args.phases, args.fps
    )
    metadata = {
        "schema": "sprite_lab.realesrgan_birefnet/v1",
        "source": str(args.source.resolve()),
        "grid": [args.phases, args.rows],
        "source_cell_size": [source_cell, source_cell],
        "cell_size": [output_cell, output_cell],
        "scale_factor": args.scale,
        "fps": float(args.fps),
        "foot_anchor_source": list(args.foot_anchor),
        "foot_anchor": [value * args.scale for value in args.foot_anchor],
        "pipeline": [
            "realesrgan_2x",
            "birefnet_lite_512_binary",
            *(["foreground_chroma_cleanup", "alpha_bleed"]
              if chroma_report["applied_images"]
              else []),
        ],
        "realesrgan": esrgan_report,
        "model_profile": args.model_profile,
        "birefnet": birefnet_report,
        "chroma_cleanup": chroma_report,
        "cells": [
            f"row{row}_col{column}.png"
            for row in range(args.rows)
            for column in range(args.phases)
        ],
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
