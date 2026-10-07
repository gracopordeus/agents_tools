"""Preclean chroma before Real-ESRGAN and reuse an approved 512px alpha mask."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

import chroma_despill
import huggingface_realesrgan
from postprocess_runtime import add_runtime_arguments, resolve_device, validate_parallelism
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


def build_lineart_layer(image: Image.Image, lineart: Image.Image, strength: float) -> Image.Image:
    """Build a white, transparent lineart layer without altering the beauty frame."""
    if not 0.0 <= strength <= 1.0:
        raise ValueError("lineart-strength deve estar entre 0 e 1")
    result = image.convert("RGBA")
    if strength == 0:
        result.putdata([(255, 255, 255, 0)] * (result.width * result.height))
        return result
    resized = lineart.convert("RGBA").resize(result.size, Image.Resampling.LANCZOS)
    line_alpha = np.asarray(resized.getchannel("A"), dtype=np.float32) * strength
    output_alpha = np.asarray(result.getchannel("A"), dtype=np.float32)
    mask = Image.fromarray(np.minimum(line_alpha, output_alpha).astype(np.uint8), mode="L")
    layer = Image.new("RGBA", result.size, (255, 255, 255, 0))
    layer.putalpha(mask)
    resized.close()
    mask.close()
    result.close()
    return layer


def _detector_output_as_lineart(image: Image.Image) -> Image.Image:
    """Convert a detector's grayscale map to transparent black linework."""
    grayscale = np.asarray(image.convert("L"), dtype=np.uint8)
    # ControlNet annotators are not all consistent about polarity. Their
    # conditioning convention is normalized here to bright lines on black.
    if float(grayscale.mean()) > 127.0:
        grayscale = 255 - grayscale
    alpha = Image.fromarray(grayscale, mode="L")
    result = Image.new("RGBA", image.size, (0, 0, 0, 0))
    result.putalpha(alpha)
    alpha.close()
    return result


def _black_composite(image: Image.Image) -> Image.Image:
    """Make a detector input without leaking transparent RGB into the map."""
    background = Image.new("RGBA", image.size, (0, 0, 0, 255))
    background.alpha_composite(image.convert("RGBA"))
    return background.convert("RGB")


def _build_ordered_gif(output: Path, rows: int, phases: int, fps: float) -> Path:
    """Build the runtime direction-order GIF for an exported layer."""
    frame_paths = [
        output / f"row{row}_col{phase}.png"
        for row in range(rows)
        for phase in range(phases)
    ]
    if not all(path.is_file() for path in frame_paths):
        raise RuntimeError("frames insuficientes para o GIF unificado da camada")
    return sprite_render._write_gif(
        frame_paths,
        output / "animation_all_directions_1-2-5-4-3-8-7-6.gif",
        fps,
    )


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


def _preclean_cell_task(
    task: tuple[str, Image.Image, Image.Image, Path, float, tuple[int, int, int] | None],
) -> tuple[str, dict[str, Any]]:
    name, source_cell, mask, output, tolerance, key_color = task
    try:
        precleaned, report = preclean_cell(
            source_cell,
            mask,
            tolerance=tolerance,
            key_color=key_color,
        )
        precleaned.save(output / name, format="PNG")
        precleaned.close()
        return name, report
    finally:
        source_cell.close()
        mask.close()


