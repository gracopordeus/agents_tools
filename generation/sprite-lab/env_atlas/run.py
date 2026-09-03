#!/usr/bin/env python3
"""
Environment Atlas Pipeline
Renderiza 8 assets × 8 direções para criar um atlas 8×8.
"""
import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Environment Atlas Pipeline")
    parser.add_argument("--blender", default="blender", help="Caminho do Blender")
    parser.add_argument("--output", default=None, help="Diretório de saída")
    parser.add_argument("--assets", default=None, help="Asset selection JSON")
    parser.add_argument("--dry-run", action="store_true", help="Apenas mostrar comando")
    args = parser.parse_args()

    base_dir = Path(__file__).parent
    render_script = base_dir.parent / "render_env_atlas.py"
    assets_path = args.assets or str(base_dir / "asset_selection.json")
    output_path = args.output or str(base_dir / "output")

    cmd = [
        sys.executable,
        str(render_script),
        "--blender", args.blender,
        "--assets", assets_path,
        "--output", output_path,
    ]

    if args.dry_run:
        print(f"Would run: {' '.join(cmd)}")
        return

    print(f"Running: {' '.join(cmd)}")
    print(f"Profile: env_atlas_v1")
    print(f"Assets: 8")

    result = subprocess.run(cmd, capture_output=False)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
