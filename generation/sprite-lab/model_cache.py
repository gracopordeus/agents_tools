"""Resolve browser-friendly model URLs and lazily cache FBX-to-GLB conversions."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import threading
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
CACHE_LOCK = threading.Lock()


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


def _extract_archive(archive: Path) -> Path:
    destination = SOURCE_CACHE_PATH / _archive_key(archive)
    marker = destination / ".complete"
    if marker.is_file():
        return destination

    with CACHE_LOCK:
        if marker.is_file():
            return destination
        temporary = SOURCE_CACHE_PATH / f".{destination.name}.tmp"
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                member = _safe_member(info.filename)
                if member is None or info.is_dir():
                    continue
                target = (temporary / member).resolve()
                if not target.is_relative_to(temporary.resolve()):
                    raise ValueError(f"member inseguro: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(handle.read(info))
        (temporary / ".complete").touch()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary.rename(destination)
        else:
            shutil.rmtree(temporary)
    return destination


def _source_root_and_path(
    asset: dict[str, Any], catalog: dict[str, Any]
) -> tuple[Path, Path]:
    root = _catalog_root(catalog)
    relative = str(asset.get("relative_path", ""))
    archive_value = asset.get("archive")
    if archive_value:
        archive = (root / str(archive_value)).resolve()
        if not archive.is_file():
            raise FileNotFoundError(archive)
        source_root = _extract_archive(archive)
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
        return _source_root_and_path(source_asset, catalog)[1]

    output = _converted_model_path(source_asset)
    if output.is_file():
        return output
    with CACHE_LOCK:
        if output.is_file():
            return output
        source = _source_root_and_path(source_asset, catalog)[1]
        MODEL_CACHE_PATH.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp.glb")
        if temporary.exists():
            temporary.unlink()
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
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if completed.returncode != 0 or not temporary.is_file():
            if temporary.exists():
                temporary.unlink()
            details = (completed.stderr or completed.stdout or "").strip().splitlines()
            raise RuntimeError(details[-1] if details else "conversão FBX para GLB falhou")
        temporary.replace(output)
    return output


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
