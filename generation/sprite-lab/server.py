#!/usr/bin/env python3
"""Local semantic catalog UI for the canonical Sprite Lab generation tools."""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import io
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# Keep direct `python3 server.py` launches compatible with the project-local
# environment used for optional provider SDKs such as `openai`.
LOCAL_VENV_SITE = (
    Path(__file__).resolve().parent
    / ".venv"
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)
if LOCAL_VENV_SITE.is_dir() and str(LOCAL_VENV_SITE) not in sys.path:
    sys.path.insert(0, str(LOCAL_VENV_SITE))

import relationship_catalog as rel
import ai_render_spec
import composition_export
import gemini_sprite_postprocess
import huggingface_realesrgan
import image_generation_provider
import model_cache
import sprite_render
import render_profile
import asset_manifest
from build_render_channel_sheets import build_channel
from direction_contract import (
    DIRECTION_LABELS,
    DIRECTION_ROWS,
    DIRECTION_VECTORS,
    direction_contract_for,
)


BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
STATE = BASE / "state"
BUG_REPORTS_PATH = STATE / "bug_reports.json"
SPRITE_JOBS_PATH = STATE / "sprite_jobs.json"
ACTION_ANNOTATIONS_PATH = rel.DEFAULT_ANNOTATIONS.with_name("action_annotations.json")
SPRITE_WORK = BASE / "work" / "sprite-renders"
GEMINI_WORK = BASE / "work" / "gemini-renders"
POSTPROCESS_WORK = BASE / "work" / "gemini-postprocess"
GEMINI_JOBS_PATH = STATE / "gemini_jobs.json"
POSTPROCESS_JOBS_PATH = STATE / "postprocess_jobs.json"
GEMINI_CONFIG_PATH = STATE / "gemini_config.json"
OPENAI_CONFIG_PATH = STATE / "openai_config.json"
QWEN_CONFIG_PATH = STATE / "qwen_config.json"
GEMINI_REFERENCES_PATH = STATE / "gemini_references.json"
GEMINI_REFERENCES_WORK = BASE / "work" / "gemini-references"
GEMINI_PROMPT_PATH = STATE / "gemini_prompt.txt"
AI_RENDER_JOB_PREFIX = "ai_render_"
GEMINI_CHANNEL_FILES = {
    "beauty": "spritesheet_beauty.png",
    "bones": "spritesheet_bones.png",
    "lineart": "spritesheet_lineart.png",
}
AI_RENDER_REFERENCE_CHANNELS = (*GEMINI_CHANNEL_FILES, "frame_control")
DEFAULT_GEMINI_CHANNELS = tuple(GEMINI_CHANNEL_FILES)
JOB_LOCK = threading.Lock()
BUG_TYPES = {
    "render_failure": "Falha em renderizar",
    "cannot_identify": "Não consigo identificar",
    "other": "Outros",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def elapsed_seconds(started_at: str | None, finished_at: str) -> float | None:
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return None
    return round(max(0.0, (finished - started).total_seconds()), 3)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_action_annotations() -> dict[str, dict]:
    if not ACTION_ANNOTATIONS_PATH.is_file():
        return {}
    try:
        data = json.loads(ACTION_ANNOTATIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = data.get("annotations", []) if isinstance(data, dict) else []
    return {
        str(row.get("action_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("action_id")
    }


def action_annotation_defaults(action: dict) -> dict:
    return {
        "action_id": action.get("id"),
        "semantic_name": action.get("clip_name") or action.get("action_name") or action.get("id", ""),
        "family": [],
        "tags": list(action.get("semantic_tags", [])),
        "notes": "",
        "review_status": "unreviewed",
        "updated_at": None,
    }


def update_action_annotation(action_id: str, patch: dict) -> dict:
    manifest = ensure_relationship_catalog()
    action = next(
        (item for item in manifest.get("animations", []) if str(item.get("id")) == action_id),
        None,
    )
    if action is None:
        raise KeyError(f"action não encontrada: {action_id}")
    with JOB_LOCK:
        rows = read_action_annotations()
        row = {
            **action_annotation_defaults(action),
            **rows.get(action_id, {}),
            **patch,
        }
        row["action_id"] = action_id
        row["updated_at"] = utc_now()
        rows[action_id] = row
        write_json_atomic(
            ACTION_ANNOTATIONS_PATH,
            {
                "schema": "sprite_lab.action_annotations/v1",
                "generated_at": utc_now(),
                "annotations": sorted(rows.values(), key=lambda item: str(item.get("action_id"))),
            },
        )
    return row


def read_sprite_jobs() -> list[dict]:
    if not SPRITE_JOBS_PATH.is_file():
        return []
    try:
        data = json.loads(SPRITE_JOBS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _read_jobs(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def read_gemini_jobs() -> list[dict]:
    return _read_jobs(GEMINI_JOBS_PATH)


def read_postprocess_jobs() -> list[dict]:
    return _read_jobs(POSTPROCESS_JOBS_PATH)


def read_gemini_config() -> dict:
    if not GEMINI_CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(GEMINI_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def gemini_api_key() -> str:
    """Prefer the local UI setting, then the conventional environment keys."""
    saved = str(read_gemini_config().get("api_key", "")).strip()
    return saved or os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()


def gemini_config_status() -> dict:
    saved = read_gemini_config()
    if str(saved.get("api_key", "")).strip():
        return {
            "configured": True,
            "source": "local",
            "updated_at": saved.get("updated_at"),
        }
    if os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip():
        return {"configured": True, "source": "environment", "updated_at": None}
    return {"configured": False, "source": None, "updated_at": None}


def read_openai_config() -> dict:
    if not OPENAI_CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(OPENAI_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def openai_api_key() -> str:
    """Prefer the local UI setting, then the conventional environment key."""
    saved = str(read_openai_config().get("api_key", "")).strip()
    return saved or os.environ.get("OPENAI_API_KEY", "").strip()


def openai_config_status() -> dict:
    saved = read_openai_config()
    if str(saved.get("api_key", "")).strip():
        return {
            "configured": True,
            "source": "local",
            "updated_at": saved.get("updated_at"),
        }
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return {"configured": True, "source": "environment", "updated_at": None}
    return {"configured": False, "source": None, "updated_at": None}


def read_qwen_config() -> dict:
    if not QWEN_CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(QWEN_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def qwen_api_key() -> str:
    """Prefer the local Qwen Cloud setting, then DashScope environment keys."""
    saved = str(read_qwen_config().get("api_key", "")).strip()
    value = saved or os.environ.get("DASHSCOPE_API_KEY", "").strip() or os.environ.get("QWEN_API_KEY", "").strip()
    return "".join(value.split())


def qwen_config_status() -> dict:
    saved = read_qwen_config()
    if "".join(str(saved.get("api_key", "")).split()):
        return {
            "configured": True,
            "source": "local",
            "updated_at": saved.get("updated_at"),
        }
    if "".join(os.environ.get("DASHSCOPE_API_KEY", "").split()) or "".join(os.environ.get("QWEN_API_KEY", "").split()):
        return {"configured": True, "source": "environment", "updated_at": None}
    return {"configured": False, "source": None, "updated_at": None}


def read_gemini_prompt() -> str | None:
    if not GEMINI_PROMPT_PATH.is_file():
        return None
    try:
        value = GEMINI_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def save_gemini_prompt(value: str) -> str:
    prompt = str(value or "").strip()
    if not prompt or len(prompt) > 12000:
        raise ValueError("o prompt deve ter entre 1 e 12000 caracteres")
    GEMINI_PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = GEMINI_PROMPT_PATH.with_name(f".{GEMINI_PROMPT_PATH.name}.tmp")
    try:
        temporary.write_text(prompt + "\n", encoding="utf-8")
        temporary.replace(GEMINI_PROMPT_PATH)
    finally:
        if temporary.exists():
            temporary.unlink()
    return prompt


def save_gemini_api_key(value: str) -> dict:
    api_key = str(value or "").strip()
    if len(api_key) > 500:
        raise ValueError("a chave Gemini é muito longa")
    if api_key:
        payload = {
            "schema": "sprite_lab.gemini_config/v1",
            "api_key": api_key,
            "updated_at": utc_now(),
        }
        GEMINI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = GEMINI_CONFIG_PATH.with_name(f".{GEMINI_CONFIG_PATH.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(GEMINI_CONFIG_PATH)
            GEMINI_CONFIG_PATH.chmod(0o600)
        finally:
            if temporary.exists():
                temporary.unlink()
    elif GEMINI_CONFIG_PATH.exists():
        GEMINI_CONFIG_PATH.unlink()
    return gemini_config_status()


def save_openai_api_key(value: str) -> dict:
    api_key = str(value or "").strip()
    if len(api_key) > 500:
        raise ValueError("a chave OpenAI é muito longa")
    if api_key:
        payload = {
            "schema": "sprite_lab.openai_config/v1",
            "api_key": api_key,
            "updated_at": utc_now(),
        }
        OPENAI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = OPENAI_CONFIG_PATH.with_name(f".{OPENAI_CONFIG_PATH.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(OPENAI_CONFIG_PATH)
            OPENAI_CONFIG_PATH.chmod(0o600)
        finally:
            if temporary.exists():
                temporary.unlink()
    elif OPENAI_CONFIG_PATH.exists():
        OPENAI_CONFIG_PATH.unlink()
    return openai_config_status()


def save_qwen_api_key(value: str) -> dict:
    # Tokens copied from dashboards can contain visual line breaks/spaces.
    api_key = "".join(str(value or "").split())
    if len(api_key) > 500:
        raise ValueError("a chave Qwen Cloud é muito longa")
    if api_key:
        payload = {
            "schema": "sprite_lab.qwen_config/v1",
            "api_key": api_key,
            "updated_at": utc_now(),
        }
        QWEN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = QWEN_CONFIG_PATH.with_name(f".{QWEN_CONFIG_PATH.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(QWEN_CONFIG_PATH)
            QWEN_CONFIG_PATH.chmod(0o600)
        finally:
            if temporary.exists():
                temporary.unlink()
    elif QWEN_CONFIG_PATH.exists():
        QWEN_CONFIG_PATH.unlink()
    return qwen_config_status()


def read_gemini_references() -> list[dict]:
    if not GEMINI_REFERENCES_PATH.is_file():
        return []
    try:
        data = json.loads(GEMINI_REFERENCES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def list_gemini_references() -> list[dict]:
    """Return cached references with their most recent Gemini usage first."""
    last_used: dict[str, str] = {}
    for job in read_gemini_jobs():
        reference_id = str(job.get("payload", {}).get("reference_id") or "").strip()
        if reference_id:
            timestamp = str(job.get("created_at") or "")
            if timestamp > last_used.get(reference_id, ""):
                last_used[reference_id] = timestamp
    references = []
    for reference in read_gemini_references():
        item = {**reference, "last_used_at": last_used.get(str(reference.get("id")))}
        references.append(item)
    return sorted(
        references,
        key=lambda item: (item.get("last_used_at") or item.get("created_at") or ""),
        reverse=True,
    )


def save_gemini_reference(data_url: str, name: str) -> dict:
    reference_id = f"reference_{uuid.uuid4().hex[:16]}"
    destination = GEMINI_REFERENCES_WORK / f"{reference_id}.png"
    report = _decode_reference(data_url, destination)
    reference = {
        "id": reference_id,
        "name": str(name or "referência").strip()[:240] or "referência",
        "file": destination.name,
        "size": report["size"],
        "bytes": report["bytes"],
        "created_at": utc_now(),
    }
    references = read_gemini_references()
    references.append(reference)
    write_json_atomic(GEMINI_REFERENCES_PATH, references)
    return reference


def get_gemini_reference(reference_id: str) -> dict | None:
    return next((item for item in read_gemini_references() if item.get("id") == reference_id), None)


def gemini_reference_path(reference_id: str) -> Path:
    reference = get_gemini_reference(reference_id)
    if reference is None:
        raise ValueError("referência Gemini não encontrada")
    path = _safe_work_child(GEMINI_REFERENCES_WORK, str(reference.get("file", "")))
    if not path.is_file():
        raise ValueError("arquivo da referência Gemini não encontrado")
    return path


def _update_job_file(path: Path, job_id: str, patch: dict) -> dict | None:
    with JOB_LOCK:
        jobs = _read_jobs(path)
        for job in jobs:
            if job.get("id") == job_id:
                job.update(patch)
                write_json_atomic(path, jobs)
                return job
    return None


def update_gemini_job(job_id: str, patch: dict) -> dict | None:
    return _update_job_file(GEMINI_JOBS_PATH, job_id, patch)


def update_postprocess_job(job_id: str, patch: dict) -> dict | None:
    return _update_job_file(POSTPROCESS_JOBS_PATH, job_id, patch)


def get_gemini_job(job_id: str) -> dict | None:
    return next((job for job in read_gemini_jobs() if job.get("id") == job_id), None)


def get_postprocess_job(job_id: str) -> dict | None:
    return next((job for job in read_postprocess_jobs() if job.get("id") == job_id), None)


def pipeline_python_executable() -> str:
    """Use the dependency-complete CPU environment for worker subprocesses."""
    configured = os.environ.get("SPRITE_LAB_PYTHON", "").strip()
    if configured:
        return configured
    local = BASE / "work" / "teed-venv" / "bin" / "python"
    return str(local) if local.is_file() else sys.executable


def update_pipeline_progress(
    path: Path,
    job_id: str,
    *,
    stage: str,
    percent: int,
    eta_seconds: int | None = None,
) -> None:
    update = {
        "progress": {
            "stage": stage,
            "percent": max(0, min(100, int(percent))),
            "eta_seconds": eta_seconds,
        }
    }
    update_job = _update_job_file(path, job_id, update)
    if update_job is None:
        raise KeyError(f"job não encontrado: {job_id}")


def _safe_work_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("caminho de job inválido")
    return candidate


def normalize_gemini_channels(value) -> list[str]:
    """Validate the ordered references selected for an AI render."""
    if value is None:
        return list(DEFAULT_GEMINI_CHANNELS)
    if not isinstance(value, list):
        raise ValueError("reference_channels deve ser uma lista")
    channels: list[str] = []
    for item in value:
        channel = str(item or "").strip().casefold()
        if channel not in AI_RENDER_REFERENCE_CHANNELS:
            raise ValueError(f"referência estrutural inválida: {item!r}")
        if channel not in channels:
            channels.append(channel)
    if not channels:
        raise ValueError("selecione ao menos uma referência estrutural")
    return channels


def list_gemini_sources() -> list[dict]:
    sources: list[dict] = []
    if not SPRITE_WORK.is_dir():
        return sources
    sprite_created_at = {
        str(job.get("id")): str(job.get("created_at") or "")
        for job in read_sprite_jobs()
        if job.get("id")
    }
    required = GEMINI_CHANNEL_FILES
    for directory in sorted(SPRITE_WORK.iterdir(), key=lambda item: item.name):
        if not directory.is_dir():
            continue
        if not all((directory / filename).is_file() for filename in required.values()):
            continue
        metadata = {}
        metadata_path = directory / "render_metadata.json"
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                metadata = {}
        cell = metadata.get("cell") or metadata.get("cell_size") or [256, 256]
        rows = len(metadata.get("directions", [])) or 8
        phases = int(metadata.get("phases", 8) or 8)
        files = {name: f"{directory.name}/{filename}" for name, filename in required.items()}
        files["root"] = directory.name
        files["metadata"] = (
            f"{directory.name}/render_metadata.json"
            if metadata_path.is_file()
            else None
        )
        sources_result = {
            "id": directory.name,
            "label": f"{directory.name} · {cell[0]}px · {rows}×{phases}",
            "root": directory.name,
            "cell_size": cell,
            "rows": rows,
            "phases": phases,
            "files": files,
            "created_at": sprite_created_at.get(directory.name),
        }
        inherited = ai_render_source_contract(directory)
        component_labels = [
            "{} → {}".format(
                item.get("name") or item.get("role") or "component",
                item.get("hand") or item.get("attach_to") or "attachment",
            )
            for item in inherited.get("components", [])
            if isinstance(item, dict)
        ]
        action = inherited.get("action") if isinstance(inherited.get("action"), dict) else {}
        sources_result["inherited"] = {
            "action": action.get("clip_name") or action.get("name") or None,
            "components": component_labels,
            "foot_anchor": inherited.get("framing", {}).get("foot_anchor"),
        }
        sources.append(sources_result)
    return sorted(
        sources,
        key=lambda item: item.get("created_at") or "",
        reverse=True,
    )


def _decode_reference(data_url: str, destination: Path) -> dict:
    if not data_url.startswith("data:image/") or "," not in data_url:
        raise ValueError("referência deve ser uma imagem em data URL")
    header, encoded = data_url.split(",", 1)
    mime = header.split(";", 1)[0].casefold()
    if mime not in {"data:image/png", "data:image/jpeg", "data:image/webp"}:
        raise ValueError("formato de referência não suportado")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("referência de imagem inválida") from exc
    if not raw or len(raw) > 25 * 1024 * 1024:
        raise ValueError("referência deve ter entre 1 byte e 25 MB")
    from PIL import Image

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.convert("RGBA").save(destination, format="PNG")
            size = list(image.size)
    except (OSError, ValueError) as exc:
        raise ValueError("não foi possível ler a referência de imagem") from exc
    return {"mime": mime.removeprefix("data:"), "bytes": len(raw), "size": size}


def update_sprite_job(job_id: str, patch: dict) -> dict | None:
    with JOB_LOCK:
        jobs = read_sprite_jobs()
        for job in jobs:
            if job.get("id") == job_id:
                job.update(patch)
                write_json_atomic(SPRITE_JOBS_PATH, jobs)
                return job
    return None


def get_sprite_job(job_id: str) -> dict | None:
    return next((job for job in read_sprite_jobs() if job.get("id") == job_id), None)


def build_sprite_download(job_id: str) -> tuple[bytes, str]:
    """Package every deliverable from a completed sprite job in one archive."""
    job = get_sprite_job(job_id)
    if job is None:
        raise KeyError("renderização de sprites não encontrada")
    if job.get("status") != "done":
        raise ValueError("a renderização ainda não terminou")
    output = (SPRITE_WORK / job_id).resolve()
    if not output.is_relative_to(SPRITE_WORK.resolve()) or not output.is_dir():
        raise ValueError("diretório de renderização inválido")

    files: list[tuple[Path, str]] = []
    files.extend((path, f"gifs/{path.name}") for path in sorted(output.glob("*.gif")))
    files.extend((path, f"frames/{path.name}") for path in sorted(output.glob("row*_col*.png")))
    files.extend((path, f"ai-base/{path.name}") for path in sorted(output.glob("ai_base_*.png")))
    files.extend(
        (path, f"structural/{path.name}")
        for name in ("beauty", "bones", "lineart")
        for path in [output / f"spritesheet_{name}.png"]
        if path.is_file()
    )
    for name in (
        "spritesheet.png",
        "asset_manifest.json",
        "render.json",
        "render_metadata.json",
    ):
        path = output / name
        if path.is_file():
            files.append((path, name if name == "spritesheet.png" else f"metadata/{name}"))
    if not files:
        raise FileNotFoundError("nenhum artefato encontrado para download")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, archive_name in files:
            archive.write(path, archive_name)
    return buffer.getvalue(), f"sprites_{job_id}.zip"


def read_bug_reports() -> list[dict]:
    if not BUG_REPORTS_PATH.is_file():
        return []
    try:
        data = json.loads(BUG_REPORTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def create_bug_report(body: dict) -> dict:
    bug_type = str(body.get("bug_type", ""))
    if bug_type not in BUG_TYPES:
        raise ValueError("tipo de bug inválido")
    description = str(body.get("description", "")).strip()
    if bug_type == "other" and not description:
        raise ValueError("descreva o bug quando o tipo for Outros")
    summary = f"Outro: {description}" if bug_type == "other" else BUG_TYPES[bug_type]
    report = {
        "id": f"bug_{uuid.uuid4().hex[:12]}",
        "created_at": utc_now(),
        "asset_id": str(body.get("asset_id") or "") or None,
        "asset_name": str(body.get("asset_name") or "") or None,
        "action_id": str(body.get("action_id") or "") or None,
        "bug_type": bug_type,
        "description": description or None,
        "summary": summary,
    }
    with JOB_LOCK:
        reports = read_bug_reports()
        reports.append(report)
        write_json_atomic(BUG_REPORTS_PATH, reports)
    return report


def ensure_relationship_catalog() -> dict:
    path = rel.DEFAULT_OUTPUT
    try:
        current = rel.load_relationship_state(path)
        if current.get("schema") == rel.RELATIONSHIP_SCHEMA:
            return current
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return rel.build_relationship_catalog()


def build_gemini_structural_sheets(output: Path, report: dict) -> None:
    """Assemble aligned channels without changing their physical row order."""
    worker = report.get("worker") or {}
    cell = worker.get("cell") or [report.get("resolution", 256)] * 2
    size = int(cell[0])
    worker_directions = [str(row) for row in (worker.get("directions") or [])]
    rows = len(worker_directions) or int(report.get("rows", 8))
    if worker_directions:
        try:
            direction_contract_for(worker_directions)
        except ValueError as error:
            raise RuntimeError("render estrutural sem ordem canônica de direções") from error
        actual_rows = (worker.get("direction_contract") or {}).get("rows")
        if isinstance(actual_rows, list):
            actual_ids = [
                str(item.get("row_id") or item.get("row"))
                for item in actual_rows
                if isinstance(item, dict)
            ]
            if actual_ids != worker_directions:
                raise RuntimeError("metadata do Blender remapeou a ordem das rows")
    columns = int(report.get("phases", 8))
    for channel in ("beauty", "bones", "lineart"):
        build_channel(output, channel, rows, columns, size)


def run_asset_job(job: dict, render_mod: Any = None) -> None:
    started = utc_now()
    update_sprite_job(job["id"], {"status": "running", "started_at": started})
    try:
        import tile_render as tile_render_mod

        mod = render_mod or tile_render_mod
        report = mod.generate_asset_render(
            job["payload"],
            job["id"],
            output_root=SPRITE_WORK / job["id"],
        )
        outputs = {
            "render": f"{job['id']}/render.json",
            "worker_report": f"{job['id']}/render_metadata.json",
        }
        update_sprite_job(
            job["id"],
            {
                "status": "done",
                "finished_at": utc_now(),
                "report": report,
                "outputs": outputs,
            },
        )
    except Exception as exc:  # noqa: BLE001
        update_sprite_job(
            job["id"],
            {"status": "error", "finished_at": utc_now(), "error": str(exc)},
        )


def run_sprite_job(job: dict) -> None:
    started = utc_now()
    update_sprite_job(job["id"], {"status": "running", "started_at": started})
    try:
        report = sprite_render.generate_sprite_render(
            job["payload"],
            job["id"],
            output_root=SPRITE_WORK / job["id"],
        )
        build_gemini_structural_sheets(SPRITE_WORK / job["id"], report)
        manifest_path = SPRITE_WORK / job["id"] / "asset_manifest.json"
        if manifest_path.is_file():
            asset_manifest.update_manifest_artifacts(
                manifest_path,
                SPRITE_WORK / job["id"],
                [
                    ("structural_beauty", "spritesheet_beauty.png"),
                    ("structural_bones", "spritesheet_bones.png"),
                    ("structural_lineart", "spritesheet_lineart.png"),
                ],
            )
        direction_gifs = {
            direction: f"{job['id']}/animation_{direction}.gif"
            for direction in (report.get("gifs") or {})
        }
        upscaled_gif = report.get("upscaled_gif")
        outputs = {
            "spritesheet": f"{job['id']}/spritesheet.png",
            "gif": f"{job['id']}/animation.gif" if report.get("gif") else None,
            "gifs": direction_gifs,
            "ai_base_pages": {
                direction: f"{job['id']}/{Path(path).name}"
                for direction, path in (report.get("ai_base_pages") or {}).items()
            },
            "ai_base_contract": report.get("ai_base_contract"),
            "render_mode": report.get("render_mode", "runtime"),
            "upscaled_gif": f"{job['id']}/animation_diagonal_upscaled.gif" if upscaled_gif else None,
            "metadata": f"{job['id']}/render.json",
            "asset_manifest": f"{job['id']}/asset_manifest.json",
        }
        update_sprite_job(
            job["id"],
            {
                "status": "done",
                "finished_at": utc_now(),
                "report": report,
                "outputs": outputs,
            },
        )
    except Exception as exc:  # noqa: BLE001 - job errors are returned to the UI.
        update_sprite_job(
            job["id"],
            {"status": "error", "finished_at": utc_now(), "error": str(exc)},
        )


def _gemini_source_directory(source_id: str) -> Path:
    source = _safe_work_child(SPRITE_WORK, source_id)
    available = {item["id"] for item in list_gemini_sources()}
    if source_id not in available or not source.is_dir():
        raise ValueError("fonte estrutural Gemini inválida ou incompleta")
    return source


def _read_json_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


@lru_cache(maxsize=1)
def _asset_name_index() -> dict[str, str]:
    catalog = _read_json_object(rel.DEFAULT_ASSETS)
    assets = catalog.get("assets") if isinstance(catalog.get("assets"), list) else []
    return {
        str(item.get("id")): str(item.get("name") or item.get("id"))
        for item in assets
        if isinstance(item, dict) and item.get("id")
    }


def ai_render_source_contract(source: Path) -> dict:
    """Extract provider-facing facts from the authoritative structural render."""
    request = _read_json_object(source / "request.json")
    metadata = _read_json_object(source / "render_metadata.json")
    asset_names = _asset_name_index()
    raw_components = request.get("components")
    if not isinstance(raw_components, list):
        raw_components = metadata.get("components")
    components = []
    for item in raw_components if isinstance(raw_components, list) else []:
        if not isinstance(item, dict) or item.get("visible") is False:
            continue
        asset_id = str(item.get("asset_id") or "").strip()
        attach_to = str(item.get("attach_to") or "").strip()
        hand = "right" if attach_to.endswith("_r") else "left" if attach_to.endswith("_l") else ""
        components.append(
            {
                "id": str(item.get("id") or "component"),
                "role": str(item.get("role") or "component"),
                "asset_id": asset_id or None,
                "name": asset_names.get(asset_id) or str(item.get("role") or "component"),
                "attach_to": attach_to or None,
                "hand": hand or None,
                "attach_to_secondary": item.get("attach_to_secondary"),
                "fit": copy.deepcopy(item.get("fit")) if isinstance(item.get("fit"), dict) else None,
            }
        )
    direction_contract = request.get("direction_contract")
    if not isinstance(direction_contract, dict):
        direction_contract = metadata.get("direction_contract")
    direction_rows = []
    canonical_labels = {
        label.casefold()
        for label in DIRECTION_LABELS.values()
    }
    for item in direction_contract.get("rows", []) if isinstance(direction_contract, dict) else []:
        if not isinstance(item, dict):
            continue
        row_value = item.get("row")
        try:
            row_number = int(row_value)
        except (TypeError, ValueError):
            row_match = re.search(r"([1-8])", str(row_value or item.get("row_id") or ""))
            row_number = int(row_match.group(1)) if row_match else len(direction_rows) + 1
        source_direction = str(item.get("id") or item.get("label") or "").strip()
        canonical_row_id = f"r{row_number}"
        # Older Blender exports used the camera target as ``label`` and did
        # not persist the semantic compass vector.  The target is a physical
        # camera coordinate; it is not the direction the character faces.
        # Resolve known compass labels by their physical row so R1 remains
        # NORTH and R5 remains SOUTH without changing the camera loop.
        is_known_compass_label = source_direction.casefold() in canonical_labels
        direction_id = (
            DIRECTION_LABELS[canonical_row_id]
            if is_known_compass_label and canonical_row_id in DIRECTION_LABELS
            else source_direction
        )
        semantic_vector = item.get("vector")
        if is_known_compass_label and canonical_row_id in DIRECTION_VECTORS:
            semantic_vector = list(DIRECTION_VECTORS[canonical_row_id])
        elif semantic_vector is None:
            semantic_vector = item.get("target")
        target = item.get("target")
        if target is None and isinstance(item.get("vector"), list):
            target = item.get("vector")
        direction_rows.append(
            {
                "row": row_number,
                "id": direction_id,
                "vector": copy.deepcopy(semantic_vector),
                "target": copy.deepcopy(target),
                "source_label": source_direction or None,
            }
        )
    animation = request.get("animation_metadata")
    if not isinstance(animation, dict):
        animation = metadata.get("animation_source")
    if not isinstance(animation, dict):
        animation = {}
    camera = metadata.get("camera") if isinstance(metadata.get("camera"), dict) else {}
    profile = metadata.get("render_profile") if isinstance(metadata.get("render_profile"), dict) else {}
    asset_spec = request.get("asset_spec")
    if not isinstance(asset_spec, dict):
        asset_spec = metadata.get("asset") if isinstance(metadata.get("asset"), dict) else {}
    animation_timing = metadata.get("animation_timing")
    if not isinstance(animation_timing, dict):
        animation_timing = {}
    orientation = metadata.get("orientation")
    if not isinstance(orientation, dict):
        orientation = request.get("orientation") if isinstance(request.get("orientation"), dict) else {}
    return {
        "schema": "sprite_lab.ai_render_source_contract/v1",
        "source_id": source.name,
        "action": {
            "name": str(request.get("action_name") or animation.get("action_name") or ""),
            "clip_name": str(animation.get("clip_name") or ""),
            "category": str(animation.get("category") or ""),
        },
        "components": components,
        "asset": copy.deepcopy(asset_spec),
        "animation_timing": copy.deepcopy(animation_timing),
        "orientation": copy.deepcopy(orientation),
        "directions": direction_rows,
        "camera": {
            "type": str(camera.get("type") or request.get("camera_preset") or "orthographic"),
            "preset": str(camera.get("preset") or request.get("camera_preset") or "isometric"),
            "elevation": camera.get("elevation", request.get("elevation")),
            "azimuth": camera.get("azimuth", request.get("azimuth")),
        },
        "framing": {
            "cell_size": copy.deepcopy(profile.get("cell_size") or metadata.get("cell") or [256, 256]),
            "foot_anchor": copy.deepcopy(profile.get("foot_anchor") or [128, 220]),
            "horizontal_margin_px": profile.get("horizontal_margin_px"),
            "vertical_margin_px": profile.get("vertical_margin_px"),
        },
    }


def with_ai_render_source_contract(render_spec: object, source: Path) -> dict:
    value = copy.deepcopy(render_spec) if isinstance(render_spec, dict) else {}
    value["source_contract"] = ai_render_source_contract(source)
    return value


def build_ai_render_validation_overlay(
    source_path: Path,
    output_path: Path,
    *,
    cell_size: int = 256,
    grid_count: int = 8,
) -> dict:
    """Create the persisted, presentation-only validation copy.

    The provider output remains the canonical image. This artifact is only
    exposed to the AI Render preview/history and must not enter downstream
    processing such as GIF creation or post-processing.
    """
    from PIL import Image, ImageDraw

    with Image.open(source_path) as opened:
        source_size = opened.size
        image = opened.convert("RGBA")
    if image.size != (cell_size * grid_count, cell_size * grid_count):
        image.close()
        raise ValueError(
            f"a validação exige {cell_size * grid_count}×{cell_size * grid_count}px; "
            f"recebido {source_size}"
        )

    pixels = image.load()

    def foreground(x: int, y: int) -> bool:
        red, green, blue, alpha = pixels[x, y]
        if alpha < 24:
            return False
        return not (green > 200 and red < 60 and blue < 60)

    tolerance = 4
    violations = []
    for row in range(grid_count):
        for column in range(grid_count):
            left = column * cell_size
            right = left + cell_size - 1
            top = row * cell_size
            bottom = top + cell_size - 1
            edges = []
            if any(foreground(x, y) for x in range(left, min(right, left + tolerance - 1) + 1) for y in range(top, bottom + 1)):
                edges.append("left")
            if any(foreground(x, y) for x in range(max(left, right - tolerance + 1), right + 1) for y in range(top, bottom + 1)):
                edges.append("right")
            if any(foreground(x, y) for x in range(left, right + 1) for y in range(top, min(bottom, top + tolerance - 1) + 1)):
                edges.append("top")
            if any(foreground(x, y) for x in range(left, right + 1) for y in range(max(top, bottom - tolerance + 1), bottom + 1)):
                edges.append("bottom")
            if edges:
                violations.append({"row": row + 1, "column": column + 1, "edges": edges})

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    for violation in violations:
        left = (violation["column"] - 1) * cell_size
        top = (violation["row"] - 1) * cell_size
        draw.rectangle(
            (left, top, left + cell_size - 1, top + cell_size - 1),
            fill=(255, 112, 124, 40),
        )
    grid_color = (220, 226, 234, 210)
    for index in range(grid_count + 1):
        position = index * cell_size
        draw.line((position, 0, position, cell_size * grid_count), fill=grid_color, width=2)
        draw.line((0, position, cell_size * grid_count, position), fill=grid_color, width=2)
    composited = Image.alpha_composite(image, overlay)
    image.close()
    overlay.close()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    composited.save(output_path, format="PNG")
    composited.close()
    return {
        "grid": {"rows": grid_count, "columns": grid_count, "cell_size": cell_size},
        "violating_cells": violations,
        "violation_count": len(violations),
        "overlay": str(output_path),
    }


def build_ai_render_frame_control(output_path: Path) -> None:
    """Create the transparent 8x8 black boundary guide sent to providers."""
    from PIL import Image, ImageDraw

    size = 2048
    cell_size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image, "RGBA")
    for index in range(9):
        position = index * cell_size
        draw.line((position, 0, position, size - 1), fill=(0, 0, 0, 255), width=2)
        draw.line((0, position, size - 1, position), fill=(0, 0, 0, 255), width=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    image.close()


def run_gemini_job(job: dict) -> None:
    started_at = utc_now()
    update_gemini_job(
        job["id"],
        {
            "status": "running",
            "started_at": started_at,
            "progress": {"stage": "preparing_inputs", "percent": 5, "eta_seconds": 300},
        },
    )
    try:
        source = _gemini_source_directory(str(job["payload"]["source_id"]))
        provider_name = image_generation_provider.normalize_provider(
            str(job["payload"].get("provider", "openai"))
        )
        output = GEMINI_WORK / job["id"] / "gemini_output.png"
        reference_channels = normalize_gemini_channels(
            job["payload"].get(
                "reference_channels", job["payload"].get("blender_channels")
            )
        )
        input_paths = [GEMINI_WORK / job["id"] / "reference.png"]
        reference_id = str(job["payload"].get("reference_id", "")).strip()
        if reference_id:
            shutil.copy2(gemini_reference_path(reference_id), input_paths[0])
        frame_control_path = None
        if "frame_control" in reference_channels:
            frame_control_path = GEMINI_WORK / job["id"] / "frame_control.png"
            build_ai_render_frame_control(frame_control_path)
        reference_paths = {
            channel: (
                frame_control_path
                if channel == "frame_control"
                else source / GEMINI_CHANNEL_FILES[channel]
            )
            for channel in reference_channels
        }
        input_paths.extend(reference_paths[channel] for channel in reference_channels)
        reference_hashes = {
            "identity": sha256_file(input_paths[0]),
            **{
                channel: sha256_file(path)
                for channel, path in reference_paths.items()
            },
        }
        render_spec = ai_render_spec.normalize_render_spec(
            with_ai_render_source_contract(job["payload"].get("render_spec"), source),
            name=str(job["payload"].get("render_name") or "").strip(),
        )
        reference_manifest = ai_render_spec.build_reference_manifest(
            reference_channels,
            identity_name=str(job["payload"].get("reference_name") or "identity reference"),
        )
        expected_roles = ["identity", *reference_channels]
        actual_roles = [str(item.get("type")) for item in reference_manifest]
        if actual_roles != expected_roles or len(input_paths) != len(reference_manifest):
            raise RuntimeError(
                "preflight de referências falhou: manifest e arquivos físicos não correspondem"
            )
        prompt = ai_render_spec.compile_provider_prompt(
            render_spec,
            reference_manifest,
            str(
                job["payload"].get(
                    "additional_instructions", job["payload"].get("prompt") or ""
                )
            ).strip(),
            provider=provider_name,
        )
        input_manifest = [
            {
                "index": item["index"],
                "type": item["type"],
                "name": item.get("name"),
                "path": str(path),
                "sha256": reference_hashes[str(item["type"])],
            }
            for item, path in zip(reference_manifest, input_paths)
        ]
        runtime_payload = {
            **job["payload"],
            "compiled_prompt": prompt,
            "prompt_contract_schema": ai_render_spec.PROMPT_SCHEMA,
            "reference_manifest": reference_manifest,
        }
        job["payload"] = runtime_payload
        update_gemini_job(
            job["id"],
            {
                "payload": runtime_payload,
                "preflight": {
                    "status": "passed",
                    "prompt_contract_schema": ai_render_spec.PROMPT_SCHEMA,
                    "reference_manifest": reference_manifest,
                    "input_manifest": input_manifest,
                },
            },
        )
        request = image_generation_provider.GenerationRequest(
            job_id=job["id"],
            prompt=prompt,
            input_images=tuple(input_paths),
            output_path=output,
            model=str(job["payload"]["model"]),
            metadata={
                "source_id": job["payload"]["source_id"],
                "channels": ["identity", *reference_channels],
                "output_size": [2048, 2048],
                "qwen_seed": job["payload"].get("qwen_seed"),
                "gemini_temperature": job["payload"].get("gemini_temperature", 1.0),
                "gemini_top_k": job["payload"].get("gemini_top_k", 64),
                "reference_hashes": reference_hashes,
                "input_manifest": input_manifest,
                "render_spec_schema": job["payload"].get("render_spec", {}).get(
                    "version", ai_render_spec.SCHEMA
                ),
                "prompt_contract_schema": ai_render_spec.PROMPT_SCHEMA,
            },
        )
        update_pipeline_progress(
            GEMINI_JOBS_PATH,
            job["id"],
            stage="generating_image",
            percent=15,
            eta_seconds=300,
        )
        if provider_name == "google":
            provider = image_generation_provider.GoogleImageProvider(
                api_key=gemini_api_key() or None
            )
        elif provider_name == "openai":
            provider = image_generation_provider.OpenAIImageProvider(
                api_key=openai_api_key() or None
            )
        elif provider_name == "qwen":
            provider = image_generation_provider.QwenImageProvider(
                api_key=qwen_api_key() or None
            )
        else:
            provider = image_generation_provider.create_provider(provider_name)
        result = provider.generate(request)
        if result.output_path is None or not result.output_path.is_file():
            raise RuntimeError(f"{provider_name} não retornou um arquivo de imagem")
        from PIL import Image

        update_pipeline_progress(
            GEMINI_JOBS_PATH,
            job["id"],
            stage="validating_output",
            percent=95,
            eta_seconds=5,
        )
        with Image.open(result.output_path) as image:
            if image.size != (2048, 2048):
                raise RuntimeError(
                    f"{provider_name} retornou {image.size}; esperado (2048, 2048)"
                )
        # Keep this as a separate persisted artifact for visual inspection.
        # `gemini_output.png` remains the only canonical pipeline input.
        validation_output = output.with_name("gemini_validation.png")
        validation_report = build_ai_render_validation_overlay(
            result.output_path,
            validation_output,
        )
        validation_report.update(
            {
                "presentation_only": True,
                "source": f"{job['id']}/gemini_output.png",
                "allowed_consumers": ["ai_render_preview", "ai_render_history"],
                "validated": validation_report["violation_count"] == 0,
            }
        )
        finished_at = utc_now()
        response_metadata = result.response_metadata or {}
        audit_metadata = {
            "duration_seconds": elapsed_seconds(started_at, finished_at),
            "request_id": response_metadata.get("request_id"),
            "usage": response_metadata.get("usage"),
            "cost": response_metadata.get("cost"),
            "effective_seed": response_metadata.get("seed")
            if response_metadata.get("seed") is not None
            else job["payload"].get("qwen_seed"),
            "render_spec_schema": job["payload"].get("render_spec", {}).get(
                "version", ai_render_spec.SCHEMA
            ),
            "prompt_contract_schema": ai_render_spec.PROMPT_SCHEMA,
            "reference_hashes": reference_hashes,
        }
        update_gemini_job(
            job["id"],
            {
                "status": "done",
                "validated": validation_report["validated"],
                "finished_at": finished_at,
                "metadata": audit_metadata,
                "report": {
                    "provider": result.provider,
                    "model": result.model,
                    "output_size": [2048, 2048],
                    "response_metadata": result.response_metadata,
                    "validation": validation_report,
                },
                "outputs": {
                    "image": f"{job['id']}/gemini_output.png",
                    "validation": f"{job['id']}/gemini_validation.png",
                    "reference": f"{job['id']}/reference.png",
                    "frame_control": (
                        f"{job['id']}/frame_control.png"
                        if frame_control_path is not None
                        else None
                    ),
                },
                "progress": {"stage": "completed", "percent": 100, "eta_seconds": 0},
            },
        )
    except Exception as exc:  # noqa: BLE001 - job errors are returned to the UI.
        update_gemini_job(
            job["id"],
            {
                "status": "error",
                "validated": False,
                "finished_at": (finished_at := utc_now()),
                "metadata": {
                    "duration_seconds": elapsed_seconds(started_at, finished_at),
                    "request_id": None,
                    "usage": None,
                    "cost": None,
                    "effective_seed": job.get("payload", {}).get("qwen_seed"),
                    "render_spec_schema": job.get("payload", {})
                    .get("render_spec", {})
                    .get("version", ai_render_spec.SCHEMA),
                    "prompt_contract_schema": ai_render_spec.PROMPT_SCHEMA,
                    "reference_hashes": None,
                },
                "error": str(exc),
            },
        )


def run_postprocess_job(job: dict) -> None:
    update_postprocess_job(
        job["id"],
        {
            "status": "running",
            "started_at": utc_now(),
            "progress": {"stage": "preparing_inputs", "percent": 5, "eta_seconds": 900},
        },
    )
    try:
        model_profile = str(job.get("payload", {}).get("model_profile", "anime_x4plus_6b"))
        huggingface_realesrgan.profile(model_profile)
        gemini_job = get_gemini_job(str(job["payload"]["gemini_job_id"]))
        if gemini_job is None or gemini_job.get("status") != "done":
            raise ValueError("o AI Render precisa estar concluído e aprovado")
        source_id = str(gemini_job["payload"]["source_id"])
        structural_dir = _gemini_source_directory(source_id)
        # Never use gemini_validation.png here: it is only a visual QA artifact
        # for the AI Render UI and is intentionally excluded from the pipeline.
        generated_sheet = _safe_work_child(
            GEMINI_WORK,
            f"{gemini_job['id']}/gemini_output.png",
        )
        output = _safe_work_child(POSTPROCESS_WORK, job["id"])
        update_pipeline_progress(
            POSTPROCESS_JOBS_PATH,
            job["id"],
            stage="mask_pass_realesrgan",
            percent=8,
            eta_seconds=1800,
        )
        report = gemini_sprite_postprocess.process(
            generated_sheet,
            structural_dir,
            output,
            rows=8,
            phases=8,
            source_cell=256,
            fps=float(job["payload"].get("fps", 10.0)),
            foot_anchor=(128, 220),
            realesrgan_repo=BASE / "work" / "Real-ESRGAN",
            python_executable=pipeline_python_executable(),
            model_profile=model_profile,
            progress_callback=lambda stage, percent, eta: update_pipeline_progress(
                POSTPROCESS_JOBS_PATH,
                job["id"],
                stage=stage,
                percent=percent,
                eta_seconds=eta,
            ),
        )
        variant_outputs = {
            name: {
                "spritesheet": f"{job['id']}/variants/{name}/spritesheet.png",
                "gif": f"{job['id']}/variants/{name}/animation_all_directions_1-2-5-4-3-8-7-6.gif",
            }
            for name in ("original", "frame_adjustment", "color_cohesion_256", "color_cohesion_128")
        }
        update_postprocess_job(
            job["id"],
            {
                "status": "done",
                "finished_at": utc_now(),
                "report": report,
                "outputs": {
                    "variants": variant_outputs,
                    "asset_manifest": f"{job['id']}/asset_manifest.json",
                    "metadata": f"{job['id']}/render_metadata.json",
                },
                "progress": {"stage": "completed", "percent": 100, "eta_seconds": 0},
            },
        )
    except Exception as exc:  # noqa: BLE001 - job errors are returned to the UI.
        update_postprocess_job(
            job["id"],
            {"status": "error", "finished_at": utc_now(), "error": str(exc)},
        )


def run_postprocess_batch(jobs: list[dict]) -> None:
    """Process a submitted batch serially to protect the local CPU pipeline."""
    for job in jobs:
        run_postprocess_job(job)


def catalog_assets(query: str = "", kind: str = "", category: str = "") -> list[dict]:
    manifest = ensure_relationship_catalog()
    query = query.casefold().strip()
    rows = manifest.get("assets", [])
    result = []
    for item in rows:
        haystack = " ".join(
            [
                str(item.get("name", "")),
                str(item.get("source", "")),
                str(item.get("category", "")),
                " ".join(str(tag) for tag in item.get("tags", [])),
                " ".join(str(tag) for tag in item.get("annotation", {}).get("tags", [])),
            ]
        ).casefold()
        if query and query not in haystack:
            continue
        if kind and item.get("kind") != kind:
            continue
        if category and item.get("category") != category:
            continue
        result.append(item)
    return result


def _json(handler: BaseHTTPRequestHandler, data, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _file(handler: BaseHTTPRequestHandler, path: Path) -> None:
    if not path.is_file():
        _json(handler, {"error": "not found"}, 404)
        return
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    data = path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _download(handler: BaseHTTPRequestHandler, data: bytes, filename: str, content_type: str) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    server_version = "SpriteLabSemantic/1.0"

    def log_message(self, fmt, *args):
        return

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/state":
                manifest = ensure_relationship_catalog()
                _json(
                    self,
                    {
                        "catalog": {
                            "assets": manifest.get("asset_count", 0),
                            "animations": manifest.get("animation_count", 0),
                            "relationships": manifest.get("relationship_count", 0),
                            "generated_at": manifest.get("generated_at"),
                        },
                    },
                )
                return
            if path == "/api/catalog":
                assets = catalog_assets(
                    query.get("query", [""])[0],
                    query.get("kind", [""])[0],
                    query.get("category", [""])[0],
                )
                _json(self, {"assets": assets, "count": len(assets)})
                return
            if path == "/api/animations":
                manifest = ensure_relationship_catalog()
                q = query.get("query", [""])[0].casefold()
                annotations = read_action_annotations()
                rows = []
                for item in manifest.get("animations", []):
                    annotation = {
                        **action_annotation_defaults(item),
                        **annotations.get(str(item.get("id")), {}),
                    }
                    row = {**item, "annotation": annotation}
                    if not q or q in json.dumps(row, ensure_ascii=False).casefold():
                        rows.append(row)
                _json(self, {"animations": rows, "count": len(rows)})
                return
            if path == "/api/relationships":
                relationships = ensure_relationship_catalog().get("relationships", [])
                _json(
                    self,
                    {
                        "relationships": [
                            {**item, "export": composition_export.describe_export(item)}
                            for item in relationships
                        ]
                    },
                )
                return
            if path == "/api/sprite-jobs":
                _json(self, {"jobs": read_sprite_jobs()})
                return
            if path == "/api/gemini/sources":
                _json(self, {"sources": list_gemini_sources()})
                return
            if path == "/api/ai-render-spec/defaults":
                _json(
                    self,
                    {
                        "schema": ai_render_spec.SCHEMA,
                        "asset_modes": list(ai_render_spec.ASSET_MODES),
                        "row_semantics": ai_render_spec.ROW_SEMANTICS,
                        "column_semantics": ai_render_spec.COLUMN_SEMANTICS,
                        "reference_roles": ai_render_spec.REFERENCE_ROLES,
                        "spec": ai_render_spec.default_render_spec(),
                    },
                )
                return
            if path == "/api/gemini/references":
                _json(self, {"references": list_gemini_references()})
                return
            if path == "/api/config/gemini":
                _json(self, {"config": gemini_config_status()})
                return
            if path == "/api/config/openai":
                _json(self, {"config": openai_config_status()})
                return
            if path == "/api/config/qwen":
                _json(self, {"config": qwen_config_status()})
                return
            if path == "/api/config/gemini-prompt":
                _json(self, {"prompt": read_gemini_prompt()})
                return
            if path == "/api/config/huggingface":
                _json(self, {"config": huggingface_realesrgan.config_status()})
                return
            if path == "/api/gemini-jobs":
                _json(self, {"jobs": read_gemini_jobs()})
                return
            if path == "/api/postprocess-jobs":
                _json(self, {"jobs": read_postprocess_jobs()})
                return
            if path == "/api/render-profiles":
                _json(self, {"render_profiles": render_profile.list_profiles()})
                return
            if path == "/api/camera-presets":
                _json(self, {"camera_presets": render_profile.list_camera_presets()})
                return
            if path == "/api/asset-contract":
                _json(self, asset_manifest.asset_contract_options())
                return
            if path.startswith("/api/sprite-jobs/") and path.endswith("/download"):
                job_id = unquote(path.removeprefix("/api/sprite-jobs/").removesuffix("/download").strip("/"))
                data, filename = build_sprite_download(job_id)
                _download(self, data, filename, "application/zip")
                return
            if path == "/api/bug-reports":
                _json(self, {"reports": read_bug_reports()})
                return
            if path.startswith("/api/catalog/"):
                asset_id = unquote(path.removeprefix("/api/catalog/"))
                manifest = ensure_relationship_catalog()
                asset = next((item for item in manifest.get("assets", []) if item.get("id") == asset_id), None)
                if asset is None:
                    _json(self, {"error": "not found"}, 404)
                    return
                action_annotations = read_action_annotations()
                asset["animations"] = [
                    {
                        **item,
                        "annotation": {
                            **action_annotation_defaults(item),
                            **action_annotations.get(str(item.get("id")), {}),
                        },
                    }
                    for item in manifest.get("animations", []) if item.get("asset_id") == asset_id
                ]
                _json(self, asset)
                return
            if path.startswith("/api/assets/") and path.endswith("/viewer"):
                asset_id = unquote(path.removeprefix("/api/assets/").removesuffix("/viewer").strip("/"))
                _json(self, model_cache.viewer_descriptor(asset_id))
                return
            if path.startswith("/api/sprite-jobs/"):
                job = get_sprite_job(unquote(path.removeprefix("/api/sprite-jobs/")))
                _json(self, job or {"error": "not found"}, 200 if job else 404)
                return
            if path.startswith("/api/gemini-jobs/"):
                job = get_gemini_job(unquote(path.removeprefix("/api/gemini-jobs/")))
                _json(self, job or {"error": "not found"}, 200 if job else 404)
                return
            if path.startswith("/api/postprocess-jobs/"):
                job = get_postprocess_job(
                    unquote(path.removeprefix("/api/postprocess-jobs/"))
                )
                _json(self, job or {"error": "not found"}, 200 if job else 404)
                return
            if path in {
                "/", "/index.html", "/catalog", "/composition", "/sprites",
                "/gemini", "/postprocess", "/env-atlas",
            }:
                _file(self, WEB / "index.html")
                return
            if path.startswith("/web/"):
                target = (WEB / Path(unquote(path.removeprefix("/web/")))).resolve()
                if not target.is_relative_to(WEB.resolve()):
                    _json(self, {"error": "invalid web path"}, 400)
                    return
                _file(self, target)
                return
            if path.startswith("/sprite-outputs/"):
                relative = Path(unquote(path.removeprefix("/sprite-outputs/")))
                target = (SPRITE_WORK / relative).resolve()
                if not target.is_relative_to(SPRITE_WORK.resolve()):
                    _json(self, {"error": "invalid sprite output path"}, 400)
                    return
                _file(self, target)
                return
            if path.startswith("/gemini-outputs/"):
                relative = Path(unquote(path.removeprefix("/gemini-outputs/")))
                target = (GEMINI_WORK / relative).resolve()
                if not target.is_relative_to(GEMINI_WORK.resolve()):
                    _json(self, {"error": "invalid Gemini output path"}, 400)
                    return
                _file(self, target)
                return
            if path.startswith("/gemini-reference-outputs/"):
                reference_id = unquote(path.removeprefix("/gemini-reference-outputs/")).strip("/")
                _file(self, gemini_reference_path(reference_id))
                return
            if path.startswith("/postprocess-outputs/"):
                relative = Path(unquote(path.removeprefix("/postprocess-outputs/")))
                target = (POSTPROCESS_WORK / relative).resolve()
                if not target.is_relative_to(POSTPROCESS_WORK.resolve()):
                    _json(self, {"error": "invalid postprocess output path"}, 400)
                    return
                _file(self, target)
                return
            if path.startswith("/env-atlas/"):
                relative = Path(unquote(path.removeprefix("/env-atlas/")))
                env_atlas_dir = BASE / "env_atlas"
                target = (env_atlas_dir / relative).resolve()
                if not target.is_relative_to(env_atlas_dir.resolve()):
                    _json(self, {"error": "invalid env-atlas output path"}, 400)
                    return
                _file(self, target)
                return
            if path.startswith("/validation-outputs/"):
                relative = Path(unquote(path.removeprefix("/validation-outputs/")))
                validation_root = BASE / "work" / "validation"
                target = (validation_root / relative).resolve()
                if not target.is_relative_to(validation_root.resolve()):
                    _json(self, {"error": "invalid validation output path"}, 400)
                    return
                _file(self, target)
                return
            if path.startswith("/composition-exports/"):
                relative = Path(unquote(path.removeprefix("/composition-exports/")))
                target = (composition_export.EXPORT_ROOT / relative).resolve()
                if not target.is_relative_to(composition_export.EXPORT_ROOT.resolve()):
                    _json(self, {"error": "invalid composition export path"}, 400)
                    return
                _file(self, target)
                return
            if path.startswith("/assets/"):
                parts = path.removeprefix("/assets/").split("/")
                if len(parts) >= 2 and parts[1] == "model" and len(parts) == 2:
                    _file(self, model_cache.model_path(unquote(parts[0])))
                    return
                if len(parts) >= 3 and parts[1] == "source":
                    asset_id = unquote(parts[0])
                    relative = unquote("/".join(parts[2:]))
                    _file(self, model_cache.source_path(asset_id, relative))
                    return
                _json(self, {"error": "invalid asset path"}, 400)
                return
            _json(self, {"error": "not found"}, 404)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError, KeyError) as exc:
            _json(self, {"error": str(exc)}, 500)

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        body = _body(self)
        try:
            if path == "/api/reindex":
                manifest = rel.build_relationship_catalog()
                _json(self, {"ok": True, "asset_count": manifest["asset_count"], "animation_count": manifest["animation_count"]})
                return
            if path == "/api/annotate":
                asset_id = str(body.get("asset_id", ""))
                patch = body.get("patch", {})
                if not asset_id or not isinstance(patch, dict):
                    _json(self, {"error": "asset_id e patch são obrigatórios"}, 400)
                    return
                annotation = rel.update_annotation(asset_id, patch)
                manifest = rel.build_relationship_catalog()
                _json(self, {"annotation": annotation, "catalog_generated_at": manifest["generated_at"]})
                return
            if path == "/api/annotate-action":
                action_id = str(body.get("action_id", ""))
                patch = body.get("patch", {})
                if not action_id or not isinstance(patch, dict):
                    _json(self, {"error": "action_id e patch são obrigatórios"}, 400)
                    return
                annotation = update_action_annotation(action_id, patch)
                _json(self, {"annotation": annotation})
                return
            if path == "/api/relationships":
                relationship = rel.add_relationship(body)
                try:
                    export = composition_export.export_relationship(relationship)
                except (OSError, ValueError, RuntimeError, KeyError) as exc:
                    _json(
                        self,
                        {
                            "error": f"Composição salva, mas a exportação GLB falhou: {exc}",
                            "relationship": relationship,
                        },
                        500,
                    )
                    return
                manifest = rel.build_relationship_catalog()
                _json(
                    self,
                    {
                        "relationship": {**relationship, "export": export},
                        "export": export,
                        "catalog_generated_at": manifest["generated_at"],
                    },
                    201,
                )
                return
            if path == "/api/relationships/delete":
                relationship_id = str(body.get("relationship_id", ""))
                if not relationship_id:
                    _json(self, {"error": "relationship_id é obrigatório"}, 400)
                    return
                relationship = rel.delete_relationship(relationship_id)
                manifest = rel.build_relationship_catalog()
                _json(self, {"relationship": relationship, "catalog_generated_at": manifest["generated_at"]})
                return
            if path == "/api/sprite-render":
                payload = body.get("payload", body)
                if not isinstance(payload, dict):
                    _json(self, {"error": "payload inválido"}, 400)
                    return
                if not payload.get("relationship_id"):
                    _json(self, {"error": "relationship_id é obrigatório"}, 400)
                    return
                if not payload.get("render_profile_id"):
                    _json(self, {"error": "render_profile_id é obrigatório"}, 400)
                    return
                try:
                    normalized_asset = asset_manifest.normalize_asset_spec(payload)
                except ValueError as exc:
                    _json(self, {"error": str(exc)}, 400)
                    return
                if normalized_asset["type"] not in {"actor", "prop_animated"}:
                    _json(
                        self,
                        {
                            "error": (
                                f"asset_type={normalized_asset['type']} exige seu worker Blender "
                                "especializado; esta rota ainda renderiza composições de personagem"
                            )
                        },
                        400,
                    )
                    return
                payload = {
                    **payload,
                    "asset_type": normalized_asset["type"],
                    "representation": normalized_asset["representation"],
                    "capabilities": normalized_asset["capabilities"],
                }
                render_profile.load(str(payload["render_profile_id"]))
                sprite_render.render_dimensions(payload)
                job_id = f"sprite_{uuid.uuid4().hex[:16]}"
                job = {"id": job_id, "status": "queued", "created_at": utc_now(), "payload": payload}
                with JOB_LOCK:
                    jobs = read_sprite_jobs()
                    jobs.append(job)
                    write_json_atomic(SPRITE_JOBS_PATH, jobs)
                threading.Thread(target=run_sprite_job, args=(job,), daemon=True).start()
                _json(self, job, 202)
                return
            if path in {"/api/tile-render", "/api/prop-render", "/api/vfx-render"}:
                import tile_render as tile_render_mod

                payload = body.get("payload", body)
                if not isinstance(payload, dict):
                    _json(self, {"error": "payload inválido"}, 400)
                    return
                if not payload.get("render_profile_id"):
                    _json(self, {"error": "render_profile_id é obrigatório"}, 400)
                    return
                try:
                    normalized_asset = asset_manifest.normalize_asset_spec(payload)
                except ValueError as exc:
                    _json(self, {"error": str(exc)}, 400)
                    return
                expected_type = {
                    "/api/tile-render": "tile",
                    "/api/prop-render": "prop_static",
                    "/api/vfx-render": "vfx",
                }.get(path)
                if normalized_asset["type"] != expected_type:
                    _json(
                        self,
                        {"error": f"esta rota espera asset_type={expected_type}, recebeu {normalized_asset['type']}"},
                        400,
                    )
                    return
                payload = {
                    **payload,
                    "asset_type": normalized_asset["type"],
                    "representation": normalized_asset["representation"],
                    "capabilities": normalized_asset["capabilities"],
                }
                render_profile.load(str(payload["render_profile_id"]))
                job_id = f"asset_{uuid.uuid4().hex[:16]}"
                job = {"id": job_id, "status": "queued", "created_at": utc_now(), "payload": payload}
                with JOB_LOCK:
                    jobs = read_sprite_jobs()
                    jobs.append(job)
                    write_json_atomic(SPRITE_JOBS_PATH, jobs)
                threading.Thread(
                    target=run_asset_job,
                    args=(job, tile_render_mod),
                    daemon=True,
                ).start()
                _json(self, job, 202)
                return
            if path == "/api/env-atlas":
                payload = body.get("payload", body)
                if not isinstance(payload, dict):
                    _json(self, {"error": "payload inválido"}, 400)
                    return
                blender_path = payload.get("blender_path", "/usr/bin/blender")
                directions = payload.get("directions", 8)
                render_profile_id = payload.get("render_profile", "env_atlas_v1")
                selected_assets = payload.get("selected_assets", None)

                import subprocess
                import time

                env_atlas_dir = BASE / "env_atlas"
                output_dir = env_atlas_dir / "output"
                output_dir.mkdir(parents=True, exist_ok=True)

                render_script = BASE / "render_env_atlas.py"
                cmd = [
                    sys.executable,
                    str(render_script),
                    "--blender", blender_path,
                    "--output", str(output_dir),
                ]

                try:
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        cwd=str(BASE),
                    )
                    if result.returncode != 0:
                        _json(self, {"error": f"Blender falhou: {result.stderr[-500:]}"}, 500)
                        return

                    atlas_path = output_dir / "env_atlas.png"
                    if not atlas_path.exists():
                        _json(self, {"error": "Atlas não foi gerado"}, 500)
                        return

                    _json(self, {
                        "status": "done",
                        "atlas_path": f"/env-atlas/output/env_atlas.png",
                        "cells": 64,
                        "size": "2048×2048",
                    }, 200)
                except subprocess.TimeoutExpired:
                    _json(self, {"error": "Timeout na renderização"}, 500)
                except Exception as exc:
                    _json(self, {"error": str(exc)}, 500)
                return
            if path == "/api/gemini/references":
                data_url = str(body.get("reference_data", ""))
                name = str(body.get("name", "referência"))
                if len(name) > 240:
                    _json(self, {"error": "nome da referência é muito longo"}, 400)
                    return
                reference = save_gemini_reference(data_url, name)
                _json(self, {"reference": reference}, 201)
                return
            if path == "/api/ai-render-spec/compile":
                try:
                    channels = normalize_gemini_channels(
                        body.get("reference_channels", body.get("blender_channels"))
                    )
                except ValueError as error:
                    _json(self, {"error": str(error)}, 400)
                    return
                source_id = str(body.get("source_id") or "").strip()
                source = _gemini_source_directory(source_id) if source_id else None
                render_spec_input = (
                    with_ai_render_source_contract(body.get("render_spec"), source)
                    if source is not None
                    else body.get("render_spec")
                )
                render_spec = ai_render_spec.normalize_render_spec(
                    render_spec_input,
                    name=str(body.get("render_name", "")).strip(),
                )
                additional_instructions = str(
                    body.get("additional_instructions", body.get("prompt", ""))
                ).strip()
                if len(additional_instructions) > 12000:
                    _json(
                        self,
                        {"error": "additional_instructions deve ter até 12000 caracteres"},
                        400,
                    )
                    return
                try:
                    provider = image_generation_provider.normalize_provider(
                        str(body.get("provider", "openai"))
                    )
                except ValueError as error:
                    _json(self, {"error": str(error)}, 400)
                    return
                reference_manifest = ai_render_spec.build_reference_manifest(
                    channels,
                    identity_name=str(body.get("reference_name") or "identity reference"),
                )
                try:
                    compiled_prompt = ai_render_spec.compile_provider_prompt(
                        render_spec,
                        reference_manifest,
                        additional_instructions,
                        provider=provider,
                    )
                except ValueError as error:
                    _json(self, {"error": str(error)}, 400)
                    return
                _json(
                    self,
                    {
                        "schema": ai_render_spec.SCHEMA,
                        "prompt_schema": ai_render_spec.PROMPT_SCHEMA,
                        "render_spec": render_spec,
                        "reference_manifest": reference_manifest,
                        "compiled_prompt": compiled_prompt,
                    },
                )
                return
            if path == "/api/gemini-render":
                source_id = str(body.get("source_id", "")).strip()
                render_name = str(body.get("render_name", "")).strip()
                prompt = str(body.get("prompt", "")).strip()
                additional_instructions = str(
                    body.get("additional_instructions", prompt)
                ).strip()
                try:
                    provider = image_generation_provider.normalize_provider(
                        str(body.get("provider", "openai"))
                    )
                except ValueError as error:
                    _json(self, {"error": str(error)}, 400)
                    return
                model = str(
                    body.get("model") or image_generation_provider.default_model(provider)
                ).strip()
                if not source_id:
                    _json(self, {"error": "source_id é obrigatório"}, 400)
                    return
                if not render_name or len(render_name) > 160:
                    _json(self, {"error": "render_name é obrigatório e deve ter até 160 caracteres"}, 400)
                    return
                if len(additional_instructions) > 12000:
                    _json(
                        self,
                        {"error": "additional_instructions deve ter até 12000 caracteres"},
                        400,
                    )
                    return
                if (
                    not re.fullmatch(r"[A-Za-z0-9._/-]{3,160}", model)
                    or model.startswith("/")
                    or model.endswith("/")
                    or ".." in model
                    or "//" in model
                ):
                    _json(self, {"error": "model inválido"}, 400)
                    return
                structural_source = _gemini_source_directory(source_id)
                try:
                    reference_channels = normalize_gemini_channels(
                        body.get("reference_channels", body.get("blender_channels"))
                    )
                except ValueError as error:
                    _json(self, {"error": str(error)}, 400)
                    return
                if provider == "qwen" and len(reference_channels) > 2:
                    _json(
                        self,
                        {
                            "error": (
                                "O Qwen aceita até três imagens por chamada: "
                                "a referência de identidade e no máximo duas "
                                "referências estruturais selecionadas."
                            )
                        },
                        400,
                    )
                    return
                qwen_seed = body.get("qwen_seed")
                if qwen_seed is not None and qwen_seed != "":
                    try:
                        qwen_seed = int(qwen_seed)
                    except (TypeError, ValueError):
                        _json(self, {"error": "qwen_seed deve ser um inteiro"}, 400)
                        return
                    if qwen_seed < 0:
                        _json(self, {"error": "qwen_seed deve ser maior ou igual a zero"}, 400)
                        return
                else:
                    qwen_seed = None
                if provider == "google":
                    gemini_temperature = body.get("gemini_temperature", 1.0)
                    try:
                        gemini_temperature = float(gemini_temperature)
                    except (TypeError, ValueError):
                        _json(self, {"error": "gemini_temperature deve ser um número entre 0 e 1"}, 400)
                        return
                    if not 0 <= gemini_temperature <= 1:
                        _json(self, {"error": "gemini_temperature deve estar entre 0 e 1"}, 400)
                        return
                    gemini_top_k = body.get("gemini_top_k", 64)
                    try:
                        gemini_top_k = int(gemini_top_k)
                    except (TypeError, ValueError):
                        _json(self, {"error": "gemini_top_k deve ser um inteiro entre 1 e 1000"}, 400)
                        return
                    if not 1 <= gemini_top_k <= 1000:
                        _json(self, {"error": "gemini_top_k deve estar entre 1 e 1000"}, 400)
                        return
                else:
                    gemini_temperature = None
                    gemini_top_k = None
                reference_id = str(body.get("reference_id", "")).strip()
                reference_data = str(body.get("reference_data", ""))
                reference_name = str(body.get("reference_name") or "identity reference").strip()
                if reference_id:
                    cached_reference = get_gemini_reference(reference_id)
                    if cached_reference is None:
                        _json(self, {"error": "referência Gemini inválida"}, 400)
                        return
                    reference_name = str(
                        cached_reference.get("name") or reference_name
                    ).strip()
                elif not reference_data:
                    _json(self, {"error": "selecione ou envie uma referência"}, 400)
                    return
                render_spec = ai_render_spec.normalize_render_spec(
                    with_ai_render_source_contract(
                        body.get("render_spec"), structural_source
                    ),
                    name=render_name,
                )
                reference_manifest = ai_render_spec.build_reference_manifest(
                    reference_channels,
                    identity_name=reference_name,
                )
                try:
                    compiled_prompt = ai_render_spec.compile_provider_prompt(
                        render_spec,
                        reference_manifest,
                        additional_instructions,
                        provider=provider,
                    )
                except ValueError as error:
                    _json(self, {"error": str(error)}, 400)
                    return
                # The job is an AI Render regardless of which provider is used.
                # Keep provider details in the payload instead of encoding
                # Gemini into the public identifier.
                job_id = f"{AI_RENDER_JOB_PREFIX}{uuid.uuid4().hex[:16]}"
                reference = GEMINI_WORK / job_id / "reference.png"
                if reference_id:
                    reference.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(gemini_reference_path(reference_id), reference)
                    reference_report = {"cached_id": reference_id}
                else:
                    reference_report = _decode_reference(reference_data, reference)
                job = {
                    "id": job_id,
                    "status": "queued",
                    "validated": False,
                    "created_at": utc_now(),
                    "payload": {
                        "source_id": source_id,
                        "render_name": render_name,
                        "prompt": prompt,
                        "additional_instructions": additional_instructions,
                        "compiled_prompt": compiled_prompt,
                        "prompt_contract_schema": ai_render_spec.PROMPT_SCHEMA,
                        "render_spec": render_spec,
                        "direction_rows": copy.deepcopy(render_spec.get("rows", [])),
                        "reference_manifest": reference_manifest,
                        "provider": provider,
                        "model": model,
                        "reference_name": reference_name,
                        "reference_id": reference_id or None,
                        "reference_channels": reference_channels,
                        # Keep the legacy field for older clients and saved jobs.
                        "blender_channels": [
                            channel
                            for channel in reference_channels
                            if channel in GEMINI_CHANNEL_FILES
                        ],
                        "frame_control": "frame_control" in reference_channels,
                        "qwen_seed": qwen_seed,
                        "gemini_temperature": gemini_temperature,
                        "gemini_top_k": gemini_top_k,
                        "reference": reference_report,
                    },
                }
                with JOB_LOCK:
                    jobs = read_gemini_jobs()
                    normalized_name = render_name.casefold()
                    if any(
                        str(item.get("payload", {}).get("render_name", "")).strip().casefold()
                        == normalized_name
                        for item in jobs
                    ):
                        _json(self, {"error": f"já existe um AI Render com o nome {render_name!r}"}, 409)
                        return
                    jobs.append(job)
                    write_json_atomic(GEMINI_JOBS_PATH, jobs)
                threading.Thread(target=run_gemini_job, args=(job,), daemon=True).start()
                _json(self, job, 202)
                return
            if path == "/api/config/gemini":
                api_key = body.get("api_key")
                if not isinstance(api_key, str):
                    _json(self, {"error": "api_key deve ser texto"}, 400)
                    return
                with JOB_LOCK:
                    config = save_gemini_api_key(api_key)
                _json(self, {"config": config})
                return
            if path == "/api/config/openai":
                api_key = body.get("api_key")
                if not isinstance(api_key, str):
                    _json(self, {"error": "api_key deve ser texto"}, 400)
                    return
                with JOB_LOCK:
                    config = save_openai_api_key(api_key)
                _json(self, {"config": config})
                return
            if path == "/api/config/qwen":
                api_key = body.get("api_key")
                if not isinstance(api_key, str):
                    _json(self, {"error": "api_key deve ser texto"}, 400)
                    return
                with JOB_LOCK:
                    config = save_qwen_api_key(api_key)
                _json(self, {"config": config})
                return
            if path == "/api/config/gemini-prompt":
                prompt = body.get("prompt")
                if not isinstance(prompt, str):
                    _json(self, {"error": "prompt deve ser texto"}, 400)
                    return
                _json(self, {"prompt": save_gemini_prompt(prompt)})
                return
            if path == "/api/config/huggingface":
                api_key = body.get("api_key")
                if not isinstance(api_key, str):
                    _json(self, {"error": "api_key deve ser texto"}, 400)
                    return
                with JOB_LOCK:
                    config = huggingface_realesrgan.save_api_token(api_key)
                _json(self, {"config": config})
                return
            if path == "/api/postprocess":
                requested_ids = body.get("gemini_job_ids")
                if isinstance(requested_ids, list):
                    gemini_job_ids = [str(value).strip() for value in requested_ids]
                else:
                    legacy_id = str(body.get("gemini_job_id", "")).strip()
                    gemini_job_ids = [legacy_id] if legacy_id else []
                gemini_job_ids = list(
                    dict.fromkeys(job_id for job_id in gemini_job_ids if job_id)
                )
                if not gemini_job_ids:
                    _json(self, {"error": "selecione pelo menos um AI Render concluído"}, 400)
                    return
                source_jobs = [get_gemini_job(job_id) for job_id in gemini_job_ids]
                if any(job is None or job.get("status") != "done" for job in source_jobs):
                    _json(self, {"error": "todos os AI Renders precisam estar concluídos"}, 400)
                    return
                model_profile = str(body.get("model_profile", "anime_x4plus_6b")).strip() or "anime_x4plus_6b"
                try:
                    huggingface_realesrgan.profile(model_profile)
                except ValueError as error:
                    _json(self, {"error": str(error)}, 400)
                    return
                fps = max(1.0, min(60.0, float(body.get("fps", 10.0))))
                batch_id = f"post_batch_{uuid.uuid4().hex[:16]}" if len(source_jobs) > 1 else None
                created_jobs = []
                for index, (gemini_job_id, gemini_job) in enumerate(
                    zip(gemini_job_ids, source_jobs),
                    start=1,
                ):
                    job_id = f"post_{uuid.uuid4().hex[:16]}"
                    payload = {
                        "gemini_job_id": gemini_job_id,
                        "ai_render_name": str(
                            gemini_job.get("payload", {}).get("render_name")
                            or gemini_job_id
                        ).strip(),
                        "fps": fps,
                        "model_profile": model_profile,
                    }
                    if batch_id:
                        payload.update(
                            {
                                "batch_id": batch_id,
                                "batch_index": index,
                                "batch_total": len(source_jobs),
                            }
                        )
                    created_jobs.append(
                        {
                            "id": job_id,
                            "status": "queued",
                            "created_at": utc_now(),
                            "payload": payload,
                        }
                    )
                with JOB_LOCK:
                    jobs = read_postprocess_jobs()
                    jobs.extend(created_jobs)
                    write_json_atomic(POSTPROCESS_JOBS_PATH, jobs)
                if batch_id:
                    threading.Thread(
                        target=run_postprocess_batch,
                        args=(created_jobs,),
                        daemon=True,
                    ).start()
                    _json(
                        self,
                        {"batch_id": batch_id, "jobs": created_jobs, "count": len(created_jobs)},
                        202,
                    )
                else:
                    threading.Thread(
                        target=run_postprocess_job,
                        args=(created_jobs[0],),
                        daemon=True,
                    ).start()
                    _json(self, created_jobs[0], 202)
                return
            if path == "/api/bug-reports":
                report = create_bug_report(body)
                _json(self, {"report": report}, 201)
                return
            _json(self, {"error": "not found"}, 404)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError, KeyError) as exc:
            _json(self, {"error": str(exc)}, 400)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprite Lab semantic catalog UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    ensure_relationship_catalog()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Sprite Lab semantic catalog em http://{args.host}:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
