"""Preclean chroma before Real-ESRGAN and reuse an approved 512px alpha mask."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

import chroma_despill
import huggingface_realesrgan
import sprite_render
from waifu2x_cunet_scale import alpha_bleed


def _run(command: list[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} falhou:\n{completed.stdout}\n{completed.stderr}")
    lines = completed.stdout.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.startswith("{"))
        return json.loads("\n".join(lines[start:]))
    except (StopIteration, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} não produziu JSON:\n{completed.stdout}") from error


def _mask_from_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    if image.mode in {"RGBA", "LA"}:
        mask = image.getchannel("A")
    else:
        mask = image.convert("L")
    return mask.resize(size, Image.Resampling.NEAREST)


def preclean_cell(
    image: Image.Image,
    mask: Image.Image,
    *,
    tolerance: float = 2.0,
    key_color: tuple[int, int, int] | None = None,
) -> tuple[Image.Image, dict[str, Any]]:
    """Remove screen spill and fill every transparent RGB pixel before GAN."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    alpha = np.asarray(mask.convert("L"), dtype=np.uint8)
    alpha = np.where(alpha >= 128, 255, 0).astype(np.uint8)
    rgba = Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA")
    estimated_key = (
        np.asarray(key_color, dtype=np.float32)
        if key_color is not None
        else chroma_despill.estimate_chroma_color(rgb, alpha)
    )
    key_dominance = float(
        estimated_key[int(np.argmax(estimated_key))]
        - np.max(np.delete(estimated_key, int(np.argmax(estimated_key))))
    )
    if key_dominance < 24.0:
        # A black/neutral background is already clean; it is not a chroma key.
        # Still bleed foreground RGB into transparent pixels so the GAN never
        # receives black matte pixels around the approved binary mask.
        result = alpha_bleed(rgba, max(rgba.size) * 2)
        result.putalpha(Image.fromarray(alpha, mode="L"))
        report = {
            "estimated_key_color": [round(float(value), 3) for value in estimated_key],
            "key_dominance": round(key_dominance, 3),
            "despill": "skipped_neutral_background",
            "changed_pixels": 0,
            "removed_channel_total": 0.0,
            "alpha_bleed_radius": max(rgba.size) * 2,
            "removed_key_island_pixels": 0,
            "transparent_rgb_fill": "nearest_foreground_full_canvas",
        }
        rgba.close()
        return result, report
    result, report = chroma_despill.process_frame(
        rgba,
        edge_radius=max(rgba.size),
        tolerance=tolerance,
        strength=1.0,
        bleed_radius=max(rgba.size) * 2,
        key_color=key_color,
        scope="foreground",
        remove_islands=False,
        key_distance=96.0,
        max_island_size=2048,
    )
    report["transparent_rgb_fill"] = "nearest_foreground_full_canvas"
    return result, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("mask_source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--realesrgan-repo", type=Path, required=True)
    parser.add_argument(
        "--model-profile",
        choices=tuple(huggingface_realesrgan.MODEL_PROFILES),
        default="anime_x4plus_6b",
    )
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--phases", type=int, default=8)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--tile-pad", type=int, default=32)
    parser.add_argument("--tolerance", type=float, default=2.0)
    parser.add_argument("--key-color", type=int, nargs=3)
    parser.add_argument("--final-bleed-radius", type=int, default=8)
    parser.add_argument("--foot-anchor", type=int, nargs=2, default=(128, 220))
    args = parser.parse_args()

    if not args.source.is_file() or not args.mask_source.is_dir():
        raise FileNotFoundError("source ou mask_source ausente")
    args.output.mkdir(parents=True, exist_ok=True)
    preclean_dir = args.output / "preclean_256"
    upscaled_dir = args.output / "realesrgan_512"
    preclean_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(args.source) as opened:
        sheet = opened.convert("RGB")
    if sheet.width % args.phases or sheet.height % args.rows:
        raise ValueError("spritesheet incompatível com a grade")
    cell_size = (sheet.width // args.phases, sheet.height // args.rows)
    if cell_size[0] != cell_size[1]:
        raise ValueError("as células precisam ser quadradas")

    started = time.monotonic()
    preclean_reports: dict[str, dict[str, Any]] = {}
    for row in range(args.rows):
        for column in range(args.phases):
            name = f"row{row}_col{column}.png"
            box = (
                column * cell_size[0],
                row * cell_size[1],
                (column + 1) * cell_size[0],
                (row + 1) * cell_size[1],
            )
            source_cell = sheet.crop(box)
            with Image.open(args.mask_source / name) as opened_mask:
                mask = _mask_from_image(opened_mask, cell_size)
            precleaned, report = preclean_cell(
                source_cell,
                mask,
                tolerance=args.tolerance,
                key_color=tuple(args.key_color) if args.key_color else None,
            )
            precleaned.save(preclean_dir / name, format="PNG")
            preclean_reports[name] = report
            source_cell.close()
            mask.close()
            precleaned.close()

    realesrgan_report = _run(
        [
            sys.executable,
            str(Path(__file__).with_name("realesrgan_anime_scale.py")),
            str(preclean_dir),
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
            "2",
            "--bleed-radius",
            "0",
            "--tile-size",
            str(args.tile_size),
            "--tile-pad",
            str(args.tile_pad),
            "--alpha-filter",
            "nearest",
            "--foot-anchor",
            str(args.foot_anchor[0]),
            str(args.foot_anchor[1]),
        ],
        "Real-ESRGAN pré-limpo",
    )

    final_size = (cell_size[0] * 2, cell_size[1] * 2)
    for row in range(args.rows):
        for column in range(args.phases):
            name = f"row{row}_col{column}.png"
            with Image.open(upscaled_dir / name) as opened:
                frame = opened.convert("RGBA")
            with Image.open(args.mask_source / name) as opened_mask:
                final_alpha = _mask_from_image(opened_mask, final_size)
            frame.putalpha(final_alpha)
            result = alpha_bleed(frame, args.final_bleed_radius)
            result.putalpha(final_alpha)
            result.save(args.output / name, format="PNG")
            frame.close()
            final_alpha.close()
            result.close()

    sprite_render._build_sheet(args.output, args.rows, args.phases, final_size[0])
    directions = sprite_render.DIRECTION_ROWS[: args.rows]
    gifs = sprite_render._build_gifs(
        args.output, args.rows, args.phases, args.fps, directions
    )
    legacy = sprite_render._build_gif(args.output, args.phases, args.fps)
    diagonal, diagonal_sequence = sprite_render._build_upscaled_diagonal_gif(
        args.output, args.rows, args.phases, args.fps
    )
    metadata = {
        "schema": "sprite_lab.pregan_realesrgan_reuse_mask/v1",
        "source": str(args.source.resolve()),
        "mask_source": str(args.mask_source.resolve()),
        "grid": [args.phases, args.rows],
        "source_cell_size": list(cell_size),
        "cell_size": list(final_size),
        "fps": float(args.fps),
        "foot_anchor_source": list(args.foot_anchor),
        "foot_anchor": [value * 2 for value in args.foot_anchor],
        "pipeline": [
            "reuse_approved_512_mask",
            "pregan_foreground_chroma_cleanup",
            "full_canvas_alpha_bleed",
            "realesrgan_2x",
            "reapply_approved_512_mask",
            "alpha_bleed",
        ],
        "preclean": {
            "tolerance": args.tolerance,
            "reports": preclean_reports,
        },
        "realesrgan": realesrgan_report,
        "model_profile": args.model_profile,
        "final_bleed_radius": args.final_bleed_radius,
        "images": args.rows * args.phases,
        "elapsed_seconds": round(time.monotonic() - started, 3),
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
