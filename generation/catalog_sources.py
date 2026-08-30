#!/usr/bin/env python3
"""Build the Sprite Lab asset catalog from a declarative source registry.

The registry describes where source packages live.  This tool scans directories
and ZIP archives, classifies files, records provenance and writes the catalog
consumed by ``tools/sprite-lab/catalog.py``.

The operation is intentionally non-destructive: source files are never moved,
renamed or deleted.  Re-running ``index`` replaces only the generated JSON
manifests through an atomic rename.

Examples::

    python /home/ggnp/tools/generation/catalog_sources.py init
    python /home/ggnp/tools/generation/catalog_sources.py index --dry-run
    python /home/ggnp/tools/generation/catalog_sources.py index
    python /home/ggnp/tools/generation/catalog_sources.py validate
"""
from __future__ import annotations

import argparse
import copy
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator

try:
    from path_config import ASSET_ROOT
except ImportError:  # pragma: no cover - useful when imported outside generation/
    ASSET_ROOT = Path(__file__).resolve().parents[1] / "source-assets"


PIPELINE_VERSION = "1.1.0"
REGISTRY_SCHEMA = "sprite_lab.source_registry/v1"
CATALOG_SCHEMA = "sprite_lab.asset_catalog/v1"
REPORT_SCHEMA = "sprite_lab.catalog_index_report/v1"

DEFAULT_EXTENSIONS = {
    "dds",
    "fbx",
    "glb",
    "gltf",
    "jpeg",
    "jpg",
    "png",
    "tga",
    "webp",
}
CONTAINER_EXTENSIONS = {"zip"}
TEXTURE_EXTENSIONS = {"dds", "jpeg", "jpg", "png", "tga", "webp"}
IGNORED_DIRECTORIES = {".git", ".godot", "__pycache__", ".import", "tmp"}


