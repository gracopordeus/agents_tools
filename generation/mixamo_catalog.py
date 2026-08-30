#!/usr/bin/env python3
"""Index, plan and run a standalone Mixamo render catalog.

The catalog is intentionally independent from the game runtime.  An input FBX
is treated as a visual source asset: its skin, weapons and attached props are
kept in the Blender render, while the output is a deterministic 2D reference
sheet plus provenance metadata.

Typical usage::

    python /home/ggnp/tools/generation/mixamo_catalog.py sync \
        --source mixamo/downloads --catalog mixamo/catalog \
        --output artifacts/mixamo_catalog
    python /home/ggnp/tools/generation/mixamo_catalog.py index \
        --source mixamo/catalog --output artifacts/mixamo_catalog
    python /home/ggnp/tools/generation/mixamo_catalog.py run \
        --manifest artifacts/mixamo_catalog/catalog.json
    python /home/ggnp/tools/generation/mixamo_catalog.py validate \
        --manifest artifacts/mixamo_catalog/catalog.json

Expected source layout::

    mixamo/catalog/characters/<character>/<animation>.fbx
    mixamo/catalog/characters/<character>/T-Pose.fbx

Flat folders are accepted too.  In that case the parent directory becomes the
character name and a file at the source root is assigned to ``unknown``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PIPELINE_VERSION = "0.2.0"
DEFAULT_DIRECTIONS = 8
DEFAULT_PHASES = 8
DEFAULT_CELL = 256
DEFAULT_FPS = 10
DEFAULT_ROWS = ("w", "nw", "e", "ne", "n", "sw", "s", "se")


def slugify(value: str) -> str:
    """Return a stable filesystem/JSON identifier from a human name."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return value or "unknown"


def build_job_id(character: str, animation: str) -> str:
    return f"{slugify(character)}__{slugify(animation)}"


def discover_fbx(source: Path) -> list[Path]:
    """Discover real FBX files in deterministic order."""
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"source directory does not exist: {source}")
    return sorted(
        (path for path in source.rglob("*") if path.is_file() and path.suffix.lower() == ".fbx"),
        key=lambda path: path.relative_to(source).as_posix().casefold(),
    )


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_character_base(path: Path) -> bool:
    """Return true for normalized FBXs that provide skin but no catalog motion."""
    return slugify(path.stem) in {"character", "base_character", "ch36_nonpbr"}


def character_base_for(path: Path, source: Path) -> Path | None:
    """Find the optional skin FBX associated with an animation FBX."""
    try:
        relative = path.resolve().relative_to(source.resolve())
    except ValueError:
        return None
    parts = list(relative.parts)
    lowered = [slugify(part) for part in parts[:-1]]
    if "characters" not in lowered:
        return None
    index = lowered.index("characters")
    if index + 1 >= len(parts):
        return None
    character_dir = source.joinpath(*parts[: index + 2])
    candidate = character_dir / "character.fbx"
    return candidate.resolve() if candidate.is_file() and candidate.resolve() != path.resolve() else None


