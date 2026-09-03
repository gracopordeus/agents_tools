#!/usr/bin/env python3
"""Semantic relationship catalog for the canonical Sprite Lab pipeline.

The physical manifests remain the source of truth for provenance:
``source-assets/catalog/assets.json`` and ``animations.json``.  This sidecar
adds user-owned semantic annotations and explicit relationships without
modifying those generated manifests.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENERATION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GENERATION_DIR))
from animation_catalog import DEFAULT_ASSETS, DEFAULT_OUTPUT as DEFAULT_ANIMATIONS  # noqa: E402
from animation_catalog import _asset_source_path  # noqa: E402
from path_config import ASSET_ROOT  # noqa: E402
import composition_schema  # noqa: E402


RELATIONSHIP_SCHEMA = "sprite_lab.relationship_catalog/v1"
ANNOTATION_SCHEMA = "sprite_lab.semantic_annotations/v1"
DEFAULT_OUTPUT = ASSET_ROOT / "catalog" / "relationships.json"
DEFAULT_ANNOTATIONS = ASSET_ROOT / "catalog" / "semantic_annotations.json"
MODEL_FORMATS = {"fbx", "glb", "gltf", "blend", "obj", "dae"}
IMAGE_FORMATS = {"png", "jpg", "jpeg", "webp", "tga", "dds"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def asset_kind(record: dict[str, Any]) -> str:
    category = str(record.get("category", "")).casefold()
    if category == "weapon" or category.startswith("weapon"):
        return "weapon"
    mannequin_name = " ".join(
        (str(record.get("name", "")), str(record.get("relative_path", "")))
    ).casefold()
    source_id = str(record.get("source_id", "")).casefold()
    filename = Path(str(record.get("relative_path", ""))).name.casefold()
    is_ual2_mannequin = (
        source_id == "quaternius_universal_animation_library_2_standard"
        and filename in {"ual2_standard.fbx", "ual2_standard_rm.fbx"}
    )
    if category in {"animation", "animation_reference"} and (
        "mannequin" in mannequin_name or is_ual2_mannequin
    ):
        return "character"
    if category in {"character", "character_base"}:
        return "character"
    if category in {"animation", "animation_reference"}:
        return "animation"
    if str(record.get("format", "")).casefold() in IMAGE_FORMATS:
        return "texture"
    return "model"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") or "asset"


def _annotation_defaults(record: dict[str, Any]) -> dict[str, Any]:
    kind = asset_kind(record)
    return {
        "asset_id": record.get("id"),
        "semantic_name": record.get("name", ""),
        "kind": kind,
        "roles": [],
        "tags": [],
        "family": "",
        "usage": [],
        "weapon_class": "",
        "handedness": "",
        "notes": "",
        "review_status": "unreviewed",
        "updated_at": None,
    }


def load_annotations(path: Path = DEFAULT_ANNOTATIONS) -> dict[str, dict[str, Any]]:
    data = read_json(path, {"schema": ANNOTATION_SCHEMA, "annotations": []})
    rows = data.get("annotations", []) if isinstance(data, dict) else []
    return {
        str(row.get("asset_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("asset_id")
    }


def save_annotations(rows: dict[str, dict[str, Any]], path: Path = DEFAULT_ANNOTATIONS) -> None:
    write_json_atomic(
        path,
        {
            "schema": ANNOTATION_SCHEMA,
            "generated_at": utc_now(),
            "annotations": sorted(rows.values(), key=lambda item: str(item.get("asset_id"))),
        },
    )


def load_relationship_state(path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    data = read_json(path, {})
    if not isinstance(data, dict):
        return {}
    return data


def build_relationship_catalog(
    assets_path: Path = DEFAULT_ASSETS,
    animations_path: Path = DEFAULT_ANIMATIONS,
    output_path: Path = DEFAULT_OUTPUT,
    annotations_path: Path = DEFAULT_ANNOTATIONS,
) -> dict[str, Any]:
    assets_data = read_json(assets_path, {})
    animations_data = read_json(animations_path, {})
    annotations = load_annotations(annotations_path)
    previous = load_relationship_state(output_path)
    previous_relationships = [
        item for item in previous.get("relationships", []) if isinstance(item, dict)
    ]
    catalog_root = Path(assets_data.get("catalog_root", assets_path.parent.parent)).expanduser()
    if not catalog_root.is_absolute():
        catalog_root = (assets_path.parent.parent / catalog_root).resolve()

    assets: list[dict[str, Any]] = []
    for source in assets_data.get("assets", []):
        if not isinstance(source, dict):
            continue
        record = {
            "id": source.get("id"),
            "name": source.get("name"),
            "kind": asset_kind(source),
            "category": source.get("category"),
            "format": source.get("format"),
            "source_id": source.get("source_id"),
            "source": source.get("source"),
            "license": source.get("license"),
            "relative_path": source.get("relative_path"),
            "archive": source.get("archive"),
            "source_root": source.get("source_root"),
            "sha256": source.get("sha256"),
            "tags": source.get("tags", []),
            "annotation": annotations.get(str(source.get("id")), _annotation_defaults(source)),
        }
        assets.append(record)

    animations: list[dict[str, Any]] = []
    for source in animations_data.get("animations", []):
        if not isinstance(source, dict):
            continue
        animations.append(
            {
                "id": source.get("id"),
                "asset_id": source.get("asset_id"),
                "asset_name": source.get("asset_name"),
                "source_id": source.get("source_id"),
                "source": source.get("source"),
                "action_name": source.get("action_name"),
                "clip_name": source.get("clip_name", source.get("action_name")),
                "category": source.get("category", "unknown"),
                "semantic_tags": source.get("semantic_tags", []),
                "frame_count": source.get("frame_count"),
                "fps": source.get("fps"),
                "duration_seconds": source.get("duration_seconds"),
                "loop": source.get("loop", False),
                "loop_name_hint": source.get("loop_name_hint", False),
                "root_motion": source.get("root_motion", {}),
                "rig_fingerprint": source.get("rig_fingerprint"),
                "classification_confidence": source.get("classification_confidence", 0.0),
            }
        )

    asset_ids = {str(item["id"]) for item in assets if item.get("id")}
    animation_ids = {str(item["id"]) for item in animations if item.get("id")}
    relationships: list[dict[str, Any]] = []
    for previous_relationship in previous_relationships:
        if str(previous_relationship.get("character_asset_id")) not in asset_ids:
            continue
        animation_id = str(previous_relationship.get("animation_id") or "").strip()
        if animation_id and animation_id not in animation_ids:
            continue
        try:
            components = composition_schema.normalize_components(previous_relationship)
        except ValueError:
            continue
        if any(str(item["asset_id"]) not in asset_ids for item in components):
            continue
        relationship = dict(previous_relationship)
        relationship["animation_id"] = animation_id or None
        relationship["components"] = components
        weapon_id, shield_id = composition_schema.legacy_asset_ids(components)
        relationship["weapon_asset_id"] = weapon_id
        relationship["shield_asset_id"] = shield_id
        relationships.append(relationship)
    manifest = {
        "schema": RELATIONSHIP_SCHEMA,
        "generated_at": utc_now(),
        "pipeline_version": "1.0.0",
        "assets_catalog": str(assets_path.resolve()),
        "animations_catalog": str(animations_path.resolve()),
        "catalog_root": str(catalog_root),
        "asset_count": len(assets),
        "animation_count": len(animations),
        "relationship_count": len(relationships),
        "assets": sorted(assets, key=lambda item: str(item.get("id"))),
        "animations": sorted(animations, key=lambda item: str(item.get("id"))),
        "relationships": relationships,
    }
    write_json_atomic(output_path, manifest)
    return manifest


def update_annotation(
    asset_id: str,
    patch: dict[str, Any],
    assets_path: Path = DEFAULT_ASSETS,
    annotations_path: Path = DEFAULT_ANNOTATIONS,
) -> dict[str, Any]:
    assets_data = read_json(assets_path, {})
    source = next(
        (item for item in assets_data.get("assets", []) if str(item.get("id")) == asset_id),
        None,
    )
    if source is None:
        raise KeyError(f"asset não encontrado: {asset_id}")
    rows = load_annotations(annotations_path)
    row = {**_annotation_defaults(source), **rows.get(asset_id, {}), **patch}
    row["asset_id"] = asset_id
    row["kind"] = asset_kind(source)
    row["updated_at"] = utc_now()
    rows[asset_id] = row
    save_annotations(rows, annotations_path)
    return row


def add_relationship(
    payload: dict[str, Any],
    output_path: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    if not payload.get("character_asset_id"):
        raise ValueError("relationship exige mesh principal")
    animation_id = str(payload.get("animation_id") or "").strip() or None
    components = composition_schema.normalize_components(payload)
    weapon_id, shield_id = composition_schema.legacy_asset_ids(components)
    data = load_relationship_state(output_path)
    save_as_new = payload.get("save_as_new", False)
    if not isinstance(save_as_new, bool):
        raise ValueError("save_as_new deve ser booleano")
    relationship_id = payload.get("id") if not save_as_new else None
    identity_payload = {
        key: value for key, value in payload.items()
        if key not in {"id", "save_as_new"}
    }
    relationship = {
        "id": relationship_id or hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20],
        "character_asset_id": payload["character_asset_id"],
        "animation_id": animation_id,
        "weapon_asset_id": weapon_id,
        "shield_asset_id": shield_id,
        "components": components,
        "mount": payload.get("mount", {}),
        "semantic_name": payload.get("semantic_name", ""),
        "tags": payload.get("tags", []),
        "notes": payload.get("notes", ""),
        "updated_at": utc_now(),
    }
    rows = [
        item for item in data.get("relationships", [])
        if str(item.get("id")) != str(relationship["id"])
    ]
    rows.append(relationship)
    data["relationships"] = rows
    data["relationship_count"] = len(rows)
    data["generated_at"] = utc_now()
    write_json_atomic(output_path, data)
    return relationship


def delete_relationship(
    relationship_id: str,
    output_path: Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    data = load_relationship_state(output_path)
    rows = data.get("relationships", [])
    remaining = [
        item for item in rows
        if str(item.get("id")) != str(relationship_id)
    ]
    if len(remaining) == len(rows):
        raise KeyError(f"composição não encontrada: {relationship_id}")
    deleted = next(item for item in rows if str(item.get("id")) == str(relationship_id))
    data["relationships"] = remaining
    data["relationship_count"] = len(remaining)
    data["generated_at"] = utc_now()
    write_json_atomic(output_path, data)
    return deleted


def validate_relationship_catalog(path: Path = DEFAULT_OUTPUT) -> list[str]:
    try:
        data = read_json(path, {})
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifesto inválido: {exc}"]
    errors: list[str] = []
    if data.get("schema") != RELATIONSHIP_SCHEMA:
        errors.append(f"schema incompatível: {data.get('schema')!r}")
    asset_ids = {str(item.get("id")) for item in data.get("assets", []) if isinstance(item, dict)}
    animation_ids = {str(item.get("id")) for item in data.get("animations", []) if isinstance(item, dict)}
    relationships = data.get("relationships", [])
    if data.get("relationship_count") != len(relationships):
        errors.append("relationship_count divergente")
    for item in relationships:
        if str(item.get("character_asset_id")) not in asset_ids:
            errors.append(f"personagem ausente: {item.get('id')}")
        animation_id = str(item.get("animation_id") or "").strip()
        if animation_id and animation_id not in animation_ids:
            errors.append(f"animação ausente: {item.get('id')}")
        try:
            components = composition_schema.normalize_components(item)
        except ValueError as exc:
            errors.append(f"componentes inválidos: {item.get('id')} ({exc})")
            continue
        for component in components:
            if str(component["asset_id"]) not in asset_ids:
                errors.append(
                    f"asset de componente ausente: {item.get('id')}:{component['id']}"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Catálogo semântico de relacionamentos do Sprite Lab")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    index.add_argument("--animations", type=Path, default=DEFAULT_ANIMATIONS)
    index.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    index.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.command == "index":
        manifest = build_relationship_catalog(args.assets, args.animations, args.output, args.annotations)
        print(f"RELATIONSHIPS_INDEXED assets={manifest['asset_count']} animations={manifest['animation_count']} relationships={manifest['relationship_count']}")
        return 0
    errors = validate_relationship_catalog(args.manifest)
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1
    print(f"RELATIONSHIPS_VALID {args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