def _write_final_cell_task(
    task: tuple[
        Path,
        Path,
        Path,
        Path | None,
        str,
        tuple[int, int],
        int,
        Path | None,
        str,
        float,
    ],
) -> None:
    (
        upscaled_dir,
        mask_source,
        output,
        lineart_output,
        name,
        final_size,
        final_bleed_radius,
        lineart_dir,
        lineart_mode,
        lineart_strength,
    ) = task
    with Image.open(upscaled_dir / name) as opened:
        frame = opened.convert("RGBA")
    with Image.open(mask_source / name) as opened_mask:
        final_alpha = _mask_from_image(opened_mask, final_size)
    frame.putalpha(final_alpha)
    result = alpha_bleed(frame, final_bleed_radius)
    result.putalpha(final_alpha)
    lineart_layer = None
    try:
        if lineart_mode == "blender" and lineart_dir is not None:
            candidate = lineart_dir / "lineart" / name
            if not candidate.is_file():
                candidate = lineart_dir / name
            if not candidate.is_file():
                raise FileNotFoundError(f"lineart ausente: {name}")
            with Image.open(candidate) as opened_lineart:
                lineart_layer = build_lineart_layer(
                    result,
                    opened_lineart,
                    lineart_strength,
                )
        result.save(output / name, format="PNG")
        if lineart_layer is not None and lineart_output is not None:
            lineart_layer.save(lineart_output / name, format="PNG")
    finally:
        if lineart_layer is not None:
            lineart_layer.close()
        frame.close()
        final_alpha.close()
        result.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(parser)
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
    parser.add_argument("--lineart-dir", type=Path,
                        help="raiz com lineart/row*_col*.png; omitido desativa aplicação")
    parser.add_argument(
        "--lineart-mode",
        choices=("blender", "lineart_standard", "lineart_coarse", "lineart_anime", "none"),
        default="blender",
        help="fonte do lineart final: Blender, detector ControlNet por frame ou desligado",
    )
    parser.add_argument("--lineart-strength", type=float, default=0.85)
    parser.add_argument("--foot-anchor", type=int, nargs=2, default=(128, 220))
    args = parser.parse_args()

    if not args.source.is_file() or not args.mask_source.is_dir():
        raise FileNotFoundError("source ou mask_source ausente")
    if not 0.0 <= args.lineart_strength <= 1.0:
        raise ValueError("lineart-strength deve estar entre 0 e 1")
    validate_parallelism(args.batch_size, args.cpu_workers)
    if args.lineart_dir is not None and not args.lineart_dir.is_dir():
        raise FileNotFoundError("lineart-dir ausente")
    if args.lineart_mode == "blender" and args.lineart_dir is None:
        # Keep direct CLI compatibility: no structural directory means no
        # Blender overlay, while the explicit detector mode remains strict.
        lineart_applied = False
    else:
        lineart_applied = args.lineart_mode != "none"
    lineart_detector = None
    lineart_device = None
    if args.lineart_mode in ("lineart_standard", "lineart_coarse", "lineart_anime"):
        if args.lineart_mode in ("lineart_standard", "lineart_coarse"):
            from controlnet_aux import LineartDetector

            detector_class = LineartDetector
        else:
            from controlnet_aux import LineartAnimeDetector

            detector_class = LineartAnimeDetector

        lineart_device = resolve_device(args.device)
        lineart_detector = detector_class.from_pretrained("lllyasviel/Annotators").to(lineart_device)
    args.output.mkdir(parents=True, exist_ok=True)
    preclean_dir = args.output / "preclean_256"
    upscaled_dir = args.output / "realesrgan_512"
    lineart_output = args.output / "lineart" if lineart_applied else None
    preclean_dir.mkdir(parents=True, exist_ok=True)
    if lineart_output is not None:
        lineart_output.mkdir(parents=True, exist_ok=True)

    with Image.open(args.source) as opened:
        sheet = opened.convert("RGB")
    if sheet.width % args.phases or sheet.height % args.rows:
        raise ValueError("spritesheet incompatível com a grade")
    cell_size = (sheet.width // args.phases, sheet.height // args.rows)
    if cell_size[0] != cell_size[1]:
        raise ValueError("as células precisam ser quadradas")

    started = time.monotonic()
    preclean_reports: dict[str, dict[str, Any]] = {}
    preclean_tasks = []
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
            preclean_tasks.append(
                (
                    name,
                    source_cell,
                    mask,
                    preclean_dir,
                    args.tolerance,
                    tuple(args.key_color) if args.key_color else None,
                )
            )
    with ThreadPoolExecutor(max_workers=args.cpu_workers) as executor:
        for name, report in executor.map(_preclean_cell_task, preclean_tasks):
            preclean_reports[name] = report

    realesrgan_report = _run(
        [
            sys.executable,
            str(Path(__file__).with_name("realesrgan_anime_scale.py")),
            str(preclean_dir),
            str(upscaled_dir),
            "--device", args.device,
            "--precision", args.precision,
            "--batch-size", str(args.batch_size),
            "--cpu-workers", str(args.cpu_workers),
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
    names = [
        f"row{row}_col{column}.png"
        for row in range(args.rows)
        for column in range(args.phases)
    ]
    lineart_detector_batched = False
    if args.lineart_mode in (
        "lineart_standard",
        "lineart_coarse",
        "lineart_anime",
    ):
        for name in names:
            with Image.open(upscaled_dir / name) as opened:
                frame = opened.convert("RGBA")
            with Image.open(args.mask_source / name) as opened_mask:
                final_alpha = _mask_from_image(opened_mask, final_size)
            frame.putalpha(final_alpha)
            result = alpha_bleed(frame, args.final_bleed_radius)
            result.putalpha(final_alpha)
            detector_input = _black_composite(result)
            detector_kwargs = {
                "detect_resolution": final_size[0],
                "image_resolution": final_size[0],
                "output_type": "pil",
            }
            if args.lineart_mode in ("lineart_standard", "lineart_coarse"):
                detector_kwargs["coarse"] = args.lineart_mode == "lineart_coarse"
            detected = lineart_detector(detector_input, **detector_kwargs)
            detected_lineart = _detector_output_as_lineart(detected)
            lineart_layer = build_lineart_layer(
                result, detected_lineart, args.lineart_strength
            )
            result.save(args.output / name, format="PNG")
            if lineart_output is not None:
                lineart_layer.save(lineart_output / name, format="PNG")
            detector_input.close()
            detected.close()
            detected_lineart.close()
            lineart_layer.close()
            frame.close()
            final_alpha.close()
            result.close()
    else:
        final_tasks = [
            (
                upscaled_dir,
                args.mask_source,
                args.output,
                lineart_output,
                name,
                final_size,
                args.final_bleed_radius,
                args.lineart_dir,
                args.lineart_mode,
                args.lineart_strength,
            )
            for name in names
        ]
        with ThreadPoolExecutor(max_workers=args.cpu_workers) as executor:
            list(executor.map(_write_final_cell_task, final_tasks))

    sprite_render._build_sheet(args.output, args.rows, args.phases, final_size[0])
    directions = sprite_render.DIRECTION_ROWS[: args.rows]
    gifs = sprite_render._build_gifs(
        args.output, args.rows, args.phases, args.fps, directions
    )
    legacy = sprite_render._build_gif(args.output, args.phases, args.fps)
    diagonal, diagonal_sequence = sprite_render._build_upscaled_diagonal_gif(
        args.output, args.rows, args.phases, args.fps
    )
    lineart_sheet = None
    lineart_gifs: dict[str, Path] = {}
    lineart_legacy = None
    lineart_ordered = None
    if lineart_output is not None:
        lineart_sheet = sprite_render._build_sheet(
            lineart_output, args.rows, args.phases, final_size[0]
        )
        lineart_gifs = sprite_render._build_gifs(
            lineart_output, args.rows, args.phases, args.fps, directions
        )
        lineart_legacy = sprite_render._build_gif(
            lineart_output, args.phases, args.fps
        )
        lineart_ordered = _build_ordered_gif(
            lineart_output, args.rows, args.phases, args.fps
        )
    lineart_export = {
        "enabled": lineart_output is not None,
        "mode": args.lineart_mode,
        "color": "white",
        "background": "transparent",
        "composite": "separate_runtime_layer",
        "directory": "lineart" if lineart_output is not None else None,
        "spritesheet": (
            str(lineart_sheet.relative_to(args.output))
            if lineart_sheet is not None
            else None
        ),
        "gifs": {direction: path.name for direction, path in lineart_gifs.items()},
        "legacy_gif": lineart_legacy.name if lineart_legacy else None,
        "ordered_gif": lineart_ordered.name if lineart_ordered else None,
    }
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
            f"lineart_export_{args.lineart_mode}" if lineart_applied else "lineart_disabled",
        ],
        "lineart": {
            "mode": args.lineart_mode,
            "applied": lineart_applied,
            "source": str(args.lineart_dir.resolve()) if args.lineart_mode == "blender" and args.lineart_dir else (
                "lllyasviel/Annotators:LineartDetector" if args.lineart_mode in ("lineart_standard", "lineart_coarse") else (
                    "lllyasviel/Annotators:LineartAnimeDetector" if args.lineart_mode == "lineart_anime" else None
                )
            ),
            "strength": args.lineart_strength if lineart_applied else 0.0,
            "mask": "source_alpha_intersection",
            "composite_color": "white",
            "composite": "separate_spritesheet",
            "spritesheet": lineart_export["spritesheet"],
            "device": lineart_device,
            "per_frame": args.lineart_mode in ("lineart_standard", "lineart_coarse", "lineart_anime"),
        },
        "lineart_export": lineart_export,
        "preclean": {
            "tolerance": args.tolerance,
            "reports": preclean_reports,
        },
        "realesrgan": realesrgan_report,
        "parallelism": {
            "gpu_batch_size": args.batch_size,
            "cpu_workers": args.cpu_workers,
            "lineart_detector_batched": lineart_detector_batched,
        },
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