def _sha256_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info, "r") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_zip_fbx_infos(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        name = Path(info.filename)
        if info.is_dir() or name.is_absolute() or ".." in name.parts:
            continue
        if name.suffix.lower() == ".fbx":
            infos.append(info)
    return infos


def _character_aliases_from_json(data: Any) -> dict[str, str]:
    """Extract flexible id/name pairs from MixamoHarvester-style JSON."""
    aliases: dict[str, str] = {}
    id_keys = ("id", "character_id", "characterId", "model_id", "modelId", "product_id", "productId", "uuid", "slug")
    name_keys = ("name", "display_name", "displayName", "character_name", "characterName", "title")

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            identifiers = [value[key] for key in id_keys if key in value and value[key] not in (None, "")]
            names = [value[key] for key in name_keys if key in value and value[key] not in (None, "")]
            if identifiers and names and isinstance(names[0], (str, int, float)):
                name = slugify(str(names[0]))
                for identifier in identifiers:
                    aliases[str(identifier)] = name
                    aliases[slugify(str(identifier))] = name
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    return aliases


def load_character_aliases(source: Path, explicit: Path | None = None) -> dict[str, str]:
    """Read character ID/name mappings without requiring a fixed JSON schema."""
    candidates = [explicit] if explicit is not None else sorted(source.rglob("characters.json"))
    aliases: dict[str, str] = {}
    for candidate in candidates:
        if candidate is None or not candidate.is_file():
            continue
        try:
            aliases.update(_character_aliases_from_json(json.loads(candidate.read_text())))
        except (OSError, json.JSONDecodeError):
            continue
    return aliases


def classify_download(path: Path, source: Path, aliases: dict[str, str],
                      default_character: str = "unknown") -> tuple[str, str, str]:
    kind, character, animation = classify_fbx(path, source, default_character)
    character = aliases.get(character, aliases.get(slugify(character), character))
    return kind, character, animation


def sync_sources(source: Path, catalog_root: Path, report_path: Path | None = None,
                 default_character: str = "unknown", characters_json: Path | None = None,
                 dry_run: bool = False) -> dict[str, Any]:
    """Normalize raw downloader output into characters/<name>/<animation>.fbx.

    Files are copied by default, so the raw downloader output remains intact.
    Existing files with the same SHA-256 are skipped.  A different file with
    the same character/animation gets a hash suffix instead of being replaced.
    """
    source = source.expanduser().resolve()
    catalog_root = catalog_root.expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"download source does not exist: {source}")
    aliases = load_character_aliases(source, characters_json)
    files = [path for path in discover_fbx(source)
             if not path.is_relative_to(catalog_root)]
    records: list[dict[str, Any]] = []
    copied = skipped = conflicts = planned = 0
    zip_assets = 0

    def place(source_label: str, source_relative: str, destination: Path, digest: str,
              character: str, animation: str, kind: str, copier) -> None:
        nonlocal copied, skipped, conflicts, planned
        action = "planned"
        final_destination = destination
        if destination.exists():
            if sha256_file(destination) == digest:
                action = "skipped_existing"
                skipped += 1
            else:
                conflicts += 1
                final_destination = destination.with_name(f"{destination.stem}__{digest[:8]}.fbx")
                if final_destination.exists() and sha256_file(final_destination) == digest:
                    action = "skipped_existing_variant"
                    skipped += 1
                elif not dry_run:
                    final_destination.parent.mkdir(parents=True, exist_ok=True)
                    copier(final_destination)
                    action = "copied_variant"
                    copied += 1
                else:
                    action = "planned_variant"
                    planned += 1
        elif not dry_run:
            final_destination.parent.mkdir(parents=True, exist_ok=True)
            copier(final_destination)
            action = "copied"
            copied += 1
        else:
            action = "planned"
            planned += 1
        records.append({
            "source": source_label,
            "source_relative": source_relative,
            "destination": str(final_destination),
            "destination_relative": final_destination.relative_to(catalog_root).as_posix(),
            "character": slugify(character),
            "animation": slugify(animation),
            "kind": kind,
            "sha256": digest,
            "action": action,
        })

    for path in files:
        kind, character, animation = classify_download(path, source, aliases, default_character)
        digest = sha256_file(path)
        destination = catalog_root / "characters" / slugify(character) / f"{slugify(animation)}.fbx"
        place(
            str(path),
            path.relative_to(source).as_posix(),
            destination,
            digest,
            character,
            animation,
            kind,
            lambda target, path=path: shutil.copy2(path, target),
        )

    zip_paths = [path for path in source.rglob("*.zip")
                 if path.is_file() and not path.is_relative_to(catalog_root)]
    for zip_path in sorted(zip_paths, key=lambda path: path.relative_to(source).as_posix().casefold()):
        with zipfile.ZipFile(zip_path) as archive:
            infos = _safe_zip_fbx_infos(archive)
            if not infos:
                continue
            base_info = max(infos, key=lambda info: info.file_size)
            character = slugify(zip_path.stem)
            base_destination = catalog_root / "characters" / character / "character.fbx"
            base_digest = _sha256_zip_member(archive, base_info)

            def copy_member(target: Path, zip_path=zip_path, member=base_info):
                with zipfile.ZipFile(zip_path) as current_archive, current_archive.open(member, "r") as source_handle:
                    with target.open("wb") as target_handle:
                        shutil.copyfileobj(source_handle, target_handle)

            place(
                f"{zip_path}!{base_info.filename}",
                f"{zip_path.relative_to(source).as_posix()}!{base_info.filename}",
                base_destination,
                base_digest,
                character,
                "character",
                "character_base",
                copy_member,
            )
            for info in infos:
                if info is base_info:
                    continue
                animation = slugify(Path(info.filename).stem)
                destination = catalog_root / "characters" / character / "animations" / f"{animation}.fbx"
                digest = _sha256_zip_member(archive, info)

                def copy_animation(target: Path, zip_path=zip_path, member=info):
                    with zipfile.ZipFile(zip_path) as current_archive, current_archive.open(member, "r") as source_handle:
                        with target.open("wb") as target_handle:
                            shutil.copyfileobj(source_handle, target_handle)

                place(
                    f"{zip_path}!{info.filename}",
                    f"{zip_path.relative_to(source).as_posix()}!{info.filename}",
                    destination,
                    digest,
                    character,
                    animation,
                    "character_animation",
                    copy_animation,
                )
                zip_assets += 1
    report = {
        "schema": "mixamo_ingest/v1",
        "created_at": _utc_now(),
        "source_root": str(source),
        "catalog_root": str(catalog_root),
        "characters_json": str(characters_json.resolve()) if characters_json else None,
        "aliases": aliases,
        "dry_run": dry_run,
        "summary": {
            "discovered": len(files) + zip_assets + len([path for path in zip_paths if path.is_file()]),
            "direct_fbx": len(files),
            "archives": len(zip_paths),
            "archive_assets": zip_assets,
            "copied": copied,
            "planned": planned,
            "skipped": skipped,
            "conflicts": conflicts,
        },
        "files": records,
    }
    if report_path is None:
        report_path = catalog_root / "ingest_report.json"
    if not dry_run:
        write_json(report_path, report)
    return report


