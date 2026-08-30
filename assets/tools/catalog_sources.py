#!/usr/bin/env python3
"""Build a provenance-aware inventory from multiple 3D asset sources."""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

PIPELINE_VERSION = "0.1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return value or "unknown"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def matches_exclude(relative: str, patterns: list[str]) -> bool:
    path = PurePosixPath(relative)
    return any(fnmatch.fnmatch(relative, pattern) or path.match(pattern) for pattern in patterns)


def extension_kind(name: str) -> str:
    extension = Path(name).suffix.casefold()
    return {
        ".fbx": "model_fbx",
        ".gltf": "model_gltf",
        ".glb": "model_glb",
        ".bin": "gltf_buffer",
        ".obj": "model_obj",
        ".blend": "blender_scene",
        ".png": "texture_png",
        ".jpg": "preview_image",
        ".jpeg": "preview_image",
        ".json": "metadata_json",
        ".txt": "documentation",
        ".zip": "source_archive",
    }.get(extension, "other")


def classify(relative: str, source_kind: str) -> tuple[str, list[str]]:
    normalized = relative.replace("\\", "/")
    lowered = normalized.casefold()
    name = Path(normalized).stem.casefold()
    tags = {"source_" + source_kind}

    if "license" in lowered:
        return "license", sorted(tags | {"provenance"})
    if "readme" in lowered:
        return "documentation", sorted(tags | {"provenance"})

    if source_kind == "universal_animation_library":
        suffix = Path(normalized).suffix.casefold()
        if suffix in {".fbx", ".gltf", ".glb", ".blend"}:
            if "female mannequin" in lowered:
                return "character_base", sorted(tags | {"mannequin", "female_mannequin"})
            if suffix in {".fbx", ".gltf", ".glb"}:
                if "_rm" in name:
                    tags.add("root_motion")
                else:
                    tags.add("root_motion_disabled")
                return "animation", sorted(tags | {"animation_library"})

    if source_kind == "quaternius_weapon_source":
        suffix = Path(normalized).suffix.casefold()
        if suffix in {".fbx", ".obj", ".blend"}:
            weapon_tags = {"weapon_asset"}
            if name in {"claymore", "sword_big"} or "two handed" in lowered:
                weapon_tags.add("two_handed")
            for weapon_type in (
                "sword", "claymore", "axe", "hammer", "spear", "bow",
                "dagger", "scythe", "shield", "arrow",
            ):
                if weapon_type in name:
                    weapon_tags.add(weapon_type)
            return "weapon", sorted(tags | weapon_tags)
        if suffix == ".mtl":
            return "material", sorted(tags | {"weapon_material"})

    if source_kind == "quaternius_nature_source":
        suffix = Path(normalized).suffix.casefold()
        if suffix in {".fbx", ".blend", ".obj", ".gltf", ".glb"}:
            nature_tags = {"environment_prop"}
            for nature_type in (
                "tree", "flower", "grass", "bush", "plant", "rock", "petal",
                "palm", "birch", "maple", "pine",
            ):
                if nature_type in name:
                    nature_tags.add(nature_type)
            return "environment_prop", sorted(tags | nature_tags)
        if suffix in {".jpg", ".jpeg"} and "preview" in lowered:
            return "preview", sorted(tags | {"reference_image"})

    if "texture" in lowered or Path(normalized).suffix.casefold() in {".png", ".jpg", ".jpeg"}:
        category = "texture"
    elif "outfits/" in lowered:
        category = "character_outfit"
        tags.add("complete_outfit")
    elif "modular parts/" in lowered:
        category = "character_part"
        tags.add("modular_part")
    elif "animation" in lowered:
        category = "animation"
    elif Path(normalized).suffix.casefold() in {".fbx", ".gltf", ".glb", ".obj", ".blend"}:
        category = "model"
    else:
        category = "other"

    for token in ("male", "female", "peasant", "ranger", "arms", "body", "feet", "legs", "head", "hood", "pauldron", "pauldrons", "boots"):
        if token in name or token in lowered:
            tags.add(token)
    return category, sorted(tags)


def make_asset(source: dict[str, Any], relative: str, size: int,
               storage: str, archive_member: str | None = None) -> dict[str, Any]:
    source_id = source["id"]
    category, tags = classify(relative, source.get("kind", "unknown"))
    suffix = Path(relative).suffix.casefold().lstrip(".") or "file"
    asset = {
        "id": f"{source_id}__{slugify(relative)}",
        "source_id": source_id,
        "relative_path": relative,
        "format": suffix,
        "kind": extension_kind(relative),
        "category": category,
        "tags": tags,
        "size_bytes": size,
        "storage": storage,
        "license": source.get("license"),
    }
    if archive_member is not None:
        asset["archive"] = str(Path(source["archive"]).expanduser().resolve())
        asset["archive_member"] = archive_member
    else:
        asset["path"] = str((Path(source["root"]) / relative).expanduser().resolve())
    return asset


def scan_source(source: dict[str, Any]) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    if source.get("archive"):
        archive_path = Path(source["archive"]).expanduser().resolve()
        if not archive_path.is_file():
            raise FileNotFoundError(f"source archive does not exist: {archive_path}")
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                relative = PurePosixPath(info.filename).as_posix()
                assets.append(make_asset(source, relative, info.file_size, "archive", info.filename))
        return assets

    root = Path(source["root"]).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"source root does not exist: {root}")
    patterns = source.get("exclude", [])
    files = (candidate for candidate in root.rglob("*") if candidate.is_file())
    for path in sorted(files, key=lambda candidate: candidate.relative_to(root).as_posix().casefold()):
        relative = path.relative_to(root).as_posix()
        if matches_exclude(relative, patterns):
            continue
        assets.append(make_asset(source, relative, path.stat().st_size, "filesystem"))
    return assets


def build_catalog(registry_path: Path, output_path: Path) -> dict[str, Any]:
    registry = read_json(registry_path)
    sources = registry.get("sources", [])
    assets: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    for source in sources:
        discovered = scan_source(source)
        assets.extend(discovered)
        source_summaries.append({
            "id": source["id"],
            "name": source.get("name", source["id"]),
            "assets": len(discovered),
            "categories": dict(sorted(Counter(asset["category"] for asset in discovered).items())),
        })
    assets.sort(key=lambda asset: asset["id"])
    catalog = {
        "schema": "quatemius_asset_catalog/v1",
        "pipeline_version": PIPELINE_VERSION,
        "created_at": utc_now(),
        "registry": str(registry_path.expanduser().resolve()),
        "source_count": len(sources),
        "asset_count": len(assets),
        "sources": source_summaries,
        "assets": assets,
    }
    write_json(output_path, catalog)
    return catalog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Index multiple Quatemius asset sources")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index", help="scan registered sources")
    index.add_argument("--registry", type=Path, default=Path("catalog/sources.json"))
    index.add_argument("--output", type=Path, default=Path("catalog/assets.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "index":
        catalog = build_catalog(args.registry, args.output)
        print(f"QUATEMIUS_CATALOG_OK sources={catalog['source_count']} assets={catalog['asset_count']} output={args.output}")
        for source in catalog["sources"]:
            print(f"SOURCE {source['id']} assets={source['assets']} categories={source['categories']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
