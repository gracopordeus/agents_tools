"""Generate asset_manifest.json for each tile category in the environment atlas.

Reads atlas_manifest.json and render_metadata.json, producing a
sprite_lab.asset_manifest/v1 compliant manifest per tile.

Usage:
    python generate_env_manifest.py --input output/ --output output/manifests/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_manifests(input_dir: Path, output_dir: Path) -> list[dict]:
    metadata_path = input_dir / "render_metadata.json"
    atlas_manifest_path = input_dir / "atlas_manifest.json"

    if not metadata_path.is_file():
        raise FileNotFoundError(f"render_metadata.json não encontrado em {input_dir}")
    if not atlas_manifest_path.is_file():
        raise FileNotFoundError(f"atlas_manifest.json não encontrado em {input_dir}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    atlas_manifest = json.loads(atlas_manifest_path.read_text(encoding="utf-8"))

    atlas_path = Path(atlas_manifest["atlas_path"])
    cell_size = metadata.get("cell_size", [256, 256])
    directions = metadata.get("directions", 8)

    manifests = []
    for asset in metadata.get("assets", []):
        tile_key = asset["tile_key"]
        name = asset["name"]
        col = asset["col"]
        capabilities = asset.get("capabilities", [])
        fbx_path = asset.get("fbx_path", "")

        asset_dir = input_dir / name
        cells = []
        for cell in asset.get("cells", []):
            direction = cell["direction"]
            cell_path = Path(cell["path"])
            if not cell_path.is_file():
                cell_path = asset_dir / f"{name}_dir_{direction:02d}.png"
            if cell_path.is_file():
                cells.append({
                    "direction": direction,
                    "angle": cell.get("angle", direction * 45.0),
                    "path": str(cell_path),
                    "bytes": cell_path.stat().st_size,
                    "sha256": file_sha256(cell_path),
                })

        manifest = {
            "schema": "sprite_lab.asset_manifest/v1",
            "asset": {
                "id": f"env_{tile_key}",
                "name": name,
                "type": "tile" if tile_key in ("floor", "rough", "solid", "road") else "prop_static",
                "representation": "directional_sprite_atlas",
                "capabilities": capabilities,
            },
            "contract": {
                "tile_key": tile_key,
                "col_index": col,
                "directions": directions,
                "cell_size": cell_size,
            },
            "source": {
                "pack": asset.get("source_pack", "unknown"),
                "fbx": fbx_path,
            },
            "layout": {
                "atlas_path": str(atlas_path),
                "atlas_size": atlas_manifest.get("atlas_size", [2048, 2048]),
                "col": col,
                "row_start": 0,
                "row_end": directions - 1,
            },
            "animation": {
                "type": "directional",
                "directions": directions,
                "phases": 1,
                "loop": False,
            },
            "placement": {
                "anchor": "center",
                "foot_offset": [0, 0],
            },
            "gameplay": {
                "tile_key": tile_key,
                "layer": asset.get("layer", "unknown"),
                "category": asset.get("category", "unknown"),
            },
            "runtime": {
                "format": "png",
                "color_mode": "rgba",
                "bit_depth": 8,
            },
            "artifacts": [
                {"role": "atlas", "path": str(atlas_path), "sha256": file_sha256(atlas_path) if atlas_path.is_file() else None},
            ] + [
                {"role": f"cell_dir{c['direction']}", "path": c["path"], "bytes": c["bytes"], "sha256": c["sha256"]}
                for c in cells
            ],
            "validation": {
                "total_cells": len(cells),
                "expected_cells": directions,
                "valid": len(cells) == directions,
            },
            "provenance": {
                "generator": "blender_env_atlas.py",
                "profile": metadata.get("atlas_id", "unknown"),
            },
        }

        manifest_dir = output_dir / tile_key
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "asset_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        manifests.append(manifest)

    return manifests


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate env atlas manifests")
    parser.add_argument("--input", required=True, help="Render output directory")
    parser.add_argument("--output", required=True, help="Manifest output directory")
    args = parser.parse_args()

    input_dir = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()

    manifests = generate_manifests(input_dir, output_dir)
    valid = sum(1 for m in manifests if m["validation"]["valid"])
    print(f"Manifests: {valid}/{len(manifests)} válidos")
    for m in manifests:
        v = m["validation"]
        status = "✅" if v["valid"] else "❌"
        print(f"  {status} {m['asset']['id']:20s} ({v['total_cells']}/{v['expected_cells']} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
