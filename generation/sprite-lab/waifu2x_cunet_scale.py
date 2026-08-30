"""Upscale RGBA sprite cells with Waifu2x CUNet while locking their mask."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt


def alpha_bleed(image: Image.Image, radius: int) -> Image.Image:
    """Propagate edge RGB into transparent pixels without changing their alpha."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    opaque = rgba[..., 3] > 0
    if radius <= 0 or not opaque.any():
        return Image.fromarray(rgba, mode="RGBA")
    distances, indices = distance_transform_edt(~opaque, return_indices=True)
    bleeding = (~opaque) & (distances <= radius)
    source_y = indices[0][bleeding]
    source_x = indices[1][bleeding]
    rgba[bleeding, :3] = rgba[source_y, source_x, :3]
    rgba[bleeding, 3] = 0
    return Image.fromarray(rgba, mode="RGBA")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--nunif-repo", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--phases", type=int, default=8)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--scale", type=int, choices=(2,), default=2)
    parser.add_argument("--bleed-radius", type=int, default=8)
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--foot-anchor", type=int, nargs=2, default=(128, 220))
    args = parser.parse_args()

    if not args.source.is_dir():
        raise FileNotFoundError(args.source)
    if args.bleed_radius < 0:
        raise ValueError("bleed-radius deve ser não negativo")
    sys.path.insert(0, str(args.nunif_repo.resolve()))
    from waifu2x.hub import waifu2x  # noqa: PLC0415
    import sprite_render  # noqa: PLC0415

    model = waifu2x(
        model_type="cunet/art",
        method="scale",
        noise_level=-1,
        device_ids=[-1],
        tile_size=args.tile_size,
        batch_size=1,
        amp=False,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    bleed_output = args.output / "alpha_bleed"
    bleed_output.mkdir(parents=True, exist_ok=True)
    inputs = [
        args.source / f"row{row}_col{column}.png"
        for row in range(args.rows)
        for column in range(args.phases)
    ]
    started = time.monotonic()
    for source in inputs:
        if not source.is_file():
            raise FileNotFoundError(source)
        with Image.open(source) as opened:
            original = opened.convert("RGBA")
        original_alpha = original.getchannel("A")
        prepared = alpha_bleed(original, args.bleed_radius)
        prepared.save(bleed_output / source.name, format="PNG")
        result = model.infer(prepared)
        expected_size = (original.width * args.scale, original.height * args.scale)
        if result.size != expected_size:
            raise RuntimeError(
                f"Waifu2x produziu {result.size}, esperado {expected_size} em {source.name}"
            )
        locked_alpha = original_alpha.resize(expected_size, Image.Resampling.NEAREST)
        result = result.convert("RGBA")
        result.putalpha(locked_alpha)
        result.save(args.output / source.name, format="PNG")
        original.close()
        original_alpha.close()
        prepared.close()
        result.close()

    output_resolution = 256 * args.scale
    sprite_render._build_sheet(args.output, args.rows, args.phases, output_resolution)
    directions = sprite_render.DIRECTION_ROWS[: args.rows]
    gifs = sprite_render._build_gifs(
        args.output, args.rows, args.phases, args.fps, directions
    )
    legacy = sprite_render._build_gif(args.output, args.phases, args.fps)
    diagonal, diagonal_sequence = sprite_render._build_upscaled_diagonal_gif(
        args.output, args.rows, args.phases, args.fps
    )
    metadata = {
        "schema": "sprite_lab.waifu2x_scale/v1",
        "source": str(args.source.resolve()),
        "grid": [args.phases, args.rows],
        "source_cell_size": [256, 256],
        "cell_size": [output_resolution, output_resolution],
        "scale_factor": args.scale,
        "fps": float(args.fps),
        "foot_anchor_source": list(args.foot_anchor),
        "foot_anchor": [value * args.scale for value in args.foot_anchor],
        "alpha_bleed_radius": args.bleed_radius,
        "mask": "source_alpha_resized_nearest_neighbor",
        "resize_filter": "nearest-neighbor",
        "waifu2x": {
            "implementation": "nagadomi/nunif",
            "model": "cunet/art",
            "method": "scale",
            "noise_level": None,
            "device": "cpu",
            "precision": "fp32",
            "tile_size": args.tile_size,
            "images": len(inputs),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        },
        "cells": [path.name for path in inputs],
        "spritesheet": "spritesheet.png",
        "gifs": {direction: path.name for direction, path in gifs.items()},
        "legacy_gif": legacy.name if legacy else None,
        "diagonal_gif": diagonal.name if diagonal else None,
        "diagonal_sequence": diagonal_sequence,
    }
    (args.output / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