DEFAULT_SOURCES: list[dict[str, Any]] = [
    {
        "id": "quaternius_universal_animation_library_standard",
        "name": "Quaternius Universal Animation Library [Standard]",
        "kind": "quaternius",
        "license": "CC0-1.0",
        "archive": "Universal Animation Library[Standard].zip",
        "category_by_extension": {
            "fbx": "animation",
            "blend": "animation_reference",
            "glb": "animation_reference",
            "png": "reference_sprite",
        },
        "tags": ["quaternius", "animation", "ual1"],
    },
    {
        "id": "quaternius_universal_animation_library_2_standard",
        "name": "Quaternius Universal Animation Library 2 [Standard]",
        "kind": "quaternius",
        "license": "CC0-1.0",
        "archive": "Universal Animation Library 2[Standard].zip",
        "category_by_extension": {
            "fbx": "animation",
            "blend": "animation_reference",
            "glb": "animation_reference",
            "png": "reference_sprite",
        },
        "tags": ["quaternius", "animation", "ual2"],
    },
    {
        "id": "quaternius_modular_character_outfits_fantasy_standard",
        "name": "Quaternius Modular Character Outfits - Fantasy [Standard]",
        "kind": "quaternius",
        "license": "CC0-1.0",
        "archive": "Modular Character Outfits - Fantasy[Standard].zip",
        "category_by_extension": {
            "fbx": "character",
            "glb": "character",
            "gltf": "character",
        },
        "tags": ["quaternius", "character", "outfit", "fantasy"],
    },
    {
        "id": "quaternius_stylized_nature_megakit_standard",
        "name": "Quaternius Stylized Nature MegaKit [Standard]",
        "kind": "quaternius",
        "license": "CC0-1.0",
        "archive": "Stylized Nature MegaKit[Standard].zip",
        "category_by_extension": {
            "fbx": "environment",
            "blend": "environment_reference",
            "glb": "environment",
            "gltf": "environment",
            "png": "environment_texture",
        },
        "tags": ["quaternius", "environment", "nature"],
    },
    {
        "id": "quaternius_ultimate_modular_ruins_pack",
        "name": "Quaternius Ultimate Modular Ruins Pack",
        "kind": "quaternius",
        "license": "CC0-1.0",
        "archive": "Ultimate Modular Ruins Pack - Aug 2021-20260825T033033Z-1-001.zip",
        "category_by_extension": {
            "fbx": "environment",
            "blend": "environment_reference",
            "glb": "environment",
            "gltf": "environment",
            "png": "environment_texture",
        },
        "tags": ["quaternius", "environment", "ruins"],
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    """Return a stable identifier suitable for a catalog key."""
    normalized = unicodedata.normalize("NFKD", value)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", normalized)
    return normalized.strip("_").lower() or "unknown"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _normalize_extensions(value: Any) -> set[str]:
    result: set[str] = set()
    for item in _as_list(value):
        text = str(item).lower().strip()
        if text.startswith("."):
            text = text[1:]
        if text:
            result.add(text)
    if value is None:
        return set(DEFAULT_EXTENSIONS)
    return result & DEFAULT_EXTENSIONS


def _resolve_path(value: str | Path, catalog_root: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (catalog_root / path).resolve()


def _relative_path(path: Path, catalog_root: Path) -> str:
    try:
        return path.resolve().relative_to(catalog_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _safe_member(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    member = PurePosixPath(normalized)
    if member.is_absolute() or ".." in member.parts:
        return None
    return member.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info, "r") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extension(name: str) -> str:
    return Path(name).suffix.lower().lstrip(".")


def _matches_exclude(name: str, patterns: Iterable[str]) -> bool:
    normalized = name.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def _source_paths(source: dict[str, Any], catalog_root: Path) -> list[tuple[Path, str]]:
    """Return configured paths and whether each path is a root or archive."""
    configured: list[tuple[Path, str]] = []
    for key in ("root", "path", "archive"):
        for value in _as_list(source.get(key)):
            configured.append((_resolve_path(str(value), catalog_root), key))
    for value in _as_list(source.get("roots")):
        configured.append((_resolve_path(str(value), catalog_root), "root"))
    for value in _as_list(source.get("archives")):
        configured.append((_resolve_path(str(value), catalog_root), "archive"))
    unique: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for path, kind in configured:
        if path not in seen:
            seen.add(path)
            unique.append((path, kind))
    return unique


def _iter_source_items(
    source: dict[str, Any], catalog_root: Path
) -> tuple[list[tuple[Path, Path, str]], list[tuple[Path, Path, str]]]:
    """Return direct files and archive files as ``(path, root, kind)`` tuples."""
    direct: list[tuple[Path, Path, str]] = []
    archives: list[tuple[Path, Path, str]] = []
    seen_direct: set[Path] = set()
    seen_archives: set[Path] = set()

    for configured, kind in _source_paths(source, catalog_root):
        if not configured.exists():
            continue
        if configured.is_file():
            if configured.suffix.lower() == ".zip" or kind == "archive":
                if configured not in seen_archives:
                    archives.append((configured, configured.parent, kind))
                    seen_archives.add(configured)
            elif configured not in seen_direct:
                direct.append((configured, configured.parent, kind))
                seen_direct.add(configured)
            continue

        for path in sorted(configured.rglob("*"), key=lambda item: item.as_posix().casefold()):
            try:
                relative_parts = path.relative_to(configured).parts
            except ValueError:
                relative_parts = path.parts
            if not path.is_file() or any(
                part in IGNORED_DIRECTORIES for part in relative_parts
            ):
                continue
            if path.suffix.lower() == ".zip":
                if path not in seen_archives:
                    archives.append((path, configured, "archive"))
                    seen_archives.add(path)
            elif path not in seen_direct:
                direct.append((path, configured, "root"))
                seen_direct.add(path)
    return direct, archives


def _category(source: dict[str, Any], name: str, extension: str) -> str:
    by_extension = source.get("category_by_extension", {})
    if isinstance(by_extension, dict) and extension in by_extension:
        return str(by_extension[extension])
    if source.get("category"):
        return str(source["category"])
    lowered = name.lower()
    if extension == "fbx":
        if any(token in lowered for token in ("character", "outfit", "body", "mannequin")):
            return "character"
        if any(
            token in lowered
            for token in (
                "animation",
                "animations",
                "locomotion",
                "motion",
                "attack",
                "idle",
                "walk",
                "run",
            )
        ):
            return "animation"
        if any(token in lowered for token in ("sword", "shield", "weapon", "axe", "bow")):
            return "weapon"
        # An FBX is a container format, not evidence that the file contains
        # animation.  The deep animation probe can later promote an asset when
        # an explicit ``--all-fbx`` scan is requested.
        return "model"
    if extension in {"gltf", "glb"}:
        return "character" if "character" in lowered or "outfit" in lowered else "model"
    if extension in TEXTURE_EXTENSIONS:
        return "texture"
    if extension == "json":
        return "metadata"
    return "asset"


def _kind(category: str, name: str = "") -> str:
    if category in {"animation", "animation_reference"}:
        normalized_name = name.casefold()
        filename = Path(name).name.casefold()
        is_ual2_mannequin = filename in {"ual2_standard.fbx", "ual2_standard_rm.fbx"}
        if "mannequin" in normalized_name or is_ual2_mannequin:
            return "character"
        return "animation"
    if category in {"character", "character_base"}:
        return "character"
    if category.startswith("weapon"):
        return "weapon"
    if category.startswith("composite"):
        return "composite"
    if category.startswith("reference"):
        return "reference"
    return category


def _asset_id(source_id: str, reference: str, digest: str | None) -> str:
    base = f"{slugify(source_id)}__{slugify(reference)}"
    return base if not digest else base


def _record_base(
    source: dict[str, Any],
    catalog_root: Path,
    reference: str,
    extension: str,
    size_bytes: int,
    digest: str | None,
    archive: Path | None,
    source_root: Path | None,
    member: str | None = None,
) -> dict[str, Any]:
    source_id = str(source["id"])
    category = _category(source, reference, extension)
    source_reference = f"{_relative_path(archive, catalog_root)}!{member}" if archive and member else reference
    tags = {str(tag) for tag in _as_list(source.get("tags"))}
    tags.update({f"category:{category}", f"format:{extension}"})
    record: dict[str, Any] = {
        "id": _asset_id(source_id, source_reference, digest),
        "name": Path(member or reference).stem,
        "source_id": source_id,
        "source": source.get("name", source_id),
        "kind": _kind(category, member or reference),
        "category": category,
        "format": extension,
        "relative_path": member or reference,
        "archive": _relative_path(archive, catalog_root) if archive else None,
        "source_root": _relative_path(source_root, catalog_root) if source_root else None,
        "license": source.get("license", ""),
        "tags": sorted(tags),
        "size_bytes": size_bytes,
        "sha256": digest,
    }
    return record


def _iter_records(
    source: dict[str, Any],
    catalog_root: Path,
    no_hash: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    extensions = _normalize_extensions(source.get("extensions"))
    excludes = [str(value) for value in _as_list(source.get("exclude"))]
    direct, archives = _iter_source_items(source, catalog_root)
    records: list[dict[str, Any]] = []
    skipped = Counter()

    for path, root, _ in direct:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        extension = _extension(relative)
        if extension not in extensions or _matches_exclude(relative, excludes):
            skipped["filtered"] += 1
            continue
        digest = None if no_hash else _sha256_file(path)
        records.append(
            _record_base(
                source,
                catalog_root,
                relative,
                extension,
                path.stat().st_size,
                digest,
                None,
                root,
            )
        )

    for archive, root, _ in archives:
        try:
            archive_relative = archive.relative_to(root).as_posix()
        except ValueError:
            archive_relative = archive.name
        try:
            with zipfile.ZipFile(archive) as handle:
                infos = sorted(handle.infolist(), key=lambda item: item.filename.casefold())
                for info in infos:
                    member = _safe_member(info.filename)
                    if info.is_dir() or member is None:
                        skipped["unsafe_or_directory"] += 1
                        continue
                    extension = _extension(member)
                    if extension not in extensions or _matches_exclude(member, excludes):
                        skipped["filtered"] += 1
                        continue
                    digest = None if no_hash else _sha256_zip_member(handle, info)
                    records.append(
                        _record_base(
                            source,
                            catalog_root,
                            archive_relative,
                            extension,
                            int(info.file_size),
                            digest,
                            archive,
                            root,
                            member,
                        )
                    )
        except (OSError, zipfile.BadZipFile) as exc:
            skipped[f"archive_error:{type(exc).__name__}"] += 1

    missing: list[str] = []
    for key in ("root", "path", "archive", "roots", "archives"):
        for value in _as_list(source.get(key)):
            if not _resolve_path(str(value), catalog_root).exists():
                missing.append(str(value))

    summary = {
        "id": source["id"],
        "name": source.get("name", source["id"]),
        "license": source.get("license", ""),
        "kind": source.get("kind", "asset"),
        "root": source.get("root"),
        "archives": source.get("archives") or source.get("archive"),
        "assets": len(records),
        "categories": sorted({record["category"] for record in records}),
        "missing": sorted(set(missing)),
        "skipped": dict(skipped),
    }
    return records, summary


def _declared_source_paths(
    sources: Iterable[dict[str, Any]], catalog_root: Path
) -> set[Path]:
    paths: set[Path] = set()
    for source in sources:
        for path, _ in _source_paths(source, catalog_root):
            paths.add(path.resolve())
    return paths


def _is_declared_candidate(candidate: Path, declared: set[Path]) -> bool:
    candidate = candidate.resolve()
    return any(candidate == path or path.is_relative_to(candidate) for path in declared)


def _auto_discovered_sources(
    registry: dict[str, Any], catalog_root: Path
) -> list[dict[str, Any]]:
    """Create stable source entries for new top-level files or folders.

    This keeps the registry useful as an inbox: a newly downloaded ZIP does not
    need a manual JSON edit before the watcher can index it. Explicit entries
    still win and retain their declared license/category metadata.
    """
    if not registry.get("auto_discover", False):
        return []
    root = _resolve_path(str(registry.get("auto_discover_root", ".")), catalog_root)
    if not root.is_dir():
        return []

    sources = [source for source in registry.get("sources", []) if isinstance(source, dict)]
    declared = _declared_source_paths(sources, catalog_root)
    used_ids = {str(source.get("id", "")) for source in sources}
    discovered: list[dict[str, Any]] = []
    excluded_names = IGNORED_DIRECTORIES | {"catalog"}

    for candidate in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if candidate.name in excluded_names or candidate.name.startswith("."):
            continue
        if _is_declared_candidate(candidate, declared):
            continue
        if (
            candidate.is_file()
            and _extension(candidate.name) not in DEFAULT_EXTENSIONS
            and _extension(candidate.name) not in CONTAINER_EXTENSIONS
        ):
            continue
        if not candidate.is_file() and not candidate.is_dir():
            continue

        base_id = f"incoming__{slugify(candidate.stem if candidate.is_file() else candidate.name)}"
        source_id = base_id
        counter = 2
        while source_id in used_ids:
            source_id = f"{base_id}_{counter}"
            counter += 1
        used_ids.add(source_id)
        relative = _relative_path(candidate, catalog_root)
        source: dict[str, Any] = {
            "id": source_id,
            "name": f"Incoming: {candidate.name}",
            "kind": "incoming",
            "license": registry.get(
                "auto_discover_license", "Unverified; configure source license"
            ),
            "category_by_extension": {},
            "tags": ["incoming", "auto_discovered"],
        }
        source["archive" if candidate.is_file() and candidate.suffix.lower() == ".zip" else "root"] = relative
        discovered.append(source)
    return discovered


def load_registry(path: Path) -> tuple[dict[str, Any], Path]:
    path = path.expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") not in {None, REGISTRY_SCHEMA}:
        raise ValueError(f"schema de registry incompatível: {data.get('schema')!r}")
    raw_root = data.get("catalog_root")
    catalog_root = _resolve_path(raw_root, path.parent.parent) if raw_root else path.parent.parent
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise ValueError("registry deve conter uma lista 'sources'")
    ids = [str(source.get("id", "")) for source in sources if isinstance(source, dict)]
    if any(not source_id for source_id in ids):
        raise ValueError("cada fonte precisa de um id")
    if len(ids) != len(set(ids)):
        raise ValueError("ids de fonte duplicados")
    return data, catalog_root.resolve()


def build_catalog(
    registry_path: Path,
    source_ids: set[str] | None = None,
    no_hash: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    registry, catalog_root = load_registry(registry_path)
    sources = [source for source in registry["sources"] if isinstance(source, dict)]
    sources.extend(_auto_discovered_sources(registry, catalog_root))
    all_records: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    duplicate_ids: Counter[str] = Counter()

    for source in sorted(sources, key=lambda item: str(item["id"])):
        source_id = str(source["id"])
        if source_ids and source_id not in source_ids:
            continue
        records, summary = _iter_records(source, catalog_root, no_hash)
        for record in records:
            duplicate_ids[record["id"]] += 1
            all_records.append(record)
        summaries.append(summary)

    seen: set[str] = set()
    for record in all_records:
        original = record["id"]
        candidate = original
        suffix = (record.get("sha256") or "duplicate")[:8]
        counter = 1
        while candidate in seen:
            candidate = f"{original}__{suffix}_{counter}"
            counter += 1
        record["id"] = candidate
        seen.add(candidate)
    all_records.sort(key=lambda item: item["id"])

    try:
        registry_reference = registry_path.resolve().relative_to(catalog_root).as_posix()
    except ValueError:
        registry_reference = str(registry_path.resolve())
    catalog = {
        "schema": CATALOG_SCHEMA,
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": utc_now(),
        "catalog_root": str(catalog_root),
        "registry": registry_reference,
        "source_count": len(summaries),
        "asset_count": len(all_records),
        "sources": summaries,
        "assets": all_records,
    }
    report = {
        "schema": REPORT_SCHEMA,
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": catalog["generated_at"],
        "registry": str(registry_path.resolve()),
        "catalog_root": str(catalog_root),
        "summary": {
            "sources": len(summaries),
            "assets": len(all_records),
            "missing_sources": sum(bool(item["missing"]) for item in summaries),
            "duplicate_ids": sum(count - 1 for count in duplicate_ids.values() if count > 1),
        },
        "sources": summaries,
    }
    return catalog, report


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def index_manifests(
    registry_path: Path,
    output_path: Path,
    report_path: Path | None = None,
    source_ids: set[str] | None = None,
    no_hash: bool = False,
) -> dict[str, Any]:
    catalog, report = build_catalog(
        registry_path,
        source_ids=source_ids,
        no_hash=no_hash,
    )
    write_json_atomic(output_path, catalog)
    write_json_atomic(report_path or output_path.with_name("index_report.json"), report)
    return report


def _notify_catalog_finished(
    report: dict[str, Any], animation_report: dict[str, Any] | None = None
) -> None:
    """Send a best-effort desktop notification without affecting indexing."""
    if os.environ.get("SPRITE_LAB_NOTIFY", "1").strip().lower() in {
        "0", "false", "no", "off"
    }:
        return

    summary = report.get("summary", {})
    assets = int(summary.get("assets", 0))
    sources = int(summary.get("sources", 0))
    missing = int(summary.get("missing_sources", 0))
    body = f"{assets} assets catalogados em {sources} fontes"
    if missing:
        body += f" ({missing} fontes ausentes)"
    if animation_report:
        animation_summary = animation_report.get("summary", {})
        body += f"; {int(animation_summary.get('animations', 0))} animações indexadas"

    configured = os.environ.get("SPRITE_LAB_NOTIFY_COMMAND", "").strip()
    commands: list[list[str]]
    if configured:
        commands = [[configured, "Sprite Lab", body]]
    else:
        commands = [
            ["notify-send", "--app-name=Sprite Lab", "--urgency=normal",
             "--expire-time=5000", "Catálogo atualizado", body],
            ["hyprctl", "notify", "2", "5000", "0", f"Catálogo atualizado: {body}"],
        ]

    for command in commands:
        executable = shutil.which(command[0])
        if not executable:
            continue
        try:
            completed = subprocess.run(
                [executable, *command[1:]],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            return


def _watch_snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """Return a cheap snapshot that ignores generated catalog manifests."""
    if not root.is_dir():
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(root)
            if "catalog" in relative.parts or any(
                part in IGNORED_DIRECTORIES for part in relative.parts
            ):
                continue
            stat = path.stat()
        except (OSError, ValueError):
            continue
        snapshot[relative.as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def watch_catalog(
    registry_path: Path,
    output_path: Path,
    report_path: Path | None,
    animation_output_path: Path,
    animation_report_path: Path,
    animation_cache_path: Path,
    interval: float,
    debounce: float,
    no_hash: bool,
    notify: bool,
    probe_animations: bool,
    animation_all_fbx: bool,
    blender: str | None,
) -> int:
    """Watch source files and re-index after a stable debounce window."""
    _, catalog_root = load_registry(registry_path)
    interval = max(0.2, interval)
    debounce = max(0.5, debounce)
    previous = _watch_snapshot(catalog_root)
    pending_since: float | None = None
    print(
        f"WATCHING {catalog_root} interval={interval:g}s debounce={debounce:g}s",
        flush=True,
    )
    try:
        # The service is also safe to start after files were copied manually.
        report = index_manifests(
            registry_path, output_path, report_path=report_path, no_hash=no_hash
        )
        print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True), flush=True)
        animation_report = None
        if probe_animations:
            try:
                from animation_catalog import index_animation_catalog

                animation_report = index_animation_catalog(
                    assets_path=output_path,
                    output_path=animation_output_path,
                    report_path=animation_report_path,
                    cache_path=animation_cache_path,
                    all_fbx=animation_all_fbx,
                    blender=blender,
                )
                print(
                    json.dumps(animation_report["summary"], ensure_ascii=False, sort_keys=True),
                    flush=True,
                )
            except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                print(f"ANIMATION_INDEX_ERROR {exc}", flush=True)
        if notify:
            _notify_catalog_finished(report, animation_report)
        while True:
            time.sleep(interval)
            current = _watch_snapshot(catalog_root)
            if current != previous:
                previous = current
                pending_since = time.monotonic()
                print("SOURCE_CHANGE_DETECTED", flush=True)
            if pending_since is None or time.monotonic() - pending_since < debounce:
                continue
            try:
                report = index_manifests(
                    registry_path, output_path, report_path=report_path, no_hash=no_hash
                )
                print(
                    json.dumps(report["summary"], ensure_ascii=False, sort_keys=True),
                    flush=True,
                )
                animation_report = None
                if probe_animations:
                    try:
                        from animation_catalog import index_animation_catalog

                        animation_report = index_animation_catalog(
                            assets_path=output_path,
                            output_path=animation_output_path,
                            report_path=animation_report_path,
                            cache_path=animation_cache_path,
                            all_fbx=animation_all_fbx,
                            blender=blender,
                        )
                        print(
                            json.dumps(animation_report["summary"], ensure_ascii=False, sort_keys=True),
                            flush=True,
                        )
                    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                        print(f"ANIMATION_INDEX_ERROR {exc}", flush=True)
                if notify:
                    _notify_catalog_finished(report, animation_report)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"INDEX_ERROR {exc}", flush=True)
            pending_since = None
    except KeyboardInterrupt:
        print("WATCH_STOPPED", flush=True)
        return 0


def init_registry(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if path.exists():
        raise FileExistsError(f"registry já existe: {path}")
    catalog_root = path.parent.parent
    registry = {
        "schema": REGISTRY_SCHEMA,
        "pipeline_version": PIPELINE_VERSION,
        "catalog_root": str(catalog_root),
        "auto_discover": True,
        "auto_discover_root": ".",
        "auto_discover_license": "Unverified; configure source license",
        "sources": copy.deepcopy(DEFAULT_SOURCES),
    }
    write_json_atomic(path, registry)
    return registry


def validate_catalog(path: Path, check_sources: bool = True) -> list[str]:
    path = path.expanduser().resolve()
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"catalog inválido: {exc}"]

    if data.get("schema") != CATALOG_SCHEMA:
        errors.append(f"schema incompatível: {data.get('schema')!r}")
    assets = data.get("assets")
    if not isinstance(assets, list):
        return errors + ["campo assets não é uma lista"]
    if data.get("asset_count") != len(assets):
        errors.append("asset_count não corresponde à lista assets")
    ids = [item.get("id") for item in assets if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append("ids de asset duplicados")
    catalog_root = Path(data.get("catalog_root", path.parent.parent)).expanduser()
    if not catalog_root.is_absolute():
        catalog_root = (path.parent.parent / catalog_root).resolve()

    if not check_sources:
        return errors
    for item in assets:
        if not isinstance(item, dict):
            errors.append("asset não é objeto")
            continue
        archive_value = item.get("archive")
        member = item.get("relative_path")
        if archive_value:
            archive = _resolve_path(str(archive_value), catalog_root)
            if not archive.is_file():
                errors.append(f"archive ausente: {archive}")
                continue
            safe_member = _safe_member(str(member or ""))
            if safe_member is None:
                errors.append(f"member inseguro: {member}")
                continue
            try:
                with zipfile.ZipFile(archive) as handle:
                    info = handle.getinfo(safe_member)
                    expected_size = item.get("size_bytes")
                    if expected_size is not None and int(expected_size) != info.file_size:
                        errors.append(f"tamanho divergente: {item.get('id')}")
                    expected_hash = item.get("sha256")
                    if expected_hash and _sha256_zip_member(handle, info) != expected_hash:
                        errors.append(f"sha256 divergente: {item.get('id')}")
            except (KeyError, OSError, zipfile.BadZipFile) as exc:
                errors.append(f"member ausente ou ZIP inválido {item.get('id')}: {exc}")
        elif item.get("source_root") and check_sources:
            root = _resolve_path(str(item["source_root"]), catalog_root)
            source_path = (root / str(member or "")).resolve()
            if not source_path.is_file():
                errors.append(f"arquivo ausente: {source_path}")
    return errors


def _default_registry_path() -> Path:
    return ASSET_ROOT / "catalog" / "sources.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Organizador automático do catálogo Sprite Lab")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="cria um registry padrão sem sobrescrever arquivo existente")
    init.add_argument("--registry", type=Path, default=_default_registry_path())

    index = sub.add_parser("index", help="indexa diretórios e ZIPs descritos pelo registry")
    index.add_argument("--registry", type=Path, default=_default_registry_path())
    index.add_argument("--output", type=Path, default=ASSET_ROOT / "catalog" / "assets.json")
    index.add_argument("--report", type=Path, default=None)
    index.add_argument("--source-id", action="append", default=[])
    index.add_argument("--no-hash", action="store_true", help="não calcula SHA-256 dos arquivos")
    index.add_argument("--dry-run", action="store_true", help="não grava manifests")
    index.add_argument("--no-notify", action="store_true")
    index.add_argument("--no-animation-probe", action="store_true")
    index.add_argument("--animation-all-fbx", action="store_true")
    index.add_argument("--blender", default=None)
    index.add_argument("--animation-output", type=Path, default=None)
    index.add_argument("--animation-report", type=Path, default=None)
    index.add_argument("--animation-cache", type=Path, default=None)

    watch = sub.add_parser(
        "watch", help="observa fontes e reindexa após novas alterações estáveis"
    )
    watch.add_argument("--registry", type=Path, default=_default_registry_path())
    watch.add_argument("--output", type=Path, default=ASSET_ROOT / "catalog" / "assets.json")
    watch.add_argument("--report", type=Path, default=None)
    watch.add_argument("--interval", type=float, default=2.0)
    watch.add_argument("--debounce", type=float, default=3.0)
    watch.add_argument("--no-hash", action="store_true")
    watch.add_argument("--no-notify", action="store_true")
    watch.add_argument("--no-animation-probe", action="store_true")
    watch.add_argument("--animation-all-fbx", action="store_true")
    watch.add_argument("--blender", default=None)
    watch.add_argument("--animation-output", type=Path, default=None)
    watch.add_argument("--animation-report", type=Path, default=None)
    watch.add_argument("--animation-cache", type=Path, default=None)

    validate = sub.add_parser("validate", help="valida o catálogo e as fontes referenciadas")
    validate.add_argument("--manifest", type=Path, default=ASSET_ROOT / "catalog" / "assets.json")
    validate.add_argument("--no-sources", action="store_true")
    validate.add_argument("--animation-manifest", type=Path, default=None)
    validate.add_argument("--no-animations", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "init":
        try:
            registry = init_registry(args.registry)
        except FileExistsError as exc:
            print(f"ERROR {exc}")
            return 2
        print(f"REGISTRY_CREATED {args.registry.resolve()} sources={len(registry['sources'])}")
        return 0

    if args.command == "index":
        animation_error = False
        animation_report = None
        try:
            if args.dry_run:
                _, report = build_catalog(
                    args.registry,
                    source_ids=set(args.source_id) or None,
                    no_hash=args.no_hash,
                )
            else:
                report = index_manifests(
                    args.registry,
                    args.output,
                    report_path=args.report,
                    source_ids=set(args.source_id) or None,
                    no_hash=args.no_hash,
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR {exc}")
            return 2
        print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
        if not args.dry_run and not args.no_animation_probe:
            try:
                from animation_catalog import index_animation_catalog

                animation_report = index_animation_catalog(
                    assets_path=args.output,
                    output_path=args.animation_output or args.output.with_name("animations.json"),
                    report_path=args.animation_report or args.output.with_name("animation_index_report.json"),
                    cache_path=args.animation_cache or args.output.with_name("animation-probe-cache"),
                    source_ids=set(args.source_id) or None,
                    all_fbx=args.animation_all_fbx,
                    blender=args.blender,
                )
                print(
                    json.dumps(animation_report["summary"], ensure_ascii=False, sort_keys=True)
                )
            except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
                print(f"ANIMATION_INDEX_ERROR {exc}")
                animation_error = True
        if not args.dry_run and not args.no_notify:
            _notify_catalog_finished(report, animation_report if not animation_error else None)
        return 0 if (report["summary"]["assets"] and not animation_error) or args.dry_run else 1

    if args.command == "watch":
        try:
            return watch_catalog(
                args.registry,
                args.output,
                args.report,
                args.animation_output or args.output.with_name("animations.json"),
                args.animation_report or args.output.with_name("animation_index_report.json"),
                args.animation_cache or args.output.with_name("animation-probe-cache"),
                args.interval,
                args.debounce,
                args.no_hash,
                not args.no_notify,
                not args.no_animation_probe,
                args.animation_all_fbx,
                args.blender,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR {exc}")
            return 2

    errors = validate_catalog(args.manifest, check_sources=not args.no_sources)
    if not args.no_animations:
        animation_manifest = args.animation_manifest or args.manifest.with_name("animations.json")
        try:
            from animation_catalog import validate_animation_catalog

            errors.extend(validate_animation_catalog(animation_manifest, args.manifest))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"manifesto de animações inválido: {exc}")
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1
    print(f"CATALOG_VALID {args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
