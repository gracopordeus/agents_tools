"""Versioned, engine-neutral contract for generated game assets.

The renderers may remain specialized, but every output is described by the
same manifest.  Binary files stay as normal artifacts; the manifest records
their relative paths, hashes and the data required by a runtime importer.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Iterable


ASSET_MANIFEST_SCHEMA = "sprite_lab.asset_manifest/v1"
ASSET_TYPES = ("actor", "prop_static", "prop_animated", "tile", "vfx")
REPRESENTATIONS = (
    "directional_sprite_atlas",
    "sprite_atlas",
    "tile_atlas",
    "frame_sequence",
)
CAPABILITIES = (
    "animated",
    "agent",
    "blocks_navigation",
    "destructible",
    "has_collision",
    "interactable",
    "occluder",
    "walkable",
)

TYPE_DEFAULTS: dict[str, dict[str, Any]] = {
    "actor": {
        "representation": "directional_sprite_atlas",
        "capabilities": ["animated", "agent"],
        "render_strategy": "blender_armature_animation",
    },
    "prop_static": {
        "representation": "sprite_atlas",
        "capabilities": [],
        "render_strategy": "blender_static_mesh",
    },
    "prop_animated": {
        "representation": "sprite_atlas",
        "capabilities": ["animated"],
        "render_strategy": "blender_armature_animation",
    },
    "tile": {
        "representation": "tile_atlas",
        "capabilities": [],
        "render_strategy": "blender_tile_atlas",
    },
    "vfx": {
        "representation": "frame_sequence",
        "capabilities": ["animated"],
        "render_strategy": "blender_vfx_sequence",
    },
}

TYPE_ALIASES = {
    "character": "actor",
    "environment_prop": "prop_static",
    "prop": "prop_static",
    "static_prop": "prop_static",
    "animated_prop": "prop_animated",
    "effect": "vfx",
}


def _string(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} é obrigatório")
    return result


def normalize_asset_spec(
    payload: dict[str, Any] | None,
    *,
    default_type: str = "actor",
) -> dict[str, Any]:
    """Normalize the user-selected asset contract before starting a job."""
    source = payload if isinstance(payload, dict) else {}
    raw_type = str(
        source.get("asset_type") or source.get("type") or default_type
    ).strip().casefold()
    asset_type = TYPE_ALIASES.get(raw_type, raw_type)
    if asset_type not in ASSET_TYPES:
        choices = ", ".join(ASSET_TYPES)
        raise ValueError(f"asset_type inválido: {raw_type}; opções: {choices}")

    defaults = TYPE_DEFAULTS[asset_type]
    representation = str(
        source.get("representation") or defaults["representation"]
    ).strip().casefold()
    if representation not in REPRESENTATIONS:
        choices = ", ".join(REPRESENTATIONS)
        raise ValueError(
            f"representation inválida: {representation}; opções: {choices}"
        )
    allowed_representations = {
        "actor": {"directional_sprite_atlas"},
        "prop_static": {"sprite_atlas"},
        "prop_animated": {"sprite_atlas"},
        "tile": {"tile_atlas"},
        "vfx": {"frame_sequence", "sprite_atlas"},
    }
    if representation not in allowed_representations[asset_type]:
        allowed = ", ".join(sorted(allowed_representations[asset_type]))
        raise ValueError(f"{asset_type} exige representation={allowed}")

    requested = source.get("capabilities")
    if requested is None:
        capabilities = list(defaults["capabilities"])
    elif not isinstance(requested, list):
        raise ValueError("capabilities deve ser uma lista")
    else:
        capabilities = []
        for value in requested:
            capability = str(value or "").strip().casefold()
            if capability not in CAPABILITIES:
                choices = ", ".join(CAPABILITIES)
                raise ValueError(
                    f"capability inválida: {capability}; opções: {choices}"
                )
            if capability not in capabilities:
                capabilities.append(capability)

    return {
        "type": asset_type,
        "representation": representation,
        "capabilities": capabilities,
        "render_strategy": defaults["render_strategy"],
        "directional": representation == "directional_sprite_atlas",
    }


def asset_contract_options() -> dict[str, Any]:
    """Return serializable options for the UI and API clients."""
    return {
        "schema": ASSET_MANIFEST_SCHEMA,
        "asset_types": [
            {
                "id": asset_type,
                "representation": defaults["representation"],
                "capabilities": list(defaults["capabilities"]),
                "render_strategy": defaults["render_strategy"],
                "available_in_composition_render": asset_type in {"actor", "prop_animated"},
            }
            for asset_type, defaults in TYPE_DEFAULTS.items()
        ],
        "representations": list(REPRESENTATIONS),
        "capabilities": list(CAPABILITIES),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_record(root: Path, path: Path | str, role: str) -> dict[str, Any]:
    """Describe a generated file without embedding its binary contents."""
    source = Path(path).expanduser().resolve()
    root_resolved = root.expanduser().resolve()
    try:
        relative = source.relative_to(root_resolved).as_posix()
    except ValueError:
        relative = str(source)
    record: dict[str, Any] = {"role": role, "path": relative}
    if source.is_file():
        record.update({"bytes": source.stat().st_size, "sha256": _sha256(source)})
    else:
        record["exists"] = False
    return record


def collect_artifacts(
    root: Path,
    entries: Iterable[tuple[str, Path | str | None]],
) -> list[dict[str, Any]]:
    """Collect deterministic artifact references, skipping absent optionals."""
    root_resolved = root.expanduser().resolve()
    result = []
    for role, path in entries:
        if path is None:
            continue
        source = Path(path).expanduser()
        if not source.is_absolute():
            source = root_resolved / source
        if source.is_file():
            result.append(artifact_record(root_resolved, source, role))
    return result


def build_manifest(
    asset_spec: dict[str, Any],
    *,
    asset_id: str,
    name: str,
    contract: dict[str, Any],
    source: dict[str, Any] | None = None,
    generation: dict[str, Any] | None = None,
    layout: dict[str, Any] | None = None,
    animation: dict[str, Any] | None = None,
    placement: dict[str, Any] | None = None,
    gameplay: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    validation: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical manifest while retaining specialized raw metadata."""
    manifest = {
        "schema": ASSET_MANIFEST_SCHEMA,
        "asset": {
            "id": _string(asset_id, "asset.id"),
            "name": _string(name, "asset.name"),
            **asset_spec,
        },
        "contract": contract,
        "source": source or {},
        "generation": generation or {},
        "layout": layout or {},
        "animation": animation or {},
        "placement": placement or {},
        "gameplay": gameplay or {},
        "runtime": runtime or {},
        "artifacts": artifacts or [],
        "validation": validation or {},
        "provenance": provenance or {},
    }
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate the cross-renderer contract and return the original object."""
    if not isinstance(manifest, dict):
        raise ValueError("manifest deve ser um objeto JSON")
    if manifest.get("schema") != ASSET_MANIFEST_SCHEMA:
        raise ValueError(f"schema de asset inválido: {manifest.get('schema')}")
    asset = manifest.get("asset")
    if not isinstance(asset, dict):
        raise ValueError("manifest.asset deve ser um objeto")
    normalize_asset_spec(asset, default_type="actor")
    for field in (
        "contract",
        "source",
        "generation",
        "layout",
        "animation",
        "placement",
        "gameplay",
        "runtime",
        "validation",
        "provenance",
    ):
        if not isinstance(manifest.get(field), dict):
            raise ValueError(f"manifest.{field} deve ser um objeto")
    if not isinstance(manifest.get("artifacts"), list):
        raise ValueError("manifest.artifacts deve ser uma lista")
    for artifact in manifest["artifacts"]:
        if not isinstance(artifact, dict) or not artifact.get("path"):
            raise ValueError("cada artifact precisa de role e path")
    return manifest


def write_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    validate_manifest(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path


def update_manifest_artifacts(
    path: Path,
    root: Path,
    entries: Iterable[tuple[str, Path | str | None]],
) -> dict[str, Any]:
    """Add newly-created channel files to an existing manifest."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    existing = {
        (item.get("role"), item.get("path")): item
        for item in manifest.get("artifacts", [])
        if isinstance(item, dict)
    }
    for item in collect_artifacts(root, entries):
        existing[(item["role"], item["path"])] = item
    manifest["artifacts"] = list(existing.values())
    write_manifest(path, manifest)
    return manifest


def copy_manifest(source: Path, destination: Path) -> None:
    """Copy a manifest when packaging an output without changing its schema."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
