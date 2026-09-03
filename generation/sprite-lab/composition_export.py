"""Build and export saved Sprite Lab relationships as reusable GLB files."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

import model_cache
import composition_schema
import relationship_catalog as rel


BASE = Path(__file__).resolve().parent
EXPORT_ROOT = BASE / "work" / "composition-exports"
BLENDER_WORKER = BASE / "blender_composition_export.py"
EXPORT_SCHEMA = "sprite_lab.composition_export/v1"


def _export_key(relationship_id: str) -> str:
    return hashlib.sha256(str(relationship_id).encode("utf-8")).hexdigest()[:24]


def export_path(relationship_id: str) -> Path:
    return EXPORT_ROOT / f"{_export_key(relationship_id)}.glb"


def export_url(relationship_id: str) -> str:
    filename = export_path(relationship_id).name
    return f"/composition-exports/{quote(filename, safe='')}"


def describe_export(relationship: dict[str, Any]) -> dict[str, Any]:
    relationship_id = str(relationship.get("id") or "")
    path = export_path(relationship_id)
    ready = bool(relationship_id and path.is_file())
    return {
        "schema": EXPORT_SCHEMA,
        "ready": ready,
        "format": "glb",
        "filename": path.name,
        "url": export_url(relationship_id) if relationship_id else None,
        "bytes": path.stat().st_size if ready else 0,
    }


def _manifest_records() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    manifest = rel.load_relationship_state()
    assets = {
        str(item.get("id")): item
        for item in manifest.get("assets", [])
        if isinstance(item, dict) and item.get("id")
    }
    animations = {
        str(item.get("id")): item
        for item in manifest.get("animations", [])
        if isinstance(item, dict) and item.get("id")
    }
    return assets, animations


def _converted_model(asset_id: str, assets: dict[str, dict[str, Any]]) -> Path:
    asset = assets.get(str(asset_id))
    if asset is None:
        raise KeyError(f"asset não encontrado: {asset_id}")
    if str(asset.get("format", "")).casefold() not in model_cache.MODEL_FORMATS:
        raise ValueError(f"asset sem formato 3D exportável: {asset_id}")
    return model_cache.model_path(str(asset_id))


def _build_blender_command(
    character_path: Path,
    animation_path: Path | None,
    animation: dict[str, Any] | None,
    component_requests: list[dict[str, Any]],
    output: Path,
) -> list[str]:
    """Build the worker command, omitting animation flags for static assets."""
    command = [
        os.environ.get("SPRITE_LAB_BLENDER", "blender"),
        "--background",
        "--factory-startup",
        "--python",
        str(BLENDER_WORKER),
        "--",
        "--character",
        str(character_path),
    ]
    if animation_path:
        if animation is None:
            raise ValueError("animation metadata is required when animation_path is set")
        command.extend(
            [
                "--animation",
                str(animation_path),
                "--action-name",
                str(animation.get("action_name") or animation.get("clip_name") or ""),
            ]
        )
    command.extend(
        [
            "--components",
            json.dumps(component_requests, ensure_ascii=False),
            "--output",
            str(output),
        ]
    )
    return command


def export_relationship(
    relationship: dict[str, Any],
    timeout: float = 900.0,
) -> dict[str, Any]:
    relationship_id = str(relationship.get("id") or "")
    character_id = str(relationship.get("character_asset_id") or "")
    animation_id = str(relationship.get("animation_id") or "").strip()
    if not relationship_id or not character_id:
        raise ValueError("composição exige id e mesh principal")

    assets, animations = _manifest_records()
    animation = animations.get(animation_id) if animation_id else None
    if animation_id and animation is None:
        raise KeyError(f"animação não encontrada: {animation_id}")
    animation_asset_id = str(animation.get("asset_id") or "") if animation else ""
    if animation and not animation_asset_id:
        raise ValueError(f"animação sem asset pai: {animation_id}")

    character_path = _converted_model(character_id, assets)
    animation_path = _converted_model(animation_asset_id, assets) if animation else None
    components = composition_schema.normalize_components(relationship)
    component_requests = []
    for component in components:
        component_asset = _converted_model(str(component["asset_id"]), assets)
        component_requests.append(
            {
                **component,
                "path": str(component_asset),
            }
        )

    output = export_path(relationship_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.glb")
    if temporary.exists():
        temporary.unlink()
    command = _build_blender_command(
        character_path,
        animation_path,
        animation,
        component_requests,
        temporary,
    )

    executable = shutil.which(command[0]) or command[0]
    completed = subprocess.run(
        [executable, *command[1:]],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    log_path = output.with_suffix(".log")
    log_path.write_text(
        (completed.stdout or "") + "\n" + (completed.stderr or ""),
        encoding="utf-8",
    )
    if completed.returncode != 0 or not temporary.is_file():
        if temporary.exists():
            temporary.unlink()
        details = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise RuntimeError(details[-1] if details else "exportação GLB da composição falhou")
    temporary.replace(output)

    # Keep filesystem paths inside the worker request/logs.  The API descriptor
    # is persisted and is consumed by the browser, so it must remain portable.
    public_components = [
        {key: value for key, value in component.items() if key != "path"}
        for component in components
    ]
    descriptor = describe_export(relationship)
    descriptor.update(
        {
            "character_asset_id": character_id,
            "animation_id": animation_id or None,
            "weapon_asset_id": next(
                (item["asset_id"] for item in public_components if item["role"] == "weapon"),
                None,
            ),
            "components": public_components,
        }
    )
    return descriptor
