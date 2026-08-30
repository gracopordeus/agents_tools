#!/usr/bin/env python3
"""Build the action-level animation catalog for the Sprite Lab.

``catalog_sources.py`` indexes physical assets.  This module indexes the
animation clips stored inside FBX files and keeps that slower inspection in a
versioned sidecar manifest.  Blender is used only as a deterministic importer;
preview rendering remains a separate generation step.

Examples::

    python3 animation_catalog.py index
    python3 animation_catalog.py index --source-id quaternius_universal_animation_library_2_standard
    python3 animation_catalog.py index --all-fbx --force
    python3 animation_catalog.py validate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from path_config import ASSET_ROOT
except ImportError:  # pragma: no cover
    ASSET_ROOT = Path(__file__).resolve().parents[1] / "source-assets"


PIPELINE_VERSION = "1.1.0"
PROBE_VERSION = "1.2.1"
ANIMATION_SCHEMA = "sprite_lab.animation_catalog/v1"
PROBE_SCHEMA = "sprite_lab.blender_animation_probe/v1"
DEFAULT_ASSETS = ASSET_ROOT / "catalog" / "assets.json"
DEFAULT_OUTPUT = ASSET_ROOT / "catalog" / "animations.json"
DEFAULT_REPORT = ASSET_ROOT / "catalog" / "animation_index_report.json"
DEFAULT_CACHE = ASSET_ROOT / "catalog" / "animation-probe-cache"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip())
    return value.strip("_").lower() or "unknown"


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _safe_member(name: str) -> str | None:
    normalized = name.replace("\\", "/")
    parts = Path(normalized).parts
    if normalized.startswith("/") or ".." in parts:
        return None
    return "/".join(parts)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify_action(name: str) -> dict[str, Any]:
    """Map common action names to controlled semantic labels.

    This is deliberately conservative: unknown names remain ``unknown`` and
    are still fully available to the user.  The classifier is metadata, not a
    replacement for the original Action name.
    """
    clip_name = name.split("|")[-1]
    lowered = re.sub(r"[^a-z0-9]+", "_", clip_name.casefold())
    rules: list[tuple[str, tuple[str, ...], float]] = [
        ("tpose", ("tpose", "bindpose", "restpose"), 0.99),
        ("death", ("death", "die", "dead", "dying", "fall"), 0.96),
        ("hit", ("hit", "hurt", "damage", "stagger", "flinch"), 0.92),
        ("dodge", ("dodge", "roll", "evade", "dash"), 0.92),
        ("block", ("block", "guard", "parry", "shield"), 0.90),
        ("cast", ("cast", "spell", "magic", "charge", "summon"), 0.86),
        (
            "attack",
            (
                "attack",
                "slash",
                "strike",
                "swing",
                "combo",
                "melee",
                "sword",
                "scratch",
                "chop",
                "throw",
                "shoot",
                "stab",
                "thrust",
                "punch",
                "kick",
            ),
            0.94,
        ),
        ("jump", ("jump", "leap"), 0.90),
        ("run", ("run", "jog", "sprint"), 0.94),
        ("walk", ("walk", "locomotion", "move", "slide"), 0.84),
        ("idle", ("idle", "stand", "breath", "rest"), 0.88),
        ("interact", ("consume", "chest", "farm", "harvest", "plant", "water", "yes"), 0.70),
    ]
    category = "unknown"
    confidence = 0.0
    for candidate, tokens, candidate_confidence in rules:
        if any(token in lowered for token in tokens):
            category = candidate
            confidence = candidate_confidence
            break

    tags = [category] if category != "unknown" else []
    weapon_tokens = ("sword", "greatsword", "axe", "mace", "spear", "bow", "shield")
    for token in weapon_tokens:
        if token in lowered:
            tags.append(token)
    explicit_no_loop = "no_loop" in lowered or "once" in lowered
    if ("loop" in lowered or category in {"idle", "walk", "run"}) and not explicit_no_loop:
        tags.append("locomotion_loop_candidate")
    return {
        "clip_name": clip_name,
        "category": category,
        "semantic_tags": sorted(set(tags)),
        "classification_confidence": confidence,
        "loop_name_hint": "loop" in lowered and "no_loop" not in lowered,
        "explicit_no_loop": explicit_no_loop,
    }


def _animation_candidate(asset: dict[str, Any], all_fbx: bool) -> bool:
    if str(asset.get("format", "")).casefold() != "fbx":
        return False
    if all_fbx:
        return True
    category = str(asset.get("category", "")).casefold()
    kind = str(asset.get("kind", "")).casefold()
    tags = {str(tag).casefold() for tag in asset.get("tags", [])}
    return category in {"animation", "animation_reference"} or kind == "animation" or "animation" in tags


def _asset_source_path(asset: dict[str, Any], catalog_root: Path, cache_root: Path) -> Path:
    """Resolve a direct asset or safely materialize one ZIP member by hash."""
    relative = str(asset.get("relative_path", ""))
    archive_value = asset.get("archive")
    if archive_value:
        archive = (catalog_root / str(archive_value)).resolve()
        member = _safe_member(relative)
        if member is None:
            raise ValueError(f"member inseguro: {relative}")
        if not archive.is_file():
            raise FileNotFoundError(archive)
        source_hash = str(asset.get("sha256") or "unknown")
        cached = cache_root / f"{source_hash}.fbx"
        if cached.is_file():
            return cached
        with zipfile.ZipFile(archive) as handle:
            info = handle.getinfo(member)
            data = handle.read(info)
        expected_hash = asset.get("sha256")
        if expected_hash and _sha256_bytes(data) != expected_hash:
            raise ValueError(f"sha256 divergente: {asset.get('id')}")
        cache_root.mkdir(parents=True, exist_ok=True)
        temporary = cache_root / f".{source_hash}.tmp"
        temporary.write_bytes(data)
        temporary.replace(cached)
        return cached

    source_root_value = asset.get("source_root") or "."
    source_root = (catalog_root / str(source_root_value)).resolve()
    path = (source_root / relative).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _existing_by_asset(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        data = _load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(item.get("asset_id")): item
        for item in data.get("assets", [])
        if isinstance(item, dict) and item.get("asset_id")
    }


def _stable_animation_id(asset: dict[str, Any], action: dict[str, Any], rig_fingerprint: str | None) -> str:
    source_hash = str(asset.get("sha256") or "unknown")
    signature = "|".join(
        (
            source_hash,
            str(action.get("name", "unknown")),
            str(action.get("frame_start", "")),
            str(action.get("frame_end", "")),
            str(rig_fingerprint or ""),
        )
    )
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
    return f"{slugify(str(asset.get('name', 'asset')))}__{slugify(str(action.get('name', 'action')))}__{digest}"


def _make_animation_record(
    asset: dict[str, Any],
    probe: dict[str, Any],
    action: dict[str, Any],
) -> dict[str, Any]:
    classification = classify_action(str(action.get("name", "unknown")))
    rig_fingerprint = probe.get("rig_fingerprint")
    animation_id = _stable_animation_id(asset, action, rig_fingerprint)
    loop_recommended = (
        classification["category"] in {"idle", "walk", "run"}
        and not classification["explicit_no_loop"]
    )
    record = {
        "id": animation_id,
        "asset_id": asset.get("id"),
        "source_id": asset.get("source_id"),
        "source": asset.get("source"),
        "asset_name": asset.get("name"),
        "action_name": action.get("name"),
        "clip_name": classification["clip_name"],
        "category": classification["category"],
        "semantic_tags": classification["semantic_tags"],
        "classification_confidence": classification["classification_confidence"],
        "frame_start": action.get("frame_start"),
        "frame_end": action.get("frame_end"),
        "frame_count": action.get("frame_count"),
        "fps": action.get("fps", probe.get("fps")),
        "duration_seconds": action.get("duration_seconds"),
        "loop": bool(action.get("loop", False)),
        "loop_name_hint": classification["loop_name_hint"],
        "loop_recommended": loop_recommended,
        "root_motion": action.get(
            "root_motion",
            {"present": False, "bone": None, "distance": 0.0, "delta": [0.0, 0.0, 0.0]},
        ),
        "fcurve_count": action.get("fcurve_count", 0),
        "keyframe_count": action.get("keyframe_count", 0),
        "animated_bone_count": action.get("animated_bone_count", 0),
        "animated_bones": action.get("animated_bones", []),
        "rig_fingerprint": rig_fingerprint,
        "probe_version": PROBE_VERSION,
    }
    return record


def _make_asset_probe(asset: dict[str, Any], raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    actions = [
        _make_animation_record(asset, raw, action)
        for action in raw.get("actions", [])
        if isinstance(action, dict)
    ]
    asset_record = {
        "asset_id": asset.get("id"),
        "asset_name": asset.get("name"),
        "source_id": asset.get("source_id"),
        "source": asset.get("source"),
        "relative_path": asset.get("relative_path"),
        "archive": asset.get("archive"),
        "source_sha256": asset.get("sha256"),
        "status": raw.get("status", "error"),
        "blender_version": raw.get("blender_version"),
        "armature_count": raw.get("armature_count", 0),
        "armatures": raw.get("armatures", []),
        "bone_count": raw.get("bone_count", 0),
        "rig_fingerprint": raw.get("rig_fingerprint"),
        "mesh_count": raw.get("mesh_count", 0),
        "vertex_count": raw.get("vertex_count", 0),
        "action_ids": [item["id"] for item in actions],
        "warnings": raw.get("warnings", []),
        "errors": raw.get("errors", []),
        "probe_version": PROBE_VERSION,
        "updated_at": utc_now(),
    }
    return asset_record, actions


def _run_blender(
    request: dict[str, Any],
    blender: str,
    worker: Path,
    workspace: Path,
    timeout: float,
) -> dict[str, Any]:
    request_path = workspace / "request.json"
    result_path = workspace / "result.json"
    write_json_atomic(request_path, request)
    command = [
        blender,
        "--background",
        "--factory-startup",
        "--python",
        str(worker),
        "--",
        "--input",
        str(request_path),
        "--output",
        str(result_path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Blender excedeu o timeout de {timeout:g}s") from exc
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip().splitlines()
        detail = details[-1] if details else "sem detalhes"
        raise RuntimeError(f"Blender retornou {completed.returncode}: {detail}")
    if not result_path.is_file():
        raise RuntimeError("Blender terminou sem gerar o resultado do probe")
    result = _load_json(result_path)
    if result.get("schema") != PROBE_SCHEMA:
        raise ValueError(f"schema de probe incompatível: {result.get('schema')!r}")
    return result


def _default_blender() -> str:
    return os.environ.get("SPRITE_LAB_BLENDER", "blender")


def index_animation_catalog(
    assets_path: Path = DEFAULT_ASSETS,
    output_path: Path = DEFAULT_OUTPUT,
    report_path: Path | None = DEFAULT_REPORT,
    cache_path: Path = DEFAULT_CACHE,
    source_ids: set[str] | None = None,
    asset_ids: set[str] | None = None,
    all_fbx: bool = False,
    force: bool = False,
    blender: str | None = None,
    timeout: float = 3600.0,
) -> dict[str, Any]:
    assets_path = assets_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    report_path = report_path.expanduser().resolve() if report_path else None
    cache_path = cache_path.expanduser().resolve()
    catalog = _load_json(assets_path)
    catalog_root = Path(catalog.get("catalog_root", assets_path.parent.parent)).expanduser()
    if not catalog_root.is_absolute():
        catalog_root = (assets_path.parent.parent / catalog_root).resolve()

    candidates = []
    for asset in catalog.get("assets", []):
        if not isinstance(asset, dict) or not _animation_candidate(asset, all_fbx):
            continue
        if source_ids and str(asset.get("source_id")) not in source_ids:
            continue
        if asset_ids and str(asset.get("id")) not in asset_ids:
            continue
        candidates.append(asset)
    candidates.sort(key=lambda item: str(item.get("id", "")).casefold())

    existing = _existing_by_asset(output_path)
    asset_records: dict[str, dict[str, Any]] = {}
    animations_by_id: dict[str, dict[str, Any]] = {}
    cached_asset_ids: set[str] = set()
    pending: list[tuple[dict[str, Any], Path]] = []
    summary = {
        "assets_considered": len(candidates),
        "assets_probed": 0,
        "assets_cached": 0,
        "assets_ok": 0,
        "assets_without_actions": 0,
        "assets_failed": 0,
        "animations": 0,
    }

    for asset in candidates:
        asset_id = str(asset.get("id"))
        old = existing.get(asset_id)
        if (
            not force
            and old
            and old.get("source_sha256") == asset.get("sha256")
            and old.get("probe_version") == PROBE_VERSION
            and old.get("status") in {"ok", "no_actions"}
        ):
            asset_records[asset_id] = old
            cached_asset_ids.add(asset_id)
            summary["assets_cached"] += 1
            continue
        try:
            pending.append((asset, _asset_source_path(asset, catalog_root, cache_path)))
        except (FileNotFoundError, KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
            asset_records[asset_id] = {
                "asset_id": asset_id,
                "asset_name": asset.get("name"),
                "source_id": asset.get("source_id"),
                "source": asset.get("source"),
                "relative_path": asset.get("relative_path"),
                "archive": asset.get("archive"),
                "source_sha256": asset.get("sha256"),
                "status": "error",
                "action_ids": [],
                "warnings": [],
                "errors": [f"materialização: {type(exc).__name__}: {exc}"],
                "probe_version": PROBE_VERSION,
                "updated_at": utc_now(),
            }

    probe_results: dict[str, dict[str, Any]] = {}
    if pending:
        blender_command = blender or _default_blender()
        blender_path = shutil.which(blender_command) or blender_command
        worker = Path(__file__).resolve().with_name("blender_animation_probe.py")
        request = {
            "schema": "sprite_lab.blender_animation_probe_request/v1",
            "probe_version": PROBE_VERSION,
            "files": [
                {
                    "asset_id": asset.get("id"),
                    "source_sha256": asset.get("sha256"),
                    "path": str(path),
                }
                for asset, path in pending
            ],
        }
        cache_path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="animation-probe-", dir=cache_path) as temporary:
            raw_result = _run_blender(
                request,
                blender_path,
                worker,
                Path(temporary),
                timeout,
            )
        probe_results = {
            str(item.get("asset_id")): item
            for item in raw_result.get("results", [])
            if isinstance(item, dict) and item.get("asset_id")
        }
        summary["assets_probed"] = len(pending)

    for asset in candidates:
        asset_id = str(asset.get("id"))
        if asset_id in asset_records:
            continue
        raw = probe_results.get(
            asset_id,
            {
                "status": "error",
                "errors": ["Blender não retornou este asset"],
                "actions": [],
            },
        )
        asset_records[asset_id], _ = _make_asset_probe(asset, raw)

    for asset_record in asset_records.values():
        asset_id = str(asset_record.get("asset_id"))
        for animation_id in asset_record.get("action_ids", []):
            # Cached asset records do not contain the flattened actions.  They
            # are recovered from the previous manifest below.
            del animation_id
        status = asset_record.get("status")
        if status == "ok":
            summary["assets_ok"] += 1
        elif status == "no_actions":
            summary["assets_without_actions"] += 1
        else:
            summary["assets_failed"] += 1

    previous_data = _load_json(output_path) if output_path.is_file() else {}
    previous_animations = {
        str(item.get("id")): item
        for item in previous_data.get("animations", [])
        if isinstance(item, dict) and item.get("id")
    }
    for asset in candidates:
        asset_id = str(asset.get("id"))
        if asset_id in probe_results:
            raw = probe_results[asset_id]
            for action in raw.get("actions", []):
                record = _make_animation_record(asset, raw, action)
                animations_by_id[record["id"]] = record
        else:
            if asset_id in cached_asset_ids:
                old = existing.get(asset_id, {})
                for action_id in old.get("action_ids", []):
                    if action_id in previous_animations:
                        animations_by_id[action_id] = previous_animations[action_id]

    animations = sorted(animations_by_id.values(), key=lambda item: str(item["id"]))
    summary["animations"] = len(animations)
    for item in asset_records.values():
        item["action_ids"] = [
            animation["id"] for animation in animations if animation.get("asset_id") == item.get("asset_id")
        ]

    manifest = {
        "schema": ANIMATION_SCHEMA,
        "pipeline_version": PIPELINE_VERSION,
        "probe_version": PROBE_VERSION,
        "generated_at": utc_now(),
        "source_catalog": str(assets_path),
        "catalog_root": str(catalog_root),
        "asset_count": len(asset_records),
        "animation_count": len(animations),
        "assets": sorted(asset_records.values(), key=lambda item: str(item["asset_id"])),
        "animations": animations,
    }
    report = {
        "schema": "sprite_lab.animation_index_report/v1",
        "pipeline_version": PIPELINE_VERSION,
        "probe_version": PROBE_VERSION,
        "generated_at": manifest["generated_at"],
        "manifest": str(output_path),
        "summary": summary,
    }
    write_json_atomic(output_path, manifest)
    if report_path:
        write_json_atomic(report_path, report)
    return report


def validate_animation_catalog(path: Path, assets_path: Path | None = None) -> list[str]:
    path = path.expanduser().resolve()
    try:
        data = _load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifesto de animações inválido: {exc}"]
    errors: list[str] = []
    if data.get("schema") != ANIMATION_SCHEMA:
        errors.append(f"schema incompatível: {data.get('schema')!r}")
    assets = data.get("assets")
    animations = data.get("animations")
    if not isinstance(assets, list):
        errors.append("campo assets não é uma lista")
        assets = []
    if not isinstance(animations, list):
        errors.append("campo animations não é uma lista")
        animations = []
    if data.get("asset_count") != len(assets):
        errors.append("asset_count não corresponde à lista assets")
    if data.get("animation_count") != len(animations):
        errors.append("animation_count não corresponde à lista animations")
    asset_ids = {str(item.get("asset_id")) for item in assets if isinstance(item, dict)}
    animation_ids = [str(item.get("id")) for item in animations if isinstance(item, dict)]
    if len(animation_ids) != len(set(animation_ids)):
        errors.append("ids de animação duplicados")
    for item in animations:
        if not isinstance(item, dict):
            errors.append("animação não é objeto")
            continue
        if not item.get("id") or not item.get("action_name"):
            errors.append("animação sem id ou action_name")
        if str(item.get("asset_id")) not in asset_ids:
            errors.append(f"animação referencia asset ausente: {item.get('id')}")
        if int(item.get("frame_count", 0)) <= 0:
            errors.append(f"animação sem frames: {item.get('id')}")
    if assets_path:
        try:
            source = _load_json(assets_path.expanduser().resolve())
            source_assets = {str(item.get("id")): item for item in source.get("assets", []) if isinstance(item, dict)}
            for item in assets:
                source_asset = source_assets.get(str(item.get("asset_id")))
                if source_asset is None:
                    errors.append(f"asset de animação ausente no catálogo base: {item.get('asset_id')}")
                elif item.get("source_sha256") != source_asset.get("sha256"):
                    errors.append(f"sha256 divergente no asset: {item.get('asset_id')}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"catálogo base inválido: {exc}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Catálogo interno de animações FBX do Sprite Lab")
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="extrai Actions internas dos FBXs")
    index.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    index.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    index.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    index.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    index.add_argument("--source-id", action="append", default=[])
    index.add_argument("--asset-id", action="append", default=[])
    index.add_argument("--all-fbx", action="store_true", help="sonda também FBXs não classificados como animação")
    index.add_argument("--force", action="store_true", help="ignora resultados já sondados")
    index.add_argument("--blender", default=None)
    index.add_argument("--timeout", type=float, default=3600.0)

    validate = sub.add_parser("validate", help="valida o manifesto de animações")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT)
    validate.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "index":
        try:
            report = index_animation_catalog(
                assets_path=args.assets,
                output_path=args.output,
                report_path=args.report,
                cache_path=args.cache,
                source_ids=set(args.source_id) or None,
                asset_ids=set(args.asset_id) or None,
                all_fbx=args.all_fbx,
                force=args.force,
                blender=args.blender,
                timeout=max(30.0, args.timeout),
            )
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError, zipfile.BadZipFile) as exc:
            print(f"ERROR {type(exc).__name__}: {exc}")
            return 2
        print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
        return 0

    errors = validate_animation_catalog(args.manifest, args.assets)
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1
    print(f"ANIMATION_CATALOG_VALID {args.manifest.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