def classify_fbx(path: Path, source: Path, default_character: str = "unknown") -> tuple[str, str, str]:
    """Classify an FBX as pose/animation and derive character identifiers."""
    source = source.expanduser()
    path = path.expanduser()
    if path.is_absolute():
        relative = path.resolve().relative_to(source.resolve())
    else:
        relative = path
    parts = list(relative.parts)
    stem = slugify(path.stem)

    lowered = [slugify(part) for part in parts[:-1]]
    if "characters" in lowered:
        index = lowered.index("characters")
        character = lowered[index + 1] if index + 1 < len(lowered) else "unknown"
    elif lowered and lowered[0] == "animations" and len(parts) == 2:
        # MixamoHarvester's flat output is ``animations/<animation>_<id>.fbx``.
        # The final token is the character/API id; keep it in the manifest so
        # two visual characters never collapse into one job.
        name, separator, character_token = stem.rpartition("_")
        if separator and name and character_token:
            character, stem = character_token, name
        else:
            character = "unknown"
    elif lowered and lowered[0] == "animations" and len(parts) > 2:
        character = slugify(path.parent.name)
    elif len(parts) > 1:
        character = slugify(path.parent.name)
    else:
        name, separator, suffix = stem.rpartition("_")
        known_flat_characters = {"manequin", "mannequin", "ybot", "xbot"}
        if separator and name and suffix in known_flat_characters:
            character, stem = suffix, name
        else:
            character = slugify(default_character)

    pose_names = {"t_pose", "tpose", "a_pose", "apose", "idle_pose", "bind_pose"}
    kind = "character_pose" if stem in pose_names else "character_animation"
    return kind, character, stem


