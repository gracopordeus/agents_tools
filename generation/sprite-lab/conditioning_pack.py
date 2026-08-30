"""Build a labelled reference pack for image-generation providers.

The command copies a deterministic set of 3D-derived channels into a run
directory, creates a human-readable montage and writes the validated manifest
consumed by the provider runner and post-processor.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps

import conditioning_schema as schema


DEFAULT_PROMPT = (
    "Transform the supplied 3D render into the target character. "
    "The target reference controls identity, clothing, materials, colors and "
    "recognizable design. The 3D beauty render controls pose, camera, scale, "
    "limb placement, equipment and frame composition. Silhouette, segmentation, "
    "depth and skeleton panels are structural guides only; they must not appear "
    "as panels, labels or overlays in the final image. Preserve the exact frame "
    "composition, transparent background and complete character."
)
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    files = sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS
    )
    if not files:
        raise ValueError(f"nenhum frame encontrado em {directory}")
    return files


def _frame_id(path: Path, index: int) -> str:
    candidate = path.stem.strip()
    return candidate if candidate and schema.FRAME_ID_RE.fullmatch(candidate) else f"f{index:02d}"


def _copy_as_png(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.convert("RGBA").save(destination, format="PNG")


def _load_and_check_size(path: Path, expected: tuple[int, int] | None) -> tuple[int, int]:
    with Image.open(path) as image:
        size = image.size
    if expected is not None and size != expected:
        raise ValueError(f"dimensão incompatível em {path.name}: {size}, esperado {expected}")
    return size


def _font() -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 18)
    except OSError:
        return ImageFont.load_default()


def _panel(label: str, images: list[Image.Image], width: int, max_width: int) -> Image.Image:
    scale = min(1.0, max_width / max(width * len(images), 1))
    panel_width = max(1, round(width * scale))
    panel_height = max(1, round(images[0].height * scale))
    header_height = 34
    panel = Image.new("RGBA", (panel_width * len(images), panel_height + header_height), (35, 37, 44, 255))
    draw = ImageDraw.Draw(panel)
    draw.rectangle((0, 0, panel.width - 1, header_height - 1), fill=(52, 56, 66, 255))
    draw.text((10, 8), label, fill=(245, 247, 250, 255), font=_font())
    for index, image in enumerate(images):
        resized = ImageOps.contain(image, (panel_width, panel_height), Image.Resampling.LANCZOS)
        tile = Image.new("RGBA", (panel_width, panel_height), (0, 0, 0, 0))
        tile.alpha_composite(
            resized,
            ((panel_width - resized.width) // 2, (panel_height - resized.height) // 2),
        )
        panel.alpha_composite(tile, (index * panel_width, header_height))
        tile.close()
        resized.close()
    return panel


def _make_montage(
    target: Image.Image,
    channels: dict[str, list[Image.Image]],
    destination: Path,
) -> None:
    first = next(iter(channels.values()))
    cell_width, cell_height = first[0].size
    max_panel_width = 1100
    panels = [_panel("TARGET IDENTITY", [target], cell_width, max_panel_width)]
    labels = {
        "beauty": "3D BEAUTY / STRUCTURE",
        "silhouette": "SILHOUETTE / ALPHA",
        "segmentation": "PART SEGMENTATION",
        "depth": "DEPTH",
        "skeleton": "ARMATURE / LANDMARKS",
    }
    for name in schema.CHANNELS:
        if name in channels:
            panels.append(_panel(labels[name], channels[name], cell_width, max_panel_width))
    panel_width = max(panel.width for panel in panels)
    panel_height = max(panel.height for panel in panels)
    columns = 2
    rows = (len(panels) + columns - 1) // columns
    montage = Image.new(
        "RGBA",
        (panel_width * columns, panel_height * rows),
        (20, 22, 27, 255),
    )
    for index, panel in enumerate(panels):
        x = (index % columns) * panel_width
        y = (index // columns) * panel_height
        montage.alpha_composite(panel, (x, y))
    destination.parent.mkdir(parents=True, exist_ok=True)
    montage.save(destination, format="PNG")


def _default_prompt(action: str, direction: str) -> str:
    return f"{DEFAULT_PROMPT} This is action {action}, direction {direction}."


def _default_pack_id(action: str, direction: str) -> str:
    value = f"conditioning-{action}-{direction}".casefold()
    value = "".join(
        character
        if character.isascii() and (character.isalnum() or character in "_-")
        else "-"
        for character in value
    )
    value = "-".join(part for part in value.split("-") if part)[:96]
    return value or "conditioning-pack"


def build_pack(
    source_dir: Path,
    output_dir: Path,
    target_reference: Path,
    *,
    action: str,
    direction: str,
    fps: float = 10.0,
    prompt: str | None = None,
    prompt_version: str = "v1",
    channels: Iterable[str] = schema.REQUIRED_CHANNELS,
    pack_id: str | None = None,
    foot_anchor: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Copy channels, create the montage and return a validated manifest."""
    if not target_reference.is_file():
        raise FileNotFoundError(target_reference)
    selected = list(dict.fromkeys(channels))
    unknown = sorted(set(selected) - set(schema.CHANNELS))
    if unknown:
        raise ValueError(f"canais desconhecidos: {', '.join(unknown)}")
    missing = sorted(set(schema.REQUIRED_CHANNELS) - set(selected))
    if missing:
        raise ValueError(f"canais obrigatórios ausentes: {', '.join(missing)}")

    source_files = {name: _files(source_dir / name) for name in selected}
    count = len(source_files[selected[0]])
    if count < 1 or count > 64:
        raise ValueError("a quantidade de frames deve ficar entre 1 e 64")
    for name, files in source_files.items():
        if len(files) != count:
            raise ValueError(f"canal {name} possui {len(files)} frames, esperado {count}")
    ids = [_frame_id(path, index) for index, path in enumerate(source_files["beauty"])]
    if len(set(ids)) != len(ids):
        raise ValueError("os nomes dos frames devem ser únicos")

    target = Image.open(target_reference).convert("RGBA")
    cell_size = _load_and_check_size(source_files["beauty"][0], None)
    channel_images: dict[str, list[Image.Image]] = {}
    for name, files in source_files.items():
        channel_images[name] = []
        for path in files:
            if _load_and_check_size(path, cell_size) != cell_size:
                raise ValueError(f"canal {name} possui células com dimensões diferentes")
            channel_images[name].append(Image.open(path).convert("RGBA"))

    output_dir.mkdir(parents=True, exist_ok=True)
    for name, files in source_files.items():
        for index, source in enumerate(files):
            _copy_as_png(source, output_dir / name / f"{ids[index]}.png")
    target_destination = output_dir / "target-reference" / f"target{target_reference.suffix.casefold()}"
    target_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target_reference, target_destination)
    montage = output_dir / "conditioning-pack.png"
    _make_montage(target, channel_images, montage)
    for images in channel_images.values():
        for image in images:
            image.close()
    target.close()

    manifest: dict[str, Any] = {
        "schema": schema.PACK_SCHEMA,
        "id": pack_id or _default_pack_id(action, direction),
        "project": "generation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "direction": direction,
        "cell_size": list(cell_size),
        "foot_anchor": list(foot_anchor or (cell_size[0] // 2, round(cell_size[1] * 0.86))),
        "frame_count": count,
        "fps": float(fps),
        "channels": selected,
        "frames": [
            {
                "id": frame_id,
                "index": index,
                "channels": {
                    name: f"{name}/{frame_id}.png" for name in selected
                },
            }
            for index, frame_id in enumerate(ids)
        ],
        "target_reference": {"path": target_destination.relative_to(output_dir).as_posix(), "role": "identity"},
        "conditioning_pack": "conditioning-pack.png",
        "prompt": {
            "version": prompt_version,
            "template": prompt or _default_prompt(action, direction),
        },
        "authority": {
            "identity": ["target_reference"],
            "structure": selected,
        },
        "source": {
            "root": str(source_dir.resolve()),
            "target_reference": str(target_reference.resolve()),
        },
    }
    schema.write_manifest(output_dir / "manifest.json", manifest)
    (output_dir / "prompt.txt").write_text(manifest["prompt"]["template"] + "\n", encoding="utf-8")
    return schema.validate_manifest(manifest)


def _parse_channels(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="diretório com subpastas de canais")
    parser.add_argument("target_reference", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--action", default="run")
    parser.add_argument("--direction", default="r1")
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--channels", default=",".join(schema.REQUIRED_CHANNELS))
    parser.add_argument(
        "--foot-anchor",
        default=None,
        help="x,y em pixels; padrão: centro e 86%% da altura",
    )
    args = parser.parse_args(argv)
    prompt = args.prompt_file.read_text(encoding="utf-8").strip() if args.prompt_file else None
    foot_anchor = None
    if args.foot_anchor:
        try:
            values = [int(item.strip()) for item in args.foot_anchor.split(",")]
        except ValueError as exc:
            raise SystemExit("--foot-anchor deve estar no formato x,y") from exc
        if len(values) != 2:
            raise SystemExit("--foot-anchor deve estar no formato x,y")
        foot_anchor = (values[0], values[1])
    manifest = build_pack(
        args.source_dir,
        args.output_dir,
        args.target_reference,
        action=args.action,
        direction=args.direction,
        fps=args.fps,
        prompt=prompt,
        channels=_parse_channels(args.channels),
        foot_anchor=foot_anchor,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
