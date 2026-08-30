"""Upscale segmented sprite cells with a Real-ESRGAN model from Hugging Face."""
from __future__ import annotations

import argparse
import json
import sys
import time
import types
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

import huggingface_realesrgan
from waifu2x_cunet_scale import alpha_bleed


def _load_realesrgan(profile_id: str, tile_size: int, tile_pad: int):
    """Load a selected Hub checkpoint with the local torchvision compatibility shim."""
    import torchvision.transforms.functional as functional

    compat = types.ModuleType("torchvision.transforms.functional_tensor")
    compat.rgb_to_grayscale = functional.rgb_to_grayscale
    sys.modules["torchvision.transforms.functional_tensor"] = compat
    from basicsr.archs.rrdbnet_arch import RRDBNet  # noqa: PLC0415
    from realesrgan import RealESRGANer  # noqa: PLC0415
    selected = huggingface_realesrgan.profile(profile_id)
    if selected["architecture"] == "traditional":
        return None
    model_path = huggingface_realesrgan.download_weight(profile_id)
    if selected["architecture"] == "rrdb":
        model = RRDBNet(
            num_in_ch=3,
            num_out_ch=3,
            num_feat=64,
            num_block=int(selected["num_block"]),
            num_grow_ch=32,
            scale=int(selected["network_scale"]),
        )
    else:
        from realesrgan.archs.srvgg_arch import SRVGGNetCompact  # noqa: PLC0415
        from safetensors.torch import load_file  # noqa: PLC0415

        model = SRVGGNetCompact(
            num_in_ch=3,
            num_out_ch=3,
            num_feat=64,
            num_conv=int(selected["num_conv"]),
            upscale=int(selected["network_scale"]),
            act_type="prelu",
        )
        state = load_file(str(model_path), device="cpu")
        state = state.get("params", state)
        converted_dir = huggingface_realesrgan.HF_CACHE_DIR / "converted"
        converted_dir.mkdir(parents=True, exist_ok=True)
        converted_path = converted_dir / f"{profile_id}.pth"
        if not converted_path.is_file():
            torch.save({"params": state}, converted_path)
        model_path = converted_path
    return RealESRGANer(
        scale=int(selected["network_scale"]),
        model_path=str(model_path),
        model=model,
        tile=tile_size,
        tile_pad=tile_pad,
        pre_pad=0,
        half=False,
        device=torch.device("cpu"),
        gpu_id=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
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
    args.output.mkdir(parents=True, exist_ok=True)
    bleed_output = args.output / "alpha_bleed"
    bleed_output.mkdir(parents=True, exist_ok=True)
    selected_profile = huggingface_realesrgan.profile(args.model_profile)
    upsampler = _load_realesrgan(args.model_profile, args.tile_size, args.tile_pad)

    import sprite_render  # noqa: PLC0415

    inputs = [
        args.source / f"row{row}_col{column}.png"
        for row in range(args.rows)
        for column in range(args.phases)
    ]
    started = time.monotonic()
    source_size: tuple[int, int] | None = None
    for source in inputs:
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
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if selected_profile["architecture"] == "traditional":
            output_bgr = cv2.resize(
                bgr,
                (original.width * args.scale, original.height * args.scale),
                interpolation=cv2.INTER_CUBIC,
            )
        else:
            output_bgr, _ = upsampler.enhance(bgr, outscale=float(args.scale))
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
        original.close()
        original_alpha.close()
        prepared.close()
        result.close()

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
        "rgb_resize_filter": "opencv_inter_cubic" if selected_profile["architecture"] == "traditional" else "realesrgan_internal_lanczos4",
        "realesrgan": {
            "implementation": "xinntao/Real-ESRGAN",
            "model_profile": args.model_profile,
            "model": selected_profile.get("repo_id", "opencv.INTER_CUBIC"),
            "network_scale": 4,
            "output_scale": args.scale,
            "device": "cpu",
            "precision": "fp32",
            "tile_size": args.tile_size,
            "tile_pad": args.tile_pad,
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
