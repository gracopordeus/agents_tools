"""Resolve browser-friendly model URLs and lazily cache FBX-to-GLB conversions."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

GENERATION_DIR = Path(__file__).resolve().parents[1]
if str(GENERATION_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATION_DIR))

from animation_catalog import _safe_member
from path_config import ASSET_ROOT


CATALOG_DIR = ASSET_ROOT / "catalog"
ASSETS_PATH = CATALOG_DIR / "assets.json"
SOURCE_CACHE_PATH = CATALOG_DIR / "web-source-cache"
MODEL_CACHE_PATH = CATALOG_DIR / "web-model-cache"
CONVERTER = Path(__file__).resolve().with_name("blender_model_convert.py")
MODEL_FORMATS = {"fbx", "glb", "gltf"}
MODEL_CACHE_VERSION = "clean-scene-v2-textures"

try:  # observability is optional when model_cache is used standalone.
    import observability as _obs
except ImportError:  # pragma: no cover - same-dir import always exists in prod.
    _obs = None


def _conversion_timeout() -> float:
    try:
        return max(60.0, float(os.environ.get("SPRITE_LAB_CONVERT_TIMEOUT", "") or 900))
    except (TypeError, ValueError):
        return 900.0


_KEY_LOCKS: dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    """Return a stable per-key lock so conversions of *different* assets run
    in parallel while repeated conversions of the *same* output serialize.

    A former global lock serialized every conversion behind a single mutex:
    one wedged Blender (timeout 900s) starved all ``/model`` requests.
    Per-key locks keep that blast radius to one asset.
    """
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def _log(name: str, **fields) -> None:
    if _obs is not None:
        _obs.log_event(name, **fields)


def _load_assets() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    catalog = json.loads(ASSETS_PATH.read_text(encoding="utf-8"))
    assets = {
        str(asset["id"]): asset
        for asset in catalog.get("assets", [])
        if asset.get("id")
    }
    return catalog, assets


def _catalog_root(catalog: dict[str, Any]) -> Path:
    root = Path(catalog.get("catalog_root", ASSETS_PATH.parent.parent)).expanduser()
    return root if root.is_absolute() else (ASSETS_PATH.parent.parent / root).resolve()


def _archive_key(archive: Path) -> str:
    stat = archive.stat()
    value = f"{archive.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _extract_member_names(handle: zipfile.ZipFile) -> set[str]:
    names: set[str] = set()
    for info in handle.infolist():
        member = _safe_member(info.filename)
        if member is not None and not info.is_dir():
            names.add(member)
    return names


def _gltf_companions(handle: zipfile.ZipFile, member: str) -> set[str]:
    """Return the extra ZIP members a ``.gltf`` needs besides itself.

    A Godot-style export splits the asset into ``.gltf`` + ``.bin`` +
    textures. The ``.bin`` shares the stem; textures are referenced by URI
    inside the glTF JSON. Anything else in the archive stays on disk.
    """
    wanted = {member}
    available = _extract_member_names(handle)
    prefix = member.rsplit(".", 1)[0] + "."
    wanted.update(name for name in available if name.startswith(prefix))
    try:
        document = json.loads(handle.read(member).decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        return wanted
    if not isinstance(document, dict):
        return wanted
    base = member.rsplit("/", 1)[0]
    base = "" if base == member else base + "/"
    candidates: list[str] = []
    for section in ("buffers", "images"):
        items = document.get(section)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("uri"), str):
                candidates.append(item["uri"])
    for uri in candidates:
        if not uri or uri.startswith("data:") or "://" in uri:
            continue
        resolved = _safe_member(base + uri.replace("\\", "/"))
        if resolved is not None and resolved in available:
            wanted.add(resolved)
    return wanted


def _extract_archive(archive: Path, wanted: set[str] | None = None) -> Path:
    """Materialize ``archive`` into the source cache.

    ``wanted=None`` extracts everything (historical behaviour, with a
    ``.complete`` marker). A ``wanted`` subset extracts only those members
    plus a ``.partial-<hash>`` marker, so the first viewer click on a 244MB
    kit materializes kilobytes instead of the whole archive. A later full
    extraction reuses files already on disk and seals ``.complete``.
    """
    destination = SOURCE_CACHE_PATH / _archive_key(archive)
    marker = destination / ".complete"
    if marker.is_file():
        return destination
    subset_marker: Path | None = None
    if wanted is not None:
        digest = hashlib.sha256("\n".join(sorted(wanted)).encode("utf-8")).hexdigest()[:16]
        subset_marker = destination / f".partial-{digest}"
        if subset_marker.is_file():
            return destination

    with _lock_for(f"extract:{_archive_key(archive)}"):
        if marker.is_file():
            return destination
        if subset_marker is not None and subset_marker.is_file():
            return destination
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                member = _safe_member(info.filename)
                if member is None or info.is_dir():
                    continue
                if wanted is not None and member not in wanted:
                    continue
                target = (destination / member).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise ValueError(f"member inseguro: {member}")
                if target.is_file() and target.stat().st_size == info.file_size:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(handle.read(info.filename))
        if wanted is None:
            marker.touch()
        elif subset_marker is not None:
            subset_marker.touch()
    return destination


def _selective_members(asset: dict[str, Any], archive: Path) -> set[str] | None:
    """Return the minimal member subset for ``asset``, or None for full extract.

    - ``.glb`` is self-contained: the member alone suffices;
    - ``.gltf`` needs its ``.bin``/texture companions (parsed from the JSON);
    - ``.fbx`` falls back to full extraction: material textures may live in
      sibling directories the binary format does not declare.
    """
    relative = str(asset.get("relative_path", ""))
    member = _safe_member(relative)
    if member is None:
        return None
    lowered = member.lower()
    if lowered.endswith(".glb"):
        return {member}
    if not lowered.endswith(".gltf"):
        return None
    try:
        with zipfile.ZipFile(archive) as handle:
            return _gltf_companions(handle, member)
    except (OSError, zipfile.BadZipFile):
        return None


def _source_root_and_path(
    asset: dict[str, Any], catalog: dict[str, Any], *, selective: bool = False
) -> tuple[Path, Path]:
    root = _catalog_root(catalog)
    relative = str(asset.get("relative_path", ""))
    archive_value = asset.get("archive")
    if archive_value:
        archive = (root / str(archive_value)).resolve()
        if not archive.is_file():
            raise FileNotFoundError(archive)
        wanted = _selective_members(asset, archive) if selective else None
        source_root = _extract_archive(archive, wanted)
        member = _safe_member(relative)
        if member is None:
            raise ValueError(f"member inseguro: {relative}")
        path = (source_root / member).resolve()
    else:
        source_root = (root / str(asset.get("source_root") or ".")).resolve()
        path = (source_root / relative).resolve()
    if not path.is_relative_to(source_root) or not path.is_file():
        raise FileNotFoundError(path)
    return source_root, path


def source_path(asset_id: str, relative_path: str | None = None) -> Path:
    catalog, assets = _load_assets()
    asset = assets.get(str(asset_id))
    if asset is None:
        raise KeyError(f"asset não encontrado: {asset_id}")
    source_root, original = _source_root_and_path(asset, catalog)
    if relative_path is None:
        return original
    member = _safe_member(relative_path)
    if member is None:
        raise ValueError(f"member inseguro: {relative_path}")
    target = (source_root / member).resolve()
    if not target.is_relative_to(source_root) or not target.is_file():
        raise FileNotFoundError(target)
    return target


def _converted_model_path(asset: dict[str, Any]) -> Path:
    key = f"{MODEL_CACHE_VERSION}:{asset.get('sha256') or asset.get('id')}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return MODEL_CACHE_PATH / f"{digest}.glb"


def _equivalent_asset(
    asset: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    format_name: str,
) -> dict[str, Any] | None:
    """Return an equivalent format variant from the same catalog source."""
    format_name = str(format_name).casefold()
    if str(asset.get("format", "")).casefold() == format_name:
        return asset
    source_id = str(asset.get("source_id") or "")
    name = str(asset.get("name") or "").strip().casefold()
    if not source_id or not name:
        return None
    candidates = [
        candidate
        for candidate in assets.values()
        if str(candidate.get("source_id") or "") == source_id
        and str(candidate.get("format") or "").casefold() == format_name
        and str(candidate.get("name") or "").strip().casefold() == name
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: str(candidate.get("relative_path") or ""))


def canonical_model_source(asset_id: str) -> dict[str, Any]:
    """Describe whether a request uses an existing GLB or needs normalization."""
    _, assets = _load_assets()
    asset = assets.get(str(asset_id))
    if asset is None:
        raise KeyError(f"asset não encontrado: {asset_id}")
    format_name = str(asset.get("format", "")).casefold()
    if format_name not in MODEL_FORMATS:
        raise ValueError(f"formato sem viewer 3D: {format_name or 'desconhecido'}")
    existing_glb = _equivalent_asset(asset, assets, "glb")
    if existing_glb is not None:
        return {
            "asset": asset,
            "source_asset": existing_glb,
            "strategy": "existing_glb",
        }
    existing_gltf = _equivalent_asset(asset, assets, "gltf")
    if existing_gltf is not None:
        return {
            "asset": asset,
            "source_asset": existing_gltf,
            "strategy": "gltf_to_glb_cache",
        }
    return {
        "asset": asset,
        "source_asset": asset,
        "strategy": "blender_cache",
    }


def model_path(asset_id: str) -> Path:
    catalog, _ = _load_assets()
    canonical = canonical_model_source(asset_id)
    asset = canonical["asset"]
    source_asset = canonical["source_asset"]
    if canonical["strategy"] == "existing_glb":
        return _source_root_and_path(source_asset, catalog, selective=True)[1]

    output = _converted_model_path(source_asset)
    if output.is_file():
        _log("model_cache_hit", asset_id=str(asset_id), bytes=output.stat().st_size)
        return output
    lock = _lock_for(f"convert:{output.name}")
    if not lock.acquire(blocking=False):
        # Another thread is already converting this exact asset: wait for it
        # instead of spawning a second Blender for the same output file.
        _log("model_convert_wait", asset_id=str(asset_id))
        with lock:
            if output.is_file():
                return output
            raise RuntimeError("conversão concorrente não gerou o GLB, tente novamente")
    try:
        if output.is_file():
            return output
        source = _source_root_and_path(source_asset, catalog, selective=True)[1]
        MODEL_CACHE_PATH.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp.glb")
        if temporary.exists():
            temporary.unlink()
        timeout = _conversion_timeout()
        _log(
            "model_convert_started",
            asset_id=str(asset_id),
            source=str(source_asset.get("relative_path")),
            timeout_s=timeout,
        )
        started = time.monotonic()
        command = [
            "blender",
            "--background",
            "--factory-startup",
            "--python",
            str(CONVERTER),
            "--",
            "--input",
            str(source),
            "--output",
            str(temporary),
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
            if temporary.exists():
                temporary.unlink()
            _log("model_convert_error", asset_id=str(asset_id), detail=f"timeout após {timeout}s")
            raise RuntimeError(f"conversão excedeu o timeout de {timeout:g}s") from exc
        duration_s = round(time.monotonic() - started, 2)
        if completed.returncode != 0 or not temporary.is_file():
            if temporary.exists():
                temporary.unlink()
            details = (completed.stderr or completed.stdout or "").strip().splitlines()
            message = details[-1] if details else "conversão FBX para GLB falhou"
            _log(
                "model_convert_error",
                asset_id=str(asset_id),
                duration_s=duration_s,
                detail=message[-500:],
            )
            raise RuntimeError(message)
        temporary.replace(output)
        _log(
            "model_convert_done",
            asset_id=str(asset_id),
            duration_s=duration_s,
            bytes=output.stat().st_size,
        )
    finally:
        lock.release()
    return output


def prewarm(asset_ids: list[str]) -> dict[str, int]:
    """Convert every missing GLB for ``asset_ids`` sequentially.

    Used for SaaS warmup (``python3 model_cache.py --prewarm --all``) so the
    first user click never pays the Blender cost inside an HTTP request.
    Returns ``{"converted": n, "cached": n, "failed": n}``.
    """
    summary = {"converted": 0, "cached": 0, "failed": 0}
    for asset_id in asset_ids:
        try:
            canonical = canonical_model_source(asset_id)
        except (KeyError, ValueError) as exc:
            _log("model_convert_error", asset_id=str(asset_id), detail=str(exc)[-200:])
            summary["failed"] += 1
            continue
        if canonical["strategy"] == "existing_glb":
            summary["cached"] += 1
            continue
        output = _converted_model_path(canonical["source_asset"])
        if output.is_file():
            summary["cached"] += 1
            continue
        try:
            model_path(asset_id)
        except (OSError, RuntimeError, ValueError) as exc:
            _log("model_convert_error", asset_id=str(asset_id), detail=str(exc)[-200:])
            summary["failed"] += 1
            continue
        summary["converted"] += 1
    return summary


def _prewarm_main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Pre-aquece o cache GLB do viewer")
    parser.add_argument("--asset-id", action="append", default=[], help="id de asset (repetir p/ vários)")
    parser.add_argument("--all", action="store_true", help="converte todos os assets 3D do catálogo")
    parser.add_argument("--limit", type=int, default=0, help="máximo de assets com --all (0 = sem limite)")
    args = parser.parse_args(argv)
    _, assets = _load_assets()
    if args.all:
        wanted = [
            asset_id
            for asset_id, asset in sorted(assets.items())
            if str(asset.get("format", "")).casefold() in MODEL_FORMATS
        ]
        if args.limit > 0:
            wanted = wanted[: args.limit]
    else:
        wanted = list(args.asset_id)
    if not wanted:
        print("nada a converter: informe --asset-id ou --all")
        return 2
    summary = prewarm(wanted)
    print(f"PREWARM converted={summary['converted']} cached={summary['cached']} failed={summary['failed']}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_prewarm_main(sys.argv[1:]))


def viewer_descriptor(asset_id: str) -> dict[str, Any]:
    catalog, assets = _load_assets()
    asset = assets.get(str(asset_id))
    if asset is None:
        raise KeyError(f"asset não encontrado: {asset_id}")
    format_name = str(asset.get("format", "")).casefold()
    if format_name not in MODEL_FORMATS:
        raise ValueError(f"formato sem viewer 3D: {format_name or 'desconhecido'}")
    relative_url = "/".join(
        quote(part, safe="") for part in str(asset.get("relative_path", "")).split("/")
    )
    canonical = canonical_model_source(asset_id)
    source_asset = canonical["source_asset"]
    return {
        "asset_id": asset_id,
        "name": asset.get("name"),
        "source_format": format_name,
        "source_url": f"/assets/{quote(asset_id, safe='')}/source/{relative_url}",
        "model_url": f"/assets/{asset_id}/model",
        "viewer_format": "glb",
        "model_strategy": canonical["strategy"],
        "canonical_asset_id": source_asset.get("id"),
        "animations": asset.get("kind") == "animation",
    }
