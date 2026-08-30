#!/usr/bin/env python3
"""Local semantic catalog UI for the canonical Sprite Lab generation tools."""
from __future__ import annotations

import argparse
import base64
import binascii
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import relationship_catalog as rel
import composition_export
import gemini_sprite_postprocess
import huggingface_realesrgan
import image_generation_provider
import model_cache
import sprite_render
import render_profile
from build_render_channel_sheets import build_channel


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
GEMINI_REFERENCES_PATH = STATE / "gemini_references.json"
GEMINI_REFERENCES_WORK = BASE / "work" / "gemini-references"
GEMINI_PROMPT_PATH = STATE / "gemini_prompt.txt"
JOB_LOCK = threading.Lock()
BUG_TYPES = {
    "render_failure": "Falha em renderizar",
    "cannot_identify": "Não consigo identificar",
    "other": "Outros",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def list_gemini_sources() -> list[dict]:
    sources: list[dict] = []
    if not SPRITE_WORK.is_dir():
        return sources
    sprite_created_at = {
        str(job.get("id")): str(job.get("created_at") or "")
        for job in read_sprite_jobs()
        if job.get("id")
    }
    required = {
        "beauty": "spritesheet_beauty.png",
        "bones": "spritesheet_bones.png",
        "lineart": "spritesheet_lineart.png",
    }
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
    for name in ("spritesheet.png", "render.json", "render_metadata.json"):
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
    """Assemble the structural channels consumed by the Gemini pipeline."""
    worker = report.get("worker") or {}
    cell = worker.get("cell") or [report.get("resolution", 256)] * 2
    size = int(cell[0])
    rows = len(worker.get("directions") or []) or int(report.get("rows", 8))
    columns = int(report.get("phases", 8))
    for channel in ("beauty", "bones", "lineart"):
        build_channel(output, channel, rows, columns, size)


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


def run_gemini_job(job: dict) -> None:
    update_gemini_job(
        job["id"],
        {
            "status": "running",
            "started_at": utc_now(),
            "progress": {"stage": "preparing_inputs", "percent": 5, "eta_seconds": 300},
        },
    )
    try:
        source = _gemini_source_directory(str(job["payload"]["source_id"]))
        output = GEMINI_WORK / job["id"] / "gemini_output.png"
        input_paths = [
            GEMINI_WORK / job["id"] / "reference.png",
            source / "spritesheet_beauty.png",
            source / "spritesheet_bones.png",
            source / "spritesheet_lineart.png",
        ]
        reference_id = str(job["payload"].get("reference_id", "")).strip()
        if reference_id:
            shutil.copy2(gemini_reference_path(reference_id), input_paths[0])
        prompt = str(job["payload"]["prompt"]).strip()
        prompt = (
            f"{prompt}\n\nUse the four uploaded images in this order: first image is the "
            "authoritative character reference; second is the aligned beauty "
            "spritesheet; third is the aligned bones guide; fourth is the aligned "
            "lineart guide. Preserve the 8x8 grid, cell boundaries, camera, pose, "
            "direction, phase, scale and foot anchor. Bones and lineart are guides "
            "only and must not appear in the final artwork. Return exactly one "
            "2048x2048 PNG spritesheet with no labels, borders or extra panels."
        )
        request = image_generation_provider.GenerationRequest(
            job_id=job["id"],
            prompt=prompt,
            input_images=tuple(input_paths),
            output_path=output,
            model=str(job["payload"]["model"]),
            metadata={
                "source_id": job["payload"]["source_id"],
                "channels": ["identity", "beauty", "bones", "lineart"],
                "output_size": [2048, 2048],
            },
        )
        update_pipeline_progress(
            GEMINI_JOBS_PATH,
            job["id"],
            stage="generating_image",
            percent=15,
            eta_seconds=300,
        )
        result = image_generation_provider.GoogleImageProvider(
            api_key=gemini_api_key() or None
        ).generate(request)
        if result.output_path is None or not result.output_path.is_file():
            raise RuntimeError("Gemini não retornou um arquivo de imagem")
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
                    f"Gemini retornou {image.size}; esperado (2048, 2048)"
                )
        update_gemini_job(
            job["id"],
            {
                "status": "done",
                "finished_at": utc_now(),
                "report": {
                    "provider": result.provider,
                    "model": result.model,
                    "output_size": [2048, 2048],
                    "response_metadata": result.response_metadata,
                },
                "outputs": {
                    "image": f"{job['id']}/gemini_output.png",
                    "reference": f"{job['id']}/reference.png",
                },
                "progress": {"stage": "completed", "percent": 100, "eta_seconds": 0},
            },
        )
    except Exception as exc:  # noqa: BLE001 - job errors are returned to the UI.
        update_gemini_job(
            job["id"],
            {"status": "error", "finished_at": utc_now(), "error": str(exc)},
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
            raise ValueError("o output Gemini precisa estar concluído e aprovado")
        source_id = str(gemini_job["payload"]["source_id"])
        structural_dir = _gemini_source_directory(source_id)
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
                "outputs": {"variants": variant_outputs},
                "progress": {"stage": "completed", "percent": 100, "eta_seconds": 0},
            },
        )
    except Exception as exc:  # noqa: BLE001 - job errors are returned to the UI.
        update_postprocess_job(
            job["id"],
            {"status": "error", "finished_at": utc_now(), "error": str(exc)},
        )


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
            if path == "/api/gemini/references":
                _json(self, {"references": list_gemini_references()})
                return
            if path == "/api/config/gemini":
                _json(self, {"config": gemini_config_status()})
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
                "/gemini", "/postprocess",
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
            if path == "/api/gemini/references":
                data_url = str(body.get("reference_data", ""))
                name = str(body.get("name", "referência"))
                if len(name) > 240:
                    _json(self, {"error": "nome da referência é muito longo"}, 400)
                    return
                reference = save_gemini_reference(data_url, name)
                _json(self, {"reference": reference}, 201)
                return
            if path == "/api/gemini-render":
                source_id = str(body.get("source_id", "")).strip()
                prompt = str(body.get("prompt", "")).strip()
                model = str(body.get("model", "gemini-3-pro-image")).strip()
                if not source_id:
                    _json(self, {"error": "source_id é obrigatório"}, 400)
                    return
                if not prompt or len(prompt) > 12000:
                    _json(self, {"error": "prompt deve ter entre 1 e 12000 caracteres"}, 400)
                    return
                if not re.fullmatch(r"[A-Za-z0-9._-]{3,120}", model):
                    _json(self, {"error": "model inválido"}, 400)
                    return
                _gemini_source_directory(source_id)
                reference_id = str(body.get("reference_id", "")).strip()
                reference_data = str(body.get("reference_data", ""))
                if reference_id:
                    reference = get_gemini_reference(reference_id)
                    if reference is None:
                        _json(self, {"error": "referência Gemini inválida"}, 400)
                        return
                elif not reference_data:
                    _json(self, {"error": "selecione ou envie uma referência"}, 400)
                    return
                job_id = f"gemini_{uuid.uuid4().hex[:16]}"
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
                    "created_at": utc_now(),
                    "payload": {
                        "source_id": source_id,
                        "prompt": prompt,
                        "model": model,
                        "reference_name": str(body.get("reference_name", "reference")),
                        "reference_id": reference_id or None,
                        "reference": reference_report,
                    },
                }
                with JOB_LOCK:
                    jobs = read_gemini_jobs()
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
                gemini_job_id = str(body.get("gemini_job_id", "")).strip()
                gemini_job = get_gemini_job(gemini_job_id)
                if gemini_job is None or gemini_job.get("status") != "done":
                    _json(self, {"error": "selecione um output Gemini concluído"}, 400)
                    return
                model_profile = str(body.get("model_profile", "anime_x4plus_6b")).strip() or "anime_x4plus_6b"
                try:
                    huggingface_realesrgan.profile(model_profile)
                except ValueError as error:
                    _json(self, {"error": str(error)}, 400)
                    return
                job_id = f"post_{uuid.uuid4().hex[:16]}"
                job = {
                    "id": job_id,
                    "status": "queued",
                    "created_at": utc_now(),
                    "payload": {
                        "gemini_job_id": gemini_job_id,
                        "fps": max(1.0, min(60.0, float(body.get("fps", 10.0)))),
                        "model_profile": model_profile,
                    },
                }
                with JOB_LOCK:
                    jobs = read_postprocess_jobs()
                    jobs.append(job)
                    write_json_atomic(POSTPROCESS_JOBS_PATH, jobs)
                threading.Thread(target=run_postprocess_job, args=(job,), daemon=True).start()
                _json(self, job, 202)
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
