#!/usr/bin/env python3
"""Orchestrate adaptive semantic previews for the Sprite Lab web/API layer."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys

GENERATION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GENERATION_DIR))
from animation_catalog import _safe_member  # noqa: E402
from path_config import ASSET_ROOT  # noqa: E402


CATALOG_DIR = ASSET_ROOT / "catalog"
ASSETS_PATH = CATALOG_DIR / "assets.json"
ANIMATIONS_PATH = CATALOG_DIR / "animations.json"
CACHE_PATH = CATALOG_DIR / "semantic-preview-cache"
WORK_PATH = Path(__file__).resolve().parent / "work" / "semantic-previews"
BLENDER_WORKER = Path(__file__).resolve().with_name("blender_semantic_preview.py")
DEFAULT_PREVIEW_FPS = 6.0


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_manifests() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads(ASSETS_PATH.read_text(encoding="utf-8")),
        json.loads(ANIMATIONS_PATH.read_text(encoding="utf-8")),
    )


def _materialize(record: dict[str, Any], assets_catalog: dict[str, Any]) -> Path:
    root = Path(assets_catalog.get("catalog_root", ASSETS_PATH.parent.parent)).expanduser()
    if not root.is_absolute():
        root = (ASSETS_PATH.parent.parent / root).resolve()
    relative = str(record.get("relative_path", ""))
    archive_value = record.get("archive")
    if archive_value:
        archive = (root / str(archive_value)).resolve()
        member = _safe_member(relative)
        if member is None:
            raise ValueError(f"member inseguro: {relative}")
        with zipfile.ZipFile(archive) as handle:
            info = handle.getinfo(member)
            content = handle.read(info)
        digest = str(record.get("sha256") or "uncached")
        suffix = Path(member).suffix.lower() or ".asset"
        target = CACHE_PATH / f"{digest}{suffix}"
        if not target.is_file():
            CACHE_PATH.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.tmp")
            temporary.write_bytes(content)
            temporary.replace(target)
        return target
    source_root = (root / str(record.get("source_root") or ".")).resolve()
    path = (source_root / relative).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id")): row for row in rows if row.get("id")}


def _image_preview(source: Path, output: Path, resolution: int) -> dict[str, Any]:
    from PIL import Image

    image = Image.open(source).convert("RGBA")
    canvas = Image.new("RGBA", (resolution, resolution), (0, 0, 0, 0))
    image.thumbnail((int(resolution * 0.9), int(resolution * 0.9)), Image.Resampling.LANCZOS)
    canvas.alpha_composite(image, ((resolution - image.width) // 2, (resolution - image.height) // 2))
    output.mkdir(parents=True, exist_ok=True)
    front = output / "front.png"
    top = output / "top.png"
    canvas.save(front)
    canvas.save(top)
    return {"front": str(front), "top": str(top), "gif": None, "spritesheet_generated": False}


def _build_gif(frame_paths: list[str], output: Path, fps: float) -> None:
    from PIL import Image

    frames = [Image.open(path).convert("RGBA") for path in frame_paths]
    if not frames:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(20, round(1000 / max(float(fps), 1.0)))
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=duration,
        loop=0,
        disposal=2,
        transparency=0,
    )


def generate_preview(
    payload: dict[str, Any],
    job_id: str,
    output_root: Path | None = None,
    blender: str | None = None,
    timeout: float = 1800.0,
) -> dict[str, Any]:
    assets_catalog, animations_catalog = load_manifests()
    assets = _by_id(assets_catalog.get("assets", []))
    animations = _by_id(animations_catalog.get("animations", []))
    output = (output_root or WORK_PATH / job_id).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    resolution = max(128, int(payload.get("resolution", 512)))
    animation_id = payload.get("animation_id")
    animation = animations.get(str(animation_id)) if animation_id else None
    if animation_id and animation is None:
        raise KeyError(f"animação não encontrada: {animation_id}")

    if animation:
        character_id = str(payload.get("character_asset_id") or animation.get("asset_id"))
        character = assets.get(character_id)
        animation_asset = assets.get(str(animation.get("asset_id")))
        if character is None or animation_asset is None:
            raise KeyError("personagem ou asset pai da animação não encontrado")
        character_path = _materialize(character, assets_catalog)
        animation_path = _materialize(animation_asset, assets_catalog)
    else:
        character_id = str(payload.get("asset_id") or payload.get("character_asset_id"))
        character = assets.get(character_id)
        if character is None:
            raise KeyError(f"asset não encontrado: {character_id}")
        character_path = _materialize(character, assets_catalog)
        animation_path = None

    weapon_path = None
    weapon = None
    if payload.get("weapon_asset_id"):
        weapon = assets.get(str(payload["weapon_asset_id"]))
        if weapon is None:
            raise KeyError(f"arma não encontrada: {payload['weapon_asset_id']}")
        weapon_path = _materialize(weapon, assets_catalog)

    if str(character.get("format", "")).casefold() in {"png", "jpg", "jpeg", "webp", "tga", "dds"}:
        if animation:
            raise ValueError("uma textura não pode receber uma animação")
        result = _image_preview(character_path, output, resolution)
        report = {
            "schema": "sprite_lab.semantic_preview/v1",
            "job_id": job_id,
            "mode": "image",
            "asset_id": character_id,
            "created_at": utc_now(),
            **result,
        }
        write_json_atomic(output / "preview.json", report)
        return report

    request = {
        "character_path": str(character_path),
        "animation_path": str(animation_path) if animation_path else None,
        "action_name": animation.get("action_name") if animation else None,
        "weapon_path": str(weapon_path) if weapon_path else None,
        "weapon_hand": payload.get("weapon_hand", "right"),
        "weapon_scale": payload.get("weapon_scale"),
        "weapon_rotation": payload.get("weapon_rotation", [0.0, 0.0, 0.0]),
        "weapon_height_ratio": payload.get("weapon_height_ratio", 0.8),
        "resolution": resolution,
        "gif_frames": payload.get("gif_frames", 24),
        "fps": payload.get("fps", DEFAULT_PREVIEW_FPS),
        "output": str(output),
    }
    request_path = output / "request.json"
    write_json_atomic(request_path, request)
    command = [
        blender or os.environ.get("SPRITE_LAB_BLENDER", "blender"),
        "--background",
        "--factory-startup",
        "--python",
        str(BLENDER_WORKER),
        "--",
        "--request",
        str(request_path),
    ]
    executable = shutil.which(command[0]) or command[0]
    completed = subprocess.run(
        [executable, *command[1:]],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    (output / "worker.log").write_text(
        (completed.stdout or "") + "\n" + (completed.stderr or ""), encoding="utf-8"
    )
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise RuntimeError(details[-1] if details else "Blender preview falhou")
    result_path = Path(str(request_path) + ".result.json")
    if not result_path.is_file():
        raise RuntimeError("Blender encerrou sem gerar preview.json")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    gif_path = None
    if result.get("gif_frames"):
        gif_file = output / "animation.gif"
        _build_gif(
            result["gif_frames"],
            gif_file,
            float(payload.get("fps", DEFAULT_PREVIEW_FPS)),
        )
        gif_path = str(gif_file)
    report = {
        **result,
        "schema": "sprite_lab.semantic_preview/v1",
        "job_id": job_id,
        "mode": "animation" if animation else "model",
        "asset_id": character_id,
        "animation_id": animation_id,
        "animation_name": animation.get("clip_name") if animation else None,
        "weapon_asset_id": weapon.get("id") if weapon else None,
        "created_at": utc_now(),
        "gif": gif_path,
        "spritesheet_generated": False,
    }
    write_json_atomic(output / "preview.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera preview semântico sem spritesheet")
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    report = generate_preview(payload, args.job_id, args.output)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
