"""Orchestrator for environment atlas rendering pipeline.

Loads asset_selection.json, calls blender_env_atlas.py for rendering,
then assembles the final 8×8 atlas.

Usage:
    python render_env_atlas.py --blender /usr/bin/blender [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

SPRITE_LAB = Path(__file__).resolve().parent
POC_DIR = SPRITE_LAB / "poc" / "env_atlas"
DEFAULT_OUTPUT = POC_DIR / "output"
DEFAULT_ASSET_SELECTION = POC_DIR / "asset_selection.json"
WORKER = SPRITE_LAB / "blender_env_atlas.py"
ASSEMBLER = SPRITE_LAB / "assemble_atlas.py"


def load_profile(profile_id: str) -> dict:
    profile_path = SPRITE_LAB / "state" / "render-profiles" / f"{profile_id}.json"
    if profile_path.is_file():
        return json.loads(profile_path.read_text(encoding="utf-8"))
    return {}


def run_blender_worker(
    blender: str,
    request_path: Path,
    timeout: int = 600,
) -> dict:
    cmd = [
        blender, "--background", "--python", str(WORKER),
        "--", "--request", str(request_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(SPRITE_LAB),
    )
    if result.returncode != 0:
        return {
            "status": "error",
            "returncode": result.returncode,
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }
    return {"status": "ok"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Render environment atlas")
    parser.add_argument("--blender", default="/usr/bin/blender", help="Blender path")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, no render")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output directory")
    parser.add_argument("--assets", default=str(DEFAULT_ASSET_SELECTION), help="Asset selection JSON")
    parser.add_argument("--optimize", action="store_true", help="Enable dynamic ortho_scale optimization")
    args = parser.parse_args()

    asset_selection = Path(args.assets)
    if not asset_selection.is_file():
        print(f"ERRO: {asset_selection} não encontrado")
        return 1

    selection = json.loads(asset_selection.read_text(encoding="utf-8"))
    profile_id = selection.get("render_profile", "tile_reference_v1")
    profile = load_profile(profile_id)
    assets = selection.get("assets", [])
    atlas_config = selection.get("atlas", {})

    print(f"Profile: {profile_id}")
    print(f"Assets: {len(assets)}")
    print(f"Atlas: {atlas_config.get('total_size', ['?','?'])}")
    print()

    for asset in assets:
        fbx = Path(asset["fbx_path"])
        exists = fbx.is_file()
        status = "✅" if exists else "❌"
        print(f"  [{asset['col']}] {status} {asset['name']:20s} → {asset['tile_key']:15s} ({fbx.name})")

    print()

    if args.dry_run:
        print("DRY RUN — validação completa, sem renderização")
        return 0

    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    request = {
        "assets": assets,
        "output": str(output_dir),
        "render_profile": profile,
        "directions": 8,
        "optimize_ortho_scale": args.optimize,
        "atlas_id": f"env_atlas_{int(time.time())}",
    }
    request_path = output_dir / "request.json"
    request_path.write_text(json.dumps(request, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Renderizando {len(assets)} assets × 8 direções...")
    t0 = time.time()
    result = run_blender_worker(args.blender, request_path)
    elapsed = time.time() - t0

    if result["status"] != "ok":
        print(f"❌ Blender falhou ({elapsed:.1f}s)")
        print(result.get("stderr", "")[-500:])
        return 1

    print(f"✅ Renderização completa ({elapsed:.1f}s)")

    print("Montando atlas 8×8...")
    atlas_output = output_dir / "env_atlas.png"
    atlas_cmd = [sys.executable, str(ASSEMBLER), "--input", str(output_dir), "--output", str(atlas_output)]
    atlas_result = subprocess.run(atlas_cmd, capture_output=True, text=True)
    if atlas_result.returncode != 0:
        print(f"❌ Assembly falhou: {atlas_result.stderr}")
        return 1
    print(atlas_result.stdout)

    print("\n✅ Pipeline completa!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
