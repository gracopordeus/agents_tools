#!/usr/bin/env python3
"""Exporta células renderizadas para o Godot: atlas processado + spec + HdVisualProfile.tres + teste de contrato.

Uso:
    python3 export_character.py --id mannequin --cells artifacts/run_template_cells \
        [--category player] [--display-height 130] [--fps 10] [--anim run]

Produz (padrões do projeto):
  assets/characters/<category>/<id>/processed/<id>_atlas_v1.png   (RGBA, transparente)
  assets/characters/<category>/<id>/<id>_profile.tres            (HdVisualProfile)
  assets/source_specs/<id>_<anim>.json
  tests/unit/test_<id>_profile.gd                                (contrato Godot)

O foot_anchor é lido do report gerado pela composição (linha do chão determinística).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import build_run_sheet as b
from path_config import PROJECT_ROOT
import sprite_manifest as manifest_lib

ROOT = PROJECT_ROOT
GODOT_DIRS = ["west", "north_west", "east", "north_east",
              "north", "south_west", "south", "south_east"]


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--id")
    p.add_argument("--manifest", help="Manifest sprite_lab.sprite_manifest v1")
    p.add_argument("--cells", help="Dir legado com as células row{r}_col{c}.png")
    p.add_argument("--output-root", default=str(ROOT),
                   help="Raiz onde assets/, tests/ e specs serão gravados")
    p.add_argument("--category")
    p.add_argument("--anim")
    p.add_argument("--display-height", type=float)
    p.add_argument("--fps", type=float)
    p.add_argument("--version", default="v1")
    p.add_argument("--no-sharpen", action="store_true",
                   help="Desliga o afiamento de contorno (binarização do alpha)")
    return p.parse_args()


def build(cells_root: Path, sharpen: bool = True):
    n_rows = len(b.ROWS)
    n_cols = b.detect_cols(cells_root)
    raw = []
    for r in range(n_rows):
        row = []
        for c in range(n_cols):
            cell = b.load_cell(cells_root, r, c)
            if sharpen:
                cell = b.sharpen_contour(cell)
            row.append(cell)
        raw.append(row)

    boxes = {}
    for r in range(n_rows):
        for c in range(n_cols):
            bb = b.content_bbox(raw[r][c])
            if bb:
                boxes[(r, c)] = bb
    valid = list(boxes.values())
    x0 = min(v[0] for v in valid); y0 = min(v[1] for v in valid)
    x1 = max(v[2] for v in valid); y1 = max(v[3] for v in valid)
    window = (x0, y0, x1, y1)
    union_w, union_h = x1 - x0 + 1, y1 - y0 + 1
    scale = min((b.FILL_FRAC * b.CELL) / union_h, b.CELL / union_w)

    frames = {}
    for r, d in enumerate(b.ROWS):
        frames[d] = [b.normalize_window(raw[r][c], window, scale) for c in range(n_cols)]

    base_w, base_h = n_cols * b.CELL, n_rows * b.CELL
    atlas = Image.new("RGBA", (base_w, base_h), (0, 0, 0, 0))
    for r, d in enumerate(b.ROWS):
        for c in range(n_cols):
            atlas.paste(frames[d][c], (c * b.CELL, r * b.CELL), frames[d][c])

    foot_anchor = [b.CELL // 2, round(union_h * scale)]
    meta = {"n_rows": n_rows, "n_cols": n_cols, "window": list(window),
            "scale": round(scale, 4), "foot_anchor": foot_anchor,
            "frame_size": [b.CELL, b.CELL], "directions": list(b.ROWS),
            "fit_policy": "reference_fit", "animation": "run", "fps": 10.0}
    return atlas, meta


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest_lib.validate_manifest(manifest)
    return manifest


def build_from_manifest(manifest_path: Path, manifest: dict):
    """Materializa o atlas a partir do contrato, sem recalcular enquadramento."""
    base = manifest_path.parent
    layout = manifest["layout"]
    directions = list(layout["directions"])
    n_cols = int(layout["columns"])
    frame_w, frame_h = (int(value) for value in layout["frame_size"])
    frame_map = {(frame["direction"], int(frame["index"])): frame
                 for frame in manifest["frames"]}
    atlas = Image.new("RGBA", (n_cols * frame_w, len(directions) * frame_h), (0, 0, 0, 0))
    for row, direction in enumerate(directions):
        for col in range(n_cols):
            frame_meta = frame_map[(direction, col)]
            frame_path = base / frame_meta["path"]
            if not frame_path.exists():
                raise FileNotFoundError(f"frame declarado no manifest não existe: {frame_path}")
            frame = Image.open(frame_path).convert("RGBA")
            if frame.size != (frame_w, frame_h):
                raise ValueError(
                    f"frame {frame_path} tem {frame.size}; esperado {(frame_w, frame_h)}")
            atlas.paste(frame, (col * frame_w, row * frame_h), frame)
    fit = manifest.get("fit", {})
    meta = {
        "n_rows": len(directions),
        "n_cols": n_cols,
        "window": fit.get("window"),
        "scale": fit.get("scale"),
        "foot_anchor": list(layout["foot_anchor"]),
        "frame_size": [frame_w, frame_h],
        "directions": directions,
        "fit_policy": layout["fit_policy"],
        "animation": manifest["asset"].get("animation") or "run",
        "fps": float(layout["fps"]),
        "manifest_path": str(manifest_path),
    }
    return atlas, meta


def write_tres(path: Path, atlas_rel: str, meta: dict, args) -> None:
    fs = meta["frame_size"]
    logical_to_godot = {
        "w": "west", "nw": "north_west", "e": "east", "ne": "north_east",
        "n": "north", "sw": "south_west", "s": "south", "se": "south_east",
    }
    directions = meta.get("directions", b.ROWS)
    origins = ", ".join(
        f'&"{logical_to_godot.get(direction, direction)}": Vector2i(0, {row * fs[1]})'
        for row, direction in enumerate(directions))
    animation = args.anim or meta.get("animation") or "run"
    fps = args.fps if args.fps is not None else float(meta.get("fps", 10.0))
    lines = [
        '[gd_resource type="Resource" script_class="HdVisualProfile" load_steps=3 format=3]',
        "",
        f'[ext_resource type="Script" path="res://scripts/visual/hd_visual_profile.gd" id="1_profile"]',
        f'[ext_resource type="Texture2D" path="res://{atlas_rel}" id="2_atlas"]',
        "",
        "[resource]",
        'script = ExtResource("1_profile")',
        'atlas = ExtResource("2_atlas")',
        f'frame_size = Vector2i({fs[0]}, {fs[1]})',
        f'frame_origins = {{{origins}}}',
        f'animation_rows = {{&"{animation}": 0}}',
        f'animation_fps = {{&"{animation}": {fps:g}}}',
        f'animation_frames = {{&"{animation}": {meta["n_cols"]}}}',
        f'foot_anchor = Vector2({meta["foot_anchor"][0]}, {meta["foot_anchor"][1]})',
        f'reference_height = {fs[1]:g}.0',
        f'display_height = {args.display_height:g}.0',
        "visual_scale_multiplier = 1.0",
        "allow_horizontal_mirroring = false",
        "",
    ]
    path.write_text("\n".join(lines))


def write_spec(path: Path, atlas_rel: str, meta: dict, args) -> None:
    fs = meta["frame_size"]
    animation = args.anim or meta.get("animation") or "run"
    fps = args.fps if args.fps is not None else float(meta.get("fps", 10.0))
    display_height = args.display_height
    spec = {
        "id": f"{args.id}_{animation}",
        "category": f"characters/{args.category}",
        "generator": "sprite_lab_manifest_v1 + godot_adapter",
        "master": {"path": None, "width": None, "height": None, "preserve": False},
        "runtime": {
            "path": atlas_rel, "mode": "RGBA", "frame_size": fs,
            "foot_anchor": meta["foot_anchor"],
            "display_height_1080p": display_height,
            "filter": "linear", "mipmaps": False,
        },
        "directions_authored": meta.get("directions", b.ROWS),
        "directions_runtime": [
            {"w": "west", "nw": "north_west", "e": "east", "ne": "north_east",
             "n": "north", "sw": "south_west", "s": "south", "se": "south_east"}
            .get(direction, direction)
            for direction in meta.get("directions", b.ROWS)
        ],
        "horizontal_mirroring": False,
        "animations": {animation: {"row": 0, "frames": meta["n_cols"], "fps": fps}},
        "fit_policy": meta.get("fit_policy", "reference_fit"),
        "manifest": meta.get("manifest_path"),
        "perspective": "top-down 3/4 isometric-like (30° elev, 45° azim)",
        "shadow_baked": False,
        "notes": "Export automatizado; foot_anchor = linha do chão determinística.",
    }
    path.write_text(json.dumps(spec, indent=2))


def write_test(path: Path, atlas_rel: str, meta: dict, args) -> None:
    fs = meta["frame_size"]
    fa = meta["foot_anchor"]
    aw, ah = meta["n_cols"] * fs[0], meta["n_rows"] * fs[1]
    animation = args.anim or meta.get("animation") or "run"
    fps = args.fps if args.fps is not None else float(meta.get("fps", 10.0))
    display_height = args.display_height
    directions = meta.get("directions", b.ROWS)
    first_direction = {"w": "west", "nw": "north_west", "e": "east", "ne": "north_east",
                       "n": "north", "sw": "south_west", "s": "south", "se": "south_east"}.get(
                           directions[0], directions[0])
    last_direction = {"w": "west", "nw": "north_west", "e": "east", "ne": "north_east",
                      "n": "north", "sw": "south_west", "s": "south", "se": "south_east"}.get(
                          directions[-1], directions[-1])
    profile = f"res://assets/characters/{args.category}/{args.id}/{args.id}_profile.tres"
    lines = [
        "extends SceneTree",
        "",
        "func _init() -> void:",
        f"\tvar profile := load(\"{profile}\") as HdVisualProfile",
        "\tassert(profile != null)",
        f"\tassert(profile.frame_size == Vector2i({fs[0]}, {fs[1]}))",
        f"\tassert(profile.foot_anchor == Vector2({fa[0]}, {fa[1]}))",
        f"\tassert(is_equal_approx(profile.runtime_scale(), {display_height:g}.0 / {fs[1]:g}.0))",
        f"\tassert(profile.atlas != null and profile.atlas.get_size() == Vector2({aw}, {ah}))",
        f"\tassert(profile.frame_origins.has(&\"{first_direction}\") and profile.frame_origins.has(&\"{last_direction}\"))",
        f"\tassert(profile.animation_frames.get(&\"{animation}\", 0) == {meta['n_cols']})",
        f"\tassert(is_equal_approx(profile.animation_fps.get(&\"{animation}\", 0.0), {fps:g}))",
        f"\tprint(\"PASS: {args.id} profile contract\")",
        "\tquit()",
        "",
    ]
    path.write_text("\n".join(lines))


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).resolve()
    manifest_path = Path(args.manifest).resolve() if args.manifest else None
    if manifest_path:
        if not manifest_path.exists():
            print(f"manifest não encontrado: {manifest_path}", file=sys.stderr)
            return 1
        try:
            manifest = load_manifest(manifest_path)
            atlas, meta = build_from_manifest(manifest_path, manifest)
        except (OSError, ValueError, KeyError) as exc:
            print(f"manifest inválido: {exc}", file=sys.stderr)
            return 1
        asset_id = manifest["asset"].get("id") or "asset"
        args.id = args.id or asset_id.replace("/", "_").replace(":", "_")
        args.category = args.category or "player"
        args.anim = args.anim or meta["animation"]
        args.fps = args.fps if args.fps is not None else meta["fps"]
        args.display_height = args.display_height if args.display_height is not None else 250.0
    elif args.cells:
        cells_root = Path(args.cells)
        if not cells_root.exists():
            print(f"células não encontradas: {cells_root}", file=sys.stderr)
            return 1
        args.id = args.id or "asset"
        args.category = args.category or "player"
        args.anim = args.anim or "run"
        args.fps = args.fps if args.fps is not None else 10.0
        args.display_height = args.display_height if args.display_height is not None else 130.0
        atlas, meta = build(cells_root, sharpen=not args.no_sharpen)
    else:
        print("informe --manifest ou --cells", file=sys.stderr)
        return 2

    char_dir = output_root / "assets/characters" / args.category / args.id
    processed = char_dir / "processed"
    processed.mkdir(parents=True, exist_ok=True)

    atlas_rel = f"assets/characters/{args.category}/{args.id}/processed/{args.id}_atlas_{args.version}.png"
    atlas.save(output_root / atlas_rel)
    print(f"  atlas: {atlas_rel} ({atlas.width}x{atlas.height}) foot_anchor={meta['foot_anchor']}")

    write_tres(char_dir / f"{args.id}_profile.tres", atlas_rel, meta, args)
    print(f"  profile: assets/characters/{args.category}/{args.id}/{args.id}_profile.tres")

    spec_rel = f"assets/source_specs/{args.id}_{args.anim}.json"
    (output_root / "assets/source_specs").mkdir(parents=True, exist_ok=True)
    if manifest_path:
        meta["manifest_path"] = manifest_lib.relative_path(manifest_path, output_root)
    write_spec(output_root / spec_rel, atlas_rel, meta, args)
    print(f"  spec: {spec_rel}")

    test_rel = f"tests/unit/test_{args.id}_profile.gd"
    (output_root / "tests/unit").mkdir(parents=True, exist_ok=True)
    write_test(output_root / test_rel, atlas_rel, meta, args)
    print(f"  teste: {test_rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
