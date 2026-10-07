"""Upscale segmented sprite cells with a Real-ESRGAN model from Hugging Face."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

import huggingface_realesrgan
from waifu2x_cunet_scale import alpha_bleed
from postprocess_runtime import (
    add_runtime_arguments,
    resolve_device,
    validate_parallelism,
)


def _load_realesrgan(profile_id: str, tile_size: int, tile_pad: int, device="auto", precision="fp32"):
    """Keep the historical entry point while supporting multiple architectures."""
    if huggingface_realesrgan.profile(profile_id)["architecture"] == "traditional":
        return None
    from upscale_backend import Upscaler
    return Upscaler(profile_id, tile_size, tile_pad, device, precision)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(parser)
    parser.add_argument("source", type=Path)
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
    parser.add_argument("--scale", type=int, choices=(2,), default=2)
    parser.add_argument("--bleed-radius", type=int, default=8)
    parser.add_argument("--tile-size", type=int, default=256)
    parser.add_argument("--tile-pad", type=int, default=32)
    parser.add_argument(
        "--alpha-filter",
        choices=("nearest", "lanczos"),
        default="lanczos",
        help="filtro da máscara original ao ampliar (lanczos suaviza o serrilhado)",
    )
    parser.add_argument("--foot-anchor", type=int, nargs=2, default=(128, 220))
    args = parser.parse_args()

    if not args.source.is_dir():
        raise FileNotFoundError(args.source)
    if args.bleed_radius < 0 or args.tile_size < 32 or args.tile_pad < 0:
        raise ValueError("parâmetros de tile/bleeding inválidos")
    validate_parallelism(args.batch_size, args.cpu_workers)
    args.output.mkdir(parents=True, exist_ok=True)
    bleed_output = args.output / "alpha_bleed"
    bleed_output.mkdir(parents=True, exist_ok=True)
    selected_profile = huggingface_realesrgan.profile(args.model_profile)
    device = "cpu" if selected_profile["architecture"] == "traditional" else resolve_device(args.device)
    upsampler = _load_realesrgan(args.model_profile, args.tile_size, args.tile_pad, device, args.precision)
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()

    import sprite_render  # noqa: PLC0415

    inputs = [
        args.source / f"row{row}_col{column}.png"
        for row in range(args.rows)
        for column in range(args.phases)
    ]
    started = time.monotonic()
    source_size: tuple[int, int] | None = None
    effective_batch_size = 1 if upsampler is None else args.batch_size
    for batch_start in range(0, len(inputs), effective_batch_size):
        batch_sources = inputs[batch_start:batch_start + effective_batch_size]
        originals: list[Image.Image] = []
        original_alphas: list[Image.Image] = []
        prepared_images: list[Image.Image] = []
        bgr_images: list[np.ndarray] = []
        try:
            for source in batch_sources:
                if not source.is_file():
                    raise FileNotFoundError(source)
                with Image.open(source) as opened:
                    original = opened.convert("RGBA")
                if source_size is None:
                    source_size = original.size
                if original.size != source_size:
                    raise ValueError("todas as células precisam ter a mesma dimensão")
                original_alpha = original.getchannel("A")
                prepared = alpha_bleed(original, args.bleed_radius)
                prepared.save(bleed_output / source.name, format="PNG")
                rgb = np.asarray(prepared.convert("RGB"), dtype=np.uint8)
                originals.append(original)
                original_alphas.append(original_alpha)
                prepared_images.append(prepared)
                bgr_images.append(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))

            if selected_profile["architecture"] == "traditional":
                output_bgr_images = [
                    cv2.resize(
                        bgr,
                        (original.width * args.scale, original.height * args.scale),
                        interpolation=cv2.INTER_CUBIC,
                    )
                    for bgr, original in zip(bgr_images, originals)
                ]
            else:
                output_bgr_batch, _ = upsampler.enhance_batch(
                    bgr_images,
                    outscale=float(args.scale),
                )
                output_bgr_images = list(output_bgr_batch)

            for source, original, original_alpha, output_bgr in zip(
                batch_sources, originals, original_alphas, output_bgr_images
            ):
                output_rgb = cv2.cvtColor(output_bgr, cv2.COLOR_BGR2RGB)
                result = Image.fromarray(output_rgb, mode="RGB").convert("RGBA")
                expected_size = (original.width * args.scale, original.height * args.scale)
                if result.size != expected_size:
                    raise RuntimeError(
                        f"Real-ESRGAN produziu {result.size}, esperado {expected_size}"
                    )
                filter_mode = (
                    Image.Resampling.NEAREST
                    if args.alpha_filter == "nearest"
                    else Image.Resampling.LANCZOS
                )
                result.putalpha(original_alpha.resize(expected_size, filter_mode))
                result.save(args.output / source.name, format="PNG")
                result.close()
        finally:
            for original_alpha in original_alphas:
                original_alpha.close()
            for original in originals:
                original.close()
            for prepared in prepared_images:
                prepared.close()

    assert source_size is not None
    output_size = source_size[0] * args.scale
    sprite_render._build_sheet(args.output, args.rows, args.phases, output_size)
    directions = sprite_render.DIRECTION_ROWS[: args.rows]
    gifs = sprite_render._build_gifs(
        args.output, args.rows, args.phases, args.fps, directions
    )
    legacy = sprite_render._build_gif(args.output, args.phases, args.fps)
    diagonal, diagonal_sequence = sprite_render._build_upscaled_diagonal_gif(
        args.output, args.rows, args.phases, args.fps
    )
    metadata = {
        "schema": "sprite_lab.realesrgan_scale/v1",
        "source": str(args.source.resolve()),
        "grid": [args.phases, args.rows],
        "source_cell_size": list(source_size),
        "cell_size": [output_size, output_size],
        "scale_factor": args.scale,
        "fps": float(args.fps),
        "foot_anchor_source": list(args.foot_anchor),
        "foot_anchor": [value * args.scale for value in args.foot_anchor],
        "alpha_bleed_radius": args.bleed_radius,
        "alpha_filter": args.alpha_filter,
        "mask": "source_alpha_resized",
        "rgb_resize_filter": "opencv_inter_cubic" if selected_profile["architecture"] == "traditional" else (
            "opencv_lanczos4" if selected_profile["network_scale"] != args.scale else "native_model_scale"),
        "realesrgan": {
            "implementation": "spandrel" if upsampler else "opencv",
            "model_profile": args.model_profile,
            "model": selected_profile.get("repo_id", selected_profile.get("url", "opencv.INTER_CUBIC")),
            "network_scale": selected_profile["network_scale"],
            "output_scale": args.scale,
            "device": device,
            "precision": args.precision if upsampler else "uint8",
            "weight_sha256": upsampler.sha256 if upsampler else None,
            "peak_vram_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else 0,
            "tile_size": args.tile_size,
            "tile_pad": args.tile_pad,
            "tiling": "full_frame_global_context" if selected_profile["architecture"] == "realcugan" else "padded_tiles",
            "images": len(inputs),
            "batch_size": effective_batch_size,
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
