"""Orchestrate tile, prop_static, and VFX renders via Blender workers.

Parallel to sprite_render.py but for non-character asset types.
Spawns the appropriate Blender worker and builds output metadata.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import render_profile
import asset_manifest

SPRITE_LAB = Path(__file__).resolve().parent
GENERATION = SPRITE_LAB.parent

WORKER_MAP: dict[str, str] = {
    "tile": "blender_tile_atlas.py",
    "prop_static": "blender_static_mesh.py",
    "vfx": "blender_vfx_sequence.py",
}

DEFAULT_CELL = 256
DEFAULT_FPS = 10


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _worker_error(completed: subprocess.CompletedProcess[str], output: Path) -> str:
    chunks = [completed.stdout or "", completed.stderr or ""]
    worker_log = output / "worker.log"
    if worker_log.is_file():
        chunks.append(worker_log.read_text(encoding="utf-8", errors="replace"))
    lines = "\n".join(chunks).strip().splitlines()
    error_prefixes = (
        "RuntimeError:", "ValueError:", "FileNotFoundError:",
        "KeyError:", "TypeError:",
    )
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith(error_prefixes):
            return stripped
    return lines[-1].strip() if lines else "worker encerrou sem produzir metadados"


def _validate_profile(profile: dict, asset_type: str) -> None:
    cell_size = profile.get("cell_size", [DEFAULT_CELL, DEFAULT_CELL])
    if not isinstance(cell_size, list) or len(cell_size) != 2:
        raise ValueError("cell_size deve ser [width, height]")
    if cell_size[0] < 32 or cell_size[1] < 32:
        raise ValueError("cell_size mínimo é 32x32")
    if asset_type == "tile":
        if cell_size[0] != cell_size[1]:
            raise ValueError("tiles devem ser quadrados (cell_size[0] == cell_size[1])")


def generate_asset_render(
    payload: dict[str, Any],
    job_id: str,
    output_root: Path | None = None,
    blender: str | None = None,
    timeout: float = 3600.0,
) -> dict[str, Any]:
    """Generate a render for tile, prop_static, or vfx asset types."""
    normalized = asset_manifest.normalize_asset_spec(payload)
    asset_type = normalized["type"]
    if asset_type not in WORKER_MAP:
        raise ValueError(f"asset_type não suportado por tile_render: {asset_type}")

    worker_file = WORKER_MAP[asset_type]
    worker_path = SPRITE_LAB / worker_file
    if not worker_path.is_file():
        raise FileNotFoundError(f"worker não encontrado: {worker_path}")

    locked_profile_id = str(payload.get("render_profile_id") or "").strip()
    locked_profile = None
    if locked_profile_id:
        locked_profile = render_profile.load(locked_profile_id)

    profile = locked_profile or {
        "cell_size": [DEFAULT_CELL, DEFAULT_CELL],
        "ortho_scale": 1.0,
        "camera_elevation": 80.0,
        "camera_azimuth": 45.0,
        "foot_anchor": [DEFAULT_CELL // 2, DEFAULT_CELL // 2],
    }
    _validate_profile(profile, asset_type)

    output = output_root or Path(SPRITE_LAB / "work" / "asset-renders" / job_id)
    output.mkdir(parents=True, exist_ok=True)

    request: dict[str, Any] = {
        "asset_type": asset_type,
        "representation": normalized["representation"],
        "capabilities": normalized["capabilities"],
        "mesh_path": payload.get("mesh_path"),
        "variant_id": payload.get("variant_id", "unknown"),
        "asset_key": payload.get("asset_key", "unknown"),
        "tile_key": payload.get("tile_key"),
        "directions": int(payload.get("directions", 1 if asset_type == "tile" else 8)),
        "phases": int(payload.get("phases", 1)),
        "orientation_angle": float(payload.get("orientation_angle", 0.0)),
        "frame_start": int(payload.get("frame_start", 0)),
        "frame_end": int(payload.get("frame_end", 7)),
        "fps": float(payload.get("fps", DEFAULT_FPS)),
        "render_profile": profile,
        "output": str(output),
    }

    if not request["mesh_path"]:
        raise ValueError("mesh_path é obrigatório")

    request_path = output / "request.json"
    write_json_atomic(request_path, request)

    blender_command = blender or os.environ.get("SPRITE_LAB_BLENDER", "blender")
    executable = shutil.which(blender_command) or blender_command
    command = [
        executable,
        "--background",
        "--factory-startup",
        "--python",
        str(worker_path),
        "--",
        "--request",
        str(request_path),
    ]

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    (output / "worker.log").write_text(
        (completed.stdout or "") + "\n" + (completed.stderr or ""), encoding="utf-8"
    )
    if completed.returncode != 0:
        raise RuntimeError(_worker_error(completed, output))

    result_path = Path(str(request_path) + ".result.json")
    if not result_path.is_file():
        raise RuntimeError(_worker_error(completed, output))

    worker_report = json.loads(result_path.read_text(encoding="utf-8"))

    metadata = {
        "job_id": job_id,
        "asset_type": asset_type,
        "representation": normalized["representation"],
        "capabilities": normalized["capabilities"],
        "variant_id": request["variant_id"],
        "asset_key": request["asset_key"],
        "cell_size": profile.get("cell_size", [DEFAULT_CELL, DEFAULT_CELL]),
        "worker_report": worker_report,
    }

    write_json_atomic(output / "render.json", metadata)

    return metadata
