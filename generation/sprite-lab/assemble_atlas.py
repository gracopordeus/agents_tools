"""Assemble 8×8 environment atlas from individual asset renders.

Reads render_metadata.json produced by blender_env_atlas.py and composes
the final atlas PNG where:
  - columns (X) = asset type
  - rows (Y) = direction

Usage:
    python assemble_atlas.py --input output/ --output output/atlas.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("ERRO: Pillow necessário. pip install Pillow")
    sys.exit(1)


def assemble_atlas(input_dir: Path, output_path: Path) -> dict:
    metadata_path = input_dir / "render_metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"render_metadata.json não encontrado em {input_dir}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    cell_size = metadata.get("cell_size", [256, 256])
    directions = metadata.get("directions", 8)
    assets = metadata.get("assets", [])

    num_cols = len(assets)
    atlas_width = cell_size[0] * num_cols
    atlas_height = cell_size[1] * directions

    atlas = Image.new("RGBA", (atlas_width, atlas_height), (0, 0, 0, 0))

    cell_map = []
    for asset in assets:
        col = asset["col"]
        name = asset["name"]
        asset_dir = input_dir / name

        for cell in asset["cells"]:
            direction = cell["direction"]
            cell_path = Path(cell["path"])
            if not cell_path.is_file():
                cell_path = asset_dir / f"{name}_dir_{direction:02d}.png"
            if not cell_path.is_file():
                print(f"  AVISO: célula ausente: {cell_path}")
                continue

            cell_img = Image.open(cell_path)
            x = col * cell_size[0]
            y = direction * cell_size[1]
            atlas.paste(cell_img, (x, y))
            cell_map.append({
                "col": col,
                "row": direction,
                "asset": name,
                "tile_key": asset.get("tile_key"),
                "path": str(cell_path),
            })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    atlas.save(output_path)

    result = {
        "atlas_path": str(output_path),
        "atlas_size": [atlas_width, atlas_height],
        "cell_size": cell_size,
        "columns": num_cols,
        "rows": directions,
        "total_cells": len(cell_map),
        "cell_map": cell_map,
    }
    manifest_path = output_path.parent / "atlas_manifest.json"
    manifest_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble 8×8 environment atlas")
    parser.add_argument("--input", required=True, help="Directory with render_metadata.json")
    parser.add_argument("--output", required=True, help="Output atlas PNG path")
    args = parser.parse_args()

    input_dir = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    result = assemble_atlas(input_dir, output_path)
    print(f"Atlas: {result['atlas_size'][0]}×{result['atlas_size'][1]}")
    print(f"Cells: {result['total_cells']}/{result['columns'] * result['rows']}")
    print(f"Output: {result['atlas_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
