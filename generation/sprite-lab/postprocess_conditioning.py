"""Normalize provider frames and assemble the generation spritesheet."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image

import conditioning_image
import conditioning_schema as schema


def normalize_frame(
    source: Path,
    *,
    cell_size: tuple[int, int],
    foot_anchor: tuple[int, int],
    target_height_ratio: float = 0.82,
    background_threshold: float = 32.0,
) -> Image.Image:
    """Remove the provider background and place the subject on a fixed canvas."""
    if not 0.1 <= target_height_ratio <= 1.0:
        raise ValueError("target_height_ratio deve ficar entre 0.1 e 1.0")
    loaded = conditioning_image.load_rgba(source)
    try:
        image = conditioning_image.remove_background(loaded, threshold=background_threshold)
    finally:
        loaded.close()
    bbox = conditioning_image.alpha_bbox(image)
    if bbox is None:
        image.close()
        raise ValueError(f"nenhum personagem detectado em {source}")
    x0, y0, x1, y1 = bbox
    subject = image.crop((x0, y0, x1 + 1, y1 + 1))
    image.close()
    target_height = max(1, round(cell_size[1] * target_height_ratio))
    scale = target_height / max(subject.height, 1)
    resized_size = (max(1, round(subject.width * scale)), target_height)
    if resized_size[0] > cell_size[0]:
        scale = cell_size[0] / resized_size[0]
        resized_size = (cell_size[0], max(1, round(resized_size[1] * scale)))
    subject = subject.resize(resized_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", cell_size, (0, 0, 0, 0))
    x = round(foot_anchor[0] - subject.width / 2.0)
    y = foot_anchor[1] - subject.height
    # The fit policy guarantees the subject is inside the canvas; these guards
    # make the failure explicit if a future policy violates that assumption.
    if x < 0 or y < 0 or x + subject.width > cell_size[0] or y + subject.height > cell_size[1]:
        subject.close()
        raise ValueError(f"subject excede o canvas após normalização: {source.name}")
    canvas.alpha_composite(subject, (x, y))
    subject.close()
    return canvas


def _copy_frame(source: Path, destination: Path, **kwargs: Any) -> None:
    frame = normalize_frame(source, **kwargs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.save(destination, format="PNG")
    frame.close()


def process_run(
    manifest_path: Path,
    generated_dir: Path,
    output_dir: Path,
    *,
    target_height_ratio: float = 0.82,
    background_threshold: float = 32.0,
    foot_anchor: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Normalize all generated frames and build sheet/GIF artifacts."""
    manifest = schema.load_manifest(manifest_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    cell_size = tuple(manifest["cell_size"])
    anchor_value = foot_anchor or tuple(manifest["foot_anchor"])
    normalized_dir = output_dir / "normalized"
    for frame in manifest["frames"]:
        frame_id = frame["id"]
        source = generated_dir / f"{frame_id}.png"
        if not source.is_file():
            raise FileNotFoundError(source)
        _copy_frame(
            source,
            normalized_dir / f"{frame_id}.png",
            cell_size=cell_size,
            foot_anchor=(int(anchor_value[0]), int(anchor_value[1])),
            target_height_ratio=target_height_ratio,
            background_threshold=background_threshold,
        )

    # Use the existing Sprite Lab sheet/GIF routines so the PoC output has the
    # same inspection semantics as the normal renderer.
    import sprite_render

    for index, frame in enumerate(manifest["frames"]):
        source = normalized_dir / f"{frame['id']}.png"
        destination = output_dir / f"row0_col{index}.png"
        destination.write_bytes(source.read_bytes())
    sheet = sprite_render._build_sheet(output_dir, 1, len(manifest["frames"]), cell_size[0])
    gifs = sprite_render._build_gifs(
        output_dir, 1, len(manifest["frames"]), float(manifest["fps"]), ("r1",)
    )
    metadata: dict[str, Any] = {
        "schema": "generation.conditioning_postprocess/v1",
        "manifest": str(manifest_path.resolve()),
        "generated_dir": str(generated_dir.resolve()),
        "cell_size": list(cell_size),
        "frame_count": len(manifest["frames"]),
        "fps": float(manifest["fps"]),
        "foot_anchor": list(anchor_value),
        "target_height_ratio": target_height_ratio,
        "background_threshold": background_threshold,
        "normalized_dir": str(normalized_dir.resolve()),
        "spritesheet": str(sheet.resolve()),
        "gifs": {key: str(value.resolve()) for key, value in gifs.items()},
    }
    (output_dir / "postprocess.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("generated_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--target-height-ratio", type=float, default=0.82)
    parser.add_argument("--background-threshold", type=float, default=32.0)
    parser.add_argument(
        "--foot-anchor",
        default=None,
        help="x,y em pixels; substitui a âncora do manifesto",
    )
    args = parser.parse_args(argv)
    foot_anchor = None
    if args.foot_anchor:
        try:
            values = [int(item.strip()) for item in args.foot_anchor.split(",")]
        except ValueError as exc:
            raise SystemExit("--foot-anchor deve estar no formato x,y") from exc
        if len(values) != 2:
            raise SystemExit("--foot-anchor deve estar no formato x,y")
        foot_anchor = (values[0], values[1])
    metadata = process_run(
        args.manifest,
        args.generated_dir,
        args.output_dir,
        target_height_ratio=args.target_height_ratio,
        background_threshold=args.background_threshold,
        foot_anchor=foot_anchor,
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
