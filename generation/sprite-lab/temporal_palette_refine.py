"""Selectively stabilize temporal chroma and optionally apply one shared palette."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import sprite_render
from waifu2x_cunet_scale import alpha_bleed


def frame_chroma_median(rgba: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2LAB)
    valid = (rgba[..., 3] > 0) & (lab[..., 0] > 25)
    if not np.any(valid):
        raise ValueError("frame sem foreground válido")
    return np.median(lab[valid, 1:].astype(np.float32), axis=0)


def bounded_shift(delta: np.ndarray, max_shift: float) -> np.ndarray:
    magnitude = float(np.linalg.norm(delta))
    if magnitude == 0.0 or magnitude <= max_shift:
        return delta.astype(np.float32)
    return (delta * (max_shift / magnitude)).astype(np.float32)


def stabilize_rgba(rgba: np.ndarray, shift: np.ndarray) -> np.ndarray:
    result = rgba.copy()
    if not np.any(shift):
        return result
    foreground = rgba[..., 3] > 0
    lab = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2LAB).astype(np.float32)
    lab[..., 1][foreground] += shift[0]
    lab[..., 2][foreground] += shift[1]
    result[..., :3] = cv2.cvtColor(
        np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB
    )
    return result


def train_shared_palette(frames: list[np.ndarray], colors: int) -> Image.Image:
    pixels = np.concatenate([frame[frame[..., 3] > 0, :3] for frame in frames])
    max_samples = 262_144
    if len(pixels) > max_samples:
        indices = np.linspace(0, len(pixels) - 1, max_samples, dtype=np.int64)
        pixels = pixels[indices]
    width = 512
    height = int(np.ceil(len(pixels) / width))
    padded = np.pad(pixels, ((0, width * height - len(pixels)), (0, 0)), mode="edge")
    training = Image.fromarray(padded.reshape(height, width, 3), mode="RGB")
    palette = training.quantize(
        colors=colors,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    training.close()
    return palette


def apply_shared_palette(rgba: np.ndarray, palette: Image.Image) -> np.ndarray:
    rgb = Image.fromarray(rgba[..., :3], mode="RGB")
    indexed = rgb.quantize(palette=palette, dither=Image.Dither.NONE)
    result = rgba.copy()
    result[..., :3] = np.asarray(indexed.convert("RGB"), dtype=np.uint8)
    rgb.close()
    indexed.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--phases", type=int, default=8)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--outlier-distance", type=float, default=3.25)
    parser.add_argument("--max-shift", type=float, default=1.5)
    parser.add_argument("--colors", type=int, default=0)
    parser.add_argument("--bleed-radius", type=int, default=8)
    args = parser.parse_args()

    if not args.source.is_dir():
        raise FileNotFoundError(args.source)
    if args.colors and not 2 <= args.colors <= 256:
        raise ValueError("colors deve ficar entre 2 e 256, ou ser zero")
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    names = [
        f"row{row}_col{column}.png"
        for row in range(args.rows)
        for column in range(args.phases)
    ]
    frames: dict[str, np.ndarray] = {}
    for name in names:
        with Image.open(args.source / name) as opened:
            frames[name] = np.asarray(opened.convert("RGBA"), dtype=np.uint8)

    reports: dict[str, dict[str, object]] = {}
    stabilized: list[np.ndarray] = []
    for row in range(args.rows):
        row_names = [f"row{row}_col{column}.png" for column in range(args.phases)]
        medians = np.asarray([frame_chroma_median(frames[name]) for name in row_names])
        target = np.median(medians, axis=0)
        for name, chroma in zip(row_names, medians):
            distance = float(np.linalg.norm(chroma - target))
            shift = (
                bounded_shift(target - chroma, args.max_shift)
                if distance > args.outlier_distance
                else np.zeros(2, dtype=np.float32)
            )
            frames[name] = stabilize_rgba(frames[name], shift)
            stabilized.append(frames[name])
            reports[name] = {
                "chroma_median": chroma.round(4).tolist(),
                "direction_target": target.round(4).tolist(),
                "distance": round(distance, 4),
                "applied_shift": shift.round(4).tolist(),
                "corrected": bool(np.any(shift)),
            }

    palette = train_shared_palette(stabilized, args.colors) if args.colors else None
    for name in names:
        rgba = apply_shared_palette(frames[name], palette) if palette else frames[name]
        image = Image.fromarray(rgba, mode="RGBA")
        alpha = image.getchannel("A")
        result = alpha_bleed(image, args.bleed_radius)
        result.putalpha(alpha)
        result.save(args.output / name, format="PNG")
        image.close()
        alpha.close()
        result.close()
    if palette:
        palette.close()

    size = (frames[names[0]].shape[1], frames[names[0]].shape[0])
    sprite_render._build_sheet(args.output, args.rows, args.phases, size[0])
    directions = sprite_render.DIRECTION_ROWS[: args.rows]
    gifs = sprite_render._build_gifs(
        args.output, args.rows, args.phases, args.fps, directions
    )
    ordered_frames = [
        args.output / f"row{direction - 1}_col{phase}.png"
        for direction in (1, 2, 5, 4, 3, 8, 7, 6)
        for phase in range(args.phases)
    ]
    ordered = sprite_render._write_gif(
        ordered_frames,
        args.output / "animation_all_directions_1-2-5-4-3-8-7-6.gif",
        args.fps,
    )
    metadata = {
        "schema": "sprite_lab.temporal_palette_refine/v1",
        "source": str(args.source.resolve()),
        "method": "bounded_directional_lab_chroma_outlier_stabilization",
        "outlier_distance": args.outlier_distance,
        "max_shift": args.max_shift,
        "shared_palette_colors": args.colors or None,
        "dithering": False,
        "alpha": "preserved_binary",
        "bleed_radius": args.bleed_radius,
        "corrected_frames": [name for name, report in reports.items() if report["corrected"]],
        "frames": reports,
        "images": len(names),
        "cell_size": list(size),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "spritesheet": "spritesheet.png",
        "gifs": {direction: path.name for direction, path in gifs.items()},
        "ordered_gif": ordered.name if ordered else None,
    }
    sprite_render.write_json_atomic(args.output / "render_metadata.json", metadata)
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