def sample_frames(start: int, end: int, phases: int) -> list[int]:
    """Sample a loop using the first frame, excluding a duplicated last frame."""
    if phases <= 0:
        raise ValueError("phases must be positive")
    start, end = int(start), int(end)
    if end < start:
        start, end = end, start
    if start == end:
        return [start] * phases
    step = max(1, (end - start + phases - 1) // phases)
    return [start + index * step for index in range(phases)]


@dataclass(frozen=True)
class CatalogConfig:
    source: Path
    output: Path
    directions: int = DEFAULT_DIRECTIONS
    phases: int = DEFAULT_PHASES
    cell: int = DEFAULT_CELL
    fps: int = DEFAULT_FPS
    with_skin: bool = True
    default_character: str = "unknown"
    rows: tuple[str, ...] = DEFAULT_ROWS


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_job(path: Path, config: CatalogConfig) -> dict[str, Any]:
    kind, character, animation = classify_fbx(path, config.source, config.default_character)
    relative = path.resolve().relative_to(config.source.resolve()).as_posix()
    job_id = build_job_id(character, animation)
    character_source = character_base_for(path, config.source)
    job = {
        "id": job_id,
        "kind": kind,
        "character": character,
        "animation": animation,
        "source": relative,
        "source_sha256": sha256_file(path),
        "output": f"jobs/{job_id}",
        "status": "pending",
        "render": {
            "directions": config.directions,
            "rows": list(config.rows[: config.directions]),
            "phases": config.phases,
            "cell": config.cell,
            "fps": config.fps,
            "with_skin": config.with_skin,
        },
    }
    if character_source is not None:
        job["character_source"] = character_source.relative_to(config.source.resolve()).as_posix()
        job["character_source_sha256"] = sha256_file(character_source)
    return job


def build_catalog(config: CatalogConfig) -> dict[str, Any]:
    if not 1 <= config.directions <= len(DEFAULT_ROWS):
        raise ValueError(f"directions must be between 1 and {len(DEFAULT_ROWS)}")
    if config.phases <= 0 or config.cell <= 0 or config.fps <= 0:
        raise ValueError("phases, cell and fps must be positive")
    paths = [path for path in discover_fbx(config.source) if not is_character_base(path)]
    jobs = [make_job(path, config) for path in paths]
    ids = [job["id"] for job in jobs]
    duplicates = sorted({job_id for job_id in ids if ids.count(job_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate job ids; rename or reorganize sources: {duplicates}")
    return {
        "schema": "mixamo_catalog/v1",
        "pipeline_version": PIPELINE_VERSION,
        "created_at": _utc_now(),
        "source_root": str(config.source.resolve()),
        "output_root": str(config.output.resolve()),
        "source_policy": {
            "preserve_skin": config.with_skin,
            "preserve_attached_props": True,
            "source_files_private": True,
            "allowed_source_format": "fbx",
        },
        "render_defaults": {
            "directions": config.directions,
            "rows": list(config.rows[: config.directions]),
            "phases": config.phases,
            "cell": config.cell,
            "fps": config.fps,
            "transparent_background": True,
            "camera": {"type": "ORTHO", "elevation": 35.264, "azimuth": 45.0},
        },
        "jobs": jobs,
        "summary": {"total": len(jobs), "pending": len(jobs), "done": 0, "failed": 0},
    }


def merge_catalog_state(catalog: dict[str, Any], previous_path: Path) -> dict[str, Any]:
    """Keep completed jobs when a sync rebuilds the manifest."""
    if not previous_path.is_file():
        return catalog
    previous = read_catalog(previous_path)
    old_jobs = {job.get("id"): job for job in previous.get("jobs", [])}
    output_root = Path(catalog["output_root"])
    for job in catalog["jobs"]:
        old = old_jobs.get(job["id"])
        done_marker = output_root / job["output"] / "DONE.json"
        if old and old.get("source_sha256") == job.get("source_sha256") and done_marker.is_file():
            job["status"] = "done"
        else:
            job["status"] = "pending"
    _update_summary(catalog)
    return catalog


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_catalog(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if data.get("schema") != "mixamo_catalog/v1":
        raise ValueError(f"unsupported catalog schema: {data.get('schema')!r}")
    if not isinstance(data.get("jobs"), list):
        raise ValueError("catalog jobs must be a list")
    return data


def validate_catalog(catalog: dict[str, Any], check_sources: bool = True) -> list[str]:
    errors: list[str] = []
    source = Path(catalog.get("source_root", ""))
    jobs = catalog.get("jobs", [])
    seen: set[str] = set()
    for index, job in enumerate(jobs):
        prefix = f"jobs[{index}]"
        job_id = job.get("id")
        if not isinstance(job_id, str) or not job_id:
            errors.append(f"{prefix}.id is required")
            continue
        if job_id in seen:
            errors.append(f"duplicate job id: {job_id}")
        seen.add(job_id)
        for field in ("character", "animation", "source", "output"):
            if not isinstance(job.get(field), str) or not job[field]:
                errors.append(f"{prefix}.{field} is required")
        render = job.get("render", {})
        if render.get("phases", 0) <= 0:
            errors.append(f"{prefix}.render.phases must be positive")
        if render.get("cell", 0) <= 0:
            errors.append(f"{prefix}.render.cell must be positive")
        if check_sources and isinstance(job.get("source"), str):
            source_path = source / job["source"]
            if not source_path.is_file():
                errors.append(f"missing source FBX: {source_path}")
        if check_sources and isinstance(job.get("character_source"), str):
            character_path = source / job["character_source"]
            if not character_path.is_file():
                errors.append(f"missing character FBX: {character_path}")
    return errors


def _update_summary(catalog: dict[str, Any]) -> None:
    statuses = [job.get("status", "pending") for job in catalog["jobs"]]
    catalog["summary"] = {
        "total": len(statuses),
        "pending": statuses.count("pending"),
        "running": statuses.count("running"),
        "done": statuses.count("done"),
        "failed": statuses.count("failed"),
        "skipped": statuses.count("skipped"),
    }


def _blender_script() -> Path:
    return Path(__file__).with_name("blender_render_catalog.py")


def run_catalog(catalog_path: Path, blender: str = "blender", only: str | None = None,
                force: bool = False, props: str = "none") -> int:
    """Render all pending jobs, continuing after individual failures."""
    catalog = read_catalog(catalog_path)
    source = Path(catalog["source_root"])
    output_root = Path(catalog["output_root"])
    output_root.mkdir(parents=True, exist_ok=True)
    jobs = [job for job in catalog["jobs"] if only is None or job["id"] == only]
    if only is not None and not jobs:
        raise ValueError(f"job not found: {only}")

    for job in jobs:
        job_dir = output_root / job["output"]
        done_marker = job_dir / "DONE.json"
        if done_marker.exists() and not force:
            job["status"] = "skipped"
            print(f"SKIP {job['id']} (DONE.json exists)")
            continue
        job["status"] = "running"
        write_json(catalog_path, catalog)
        source_fbx = source / job["source"]
        render = job["render"]
        cells_dir = job_dir / "cells"
        command = [
            blender,
            "--background",
            "-noaudio",
            "--python",
            str(_blender_script()),
            "--",
            "--fbx",
            str(source_fbx),
            "--out",
            str(cells_dir),
            "--rows",
            str(render["directions"]),
            "--phases",
            str(render["phases"]),
            "--cell",
            str(render["cell"]),
            "--fps",
            str(render["fps"]),
            "--character",
            job["character"],
            "--animation",
            job["animation"],
            "--props",
            props,
        ]
        if job.get("character_source"):
            command.extend(["--character-fbx", str(source / job["character_source"])])
        print(f"RUN  {job['id']}")
        headless_env = os.environ.copy()
        # Blender 5.2 can keep a background process alive while trying to
        # connect to PipeWire.  Catalog jobs never need audio.
        headless_env["ALSOFT_DRIVERS"] = "null"
        headless_env["SDL_AUDIODRIVER"] = "dummy"
        result = subprocess.run(command, check=False, env=headless_env)
        if result.returncode != 0:
            job["status"] = "failed"
            job["error"] = f"blender exited with {result.returncode}"
            print(f"FAIL {job['id']}: {job['error']}", file=sys.stderr)
            _update_summary(catalog)
            write_json(catalog_path, catalog)
            continue
        try:
            from build_catalog_sheet import compose_job

            compose_job(cells_dir, job_dir, fps=render["fps"])
            write_json(done_marker, {"job": job["id"], "completed_at": _utc_now()})
            job["status"] = "done"
            job.pop("error", None)
        except Exception as exc:  # keep the batch resumable
            job["status"] = "failed"
            job["error"] = f"composition failed: {exc}"
            print(f"FAIL {job['id']}: {job['error']}", file=sys.stderr)
        _update_summary(catalog)
        write_json(catalog_path, catalog)
    _update_summary(catalog)
    write_json(catalog_path, catalog)
    return 1 if catalog["summary"]["failed"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone Mixamo catalog pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="discover FBXs and write catalog.json")
    index.add_argument("--source", required=True, type=Path)
    index.add_argument("--output", required=True, type=Path)
    index.add_argument("--phases", type=int, default=DEFAULT_PHASES)
    index.add_argument("--cell", type=int, default=DEFAULT_CELL)
    index.add_argument("--fps", type=int, default=DEFAULT_FPS)
    index.add_argument("--directions", type=int, default=DEFAULT_DIRECTIONS)
    index.add_argument("--default-character", default="unknown")

    sync = sub.add_parser("sync", help="ingest raw downloads, normalize names and update the catalog")
    sync.add_argument("--source", required=True, type=Path, help="raw downloader output")
    sync.add_argument("--catalog", required=True, type=Path, help="normalized FBX catalog root")
    sync.add_argument("--output", required=True, type=Path, help="render manifest/output root")
    sync.add_argument("--characters-json", type=Path)
    sync.add_argument("--default-character", default="unknown")
    sync.add_argument("--phases", type=int, default=DEFAULT_PHASES)
    sync.add_argument("--cell", type=int, default=DEFAULT_CELL)
    sync.add_argument("--fps", type=int, default=DEFAULT_FPS)
    sync.add_argument("--directions", type=int, default=DEFAULT_DIRECTIONS)
    sync.add_argument("--dry-run", action="store_true")

    validate = sub.add_parser("validate", help="validate a catalog manifest")
    validate.add_argument("--manifest", required=True, type=Path)
    validate.add_argument("--no-sources", action="store_true")

    run = sub.add_parser("run", help="render and compose pending jobs")
    run.add_argument("--manifest", required=True, type=Path)
    run.add_argument("--blender", default="blender")
    run.add_argument("--only")
    run.add_argument("--force", action="store_true")
    run.add_argument(
        "--props",
        choices=("none", "sword_shield"),
        default="none",
        help="procedural placeholder equipment for each rendered job",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "sync":
        report = sync_sources(
            args.source,
            args.catalog,
            characters_json=args.characters_json,
            default_character=args.default_character,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            print(json.dumps(report["summary"], ensure_ascii=False))
            return 0
        config = CatalogConfig(
            source=args.catalog,
            output=args.output,
            directions=args.directions,
            phases=args.phases,
            cell=args.cell,
            fps=args.fps,
        )
        manifest = args.output / "catalog.json"
        catalog = build_catalog(config)
        catalog = merge_catalog_state(catalog, manifest)
        catalog["last_ingest"] = {
            "report": str((args.catalog / "ingest_report.json").resolve()),
            "summary": report["summary"],
        }
        write_json(manifest, catalog)
        print(
            f"SYNC_OK discovered={report['summary']['discovered']} "
            f"copied={report['summary']['copied']} skipped={report['summary']['skipped']} "
            f"planned={report['summary']['planned']} "
            f"manifest={manifest}"
        )
        return 0
    if args.command == "index":
        config = CatalogConfig(
            source=args.source,
            output=args.output,
            directions=args.directions,
            phases=args.phases,
            cell=args.cell,
            fps=args.fps,
            default_character=args.default_character,
        )
        catalog = build_catalog(config)
        errors = validate_catalog(catalog, check_sources=True)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        manifest = args.output / "catalog.json"
        write_json(manifest, catalog)
        print(f"INDEX_OK jobs={len(catalog['jobs'])} manifest={manifest}")
        return 0
    if args.command == "validate":
        catalog = read_catalog(args.manifest)
        errors = validate_catalog(catalog, check_sources=not args.no_sources)
        if errors:
            for error in errors:
                print(f"ERROR {error}", file=sys.stderr)
            return 1
        print(f"VALIDATE_OK jobs={len(catalog['jobs'])}")
        return 0
    return run_catalog(
        args.manifest,
        blender=args.blender,
        only=args.only,
        force=args.force,
        props=args.props,
    )


if __name__ == "__main__":
    raise SystemExit(main())
