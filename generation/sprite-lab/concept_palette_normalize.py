"""Apply one conservative concept-guided Lab chroma shift to every sprite frame."""
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


def concept_foreground(image: np.ndarray, distance: float = 22.0) -> np.ndarray:
    """Find the principal subject against a near-uniform border background."""
    border = np.concatenate([image[0], image[-1], image[:, 0], image[:, -1]])
    background = np.median(border, axis=0)
    candidate = (
        np.linalg.norm(image.astype(np.float32) - background, axis=2) > distance
    ).astype(np.uint8)
    contours, _ = cv2.findContours(
        candidate, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        raise ValueError("não foi possível separar o personagem da imagem conceito")
    mask = np.zeros(candidate.shape, dtype=np.uint8)
    cv2.drawContours(mask, [max(contours, key=cv2.contourArea)], -1, 1, -1)
    return mask.astype(bool)


def lab_chroma_mean(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    valid = mask & (lab[..., 0] > 25)
    if not np.any(valid):
        raise ValueError("máscara não contém pixels válidos para medir a paleta")
    return lab[valid, 1:].astype(np.float32).mean(axis=0)


def apply_chroma_shift(
    image: Image.Image, shift: np.ndarray, bleed_radius: int
) -> Image.Image:
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    lab = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2LAB).astype(np.float32)
    foreground = rgba[..., 3] > 0
    lab[..., 1][foreground] += shift[0]
    lab[..., 2][foreground] += shift[1]
    corrected = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)
    result = Image.fromarray(np.dstack([corrected, rgba[..., 3]]), mode="RGBA")
    alpha = result.getchannel("A")
    bled = alpha_bleed(result, bleed_radius)
    bled.putalpha(alpha)
    result.close()
    alpha.close()
    return bled


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("concept", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--phases", type=int, default=8)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--strength", type=float, default=1.0)
    parser.add_argument("--max-shift", type=float, default=6.0)
    parser.add_argument("--bleed-radius", type=int, default=8)
    args = parser.parse_args()

    if not args.source.is_dir() or not args.concept.is_file():
        raise FileNotFoundError("source ou concept ausente")
    if not 0.0 <= args.strength <= 1.0 or args.max_shift < 0:
        raise ValueError("strength ou max-shift inválido")
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    with Image.open(args.concept) as opened:
        concept_rgb = np.asarray(opened.convert("RGB"), dtype=np.uint8)
    concept_ab = lab_chroma_mean(concept_rgb, concept_foreground(concept_rgb))

    names = [
        f"row{row}_col{column}.png"
        for row in range(args.rows)
        for column in range(args.phases)
    ]
    sprite_values: list[np.ndarray] = []
    frame_size: tuple[int, int] | None = None
    for name in names:
        with Image.open(args.source / name) as opened:
            rgba = np.asarray(opened.convert("RGBA"), dtype=np.uint8)
        frame_size = (rgba.shape[1], rgba.shape[0])
        lab = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2LAB)
        valid = (rgba[..., 3] > 0) & (lab[..., 0] > 25)
        sprite_values.append(lab[valid, 1:].astype(np.float32))
    sprite_ab = np.concatenate(sprite_values).mean(axis=0)
    raw_shift = concept_ab - sprite_ab
    shift = np.clip(raw_shift, -args.max_shift, args.max_shift) * args.strength

    for name in names:
        with Image.open(args.source / name) as opened:
            corrected = apply_chroma_shift(opened, shift, args.bleed_radius)
        corrected.save(args.output / name, format="PNG")
        corrected.close()

    assert frame_size is not None
    sprite_render._build_sheet(args.output, args.rows, args.phases, frame_size[0])
    directions = sprite_render.DIRECTION_ROWS[: args.rows]
    gifs = sprite_render._build_gifs(
        args.output, args.rows, args.phases, args.fps, directions
    )
    legacy = sprite_render._build_gif(args.output, args.phases, args.fps)
    diagonal, diagonal_sequence = sprite_render._build_upscaled_diagonal_gif(
        args.output, args.rows, args.phases, args.fps
    )
    metadata = {
        "schema": "sprite_lab.concept_palette_normalize/v1",
        "source": str(args.source.resolve()),
        "concept": str(args.concept.resolve()),
        "method": "global_lab_chroma_mean_shift",
        "preserves_luminance": True,
        "concept_ab_mean": concept_ab.round(4).tolist(),
        "sprite_ab_mean": sprite_ab.round(4).tolist(),
        "raw_ab_shift": raw_shift.round(4).tolist(),
        "applied_ab_shift": shift.round(4).tolist(),
        "strength": args.strength,
        "max_shift": args.max_shift,
        "alpha": "preserved_binary",
        "bleed_radius": args.bleed_radius,
        "grid": [args.phases, args.rows],
        "cell_size": list(frame_size),
        "images": len(names),
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
