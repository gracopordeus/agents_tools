#!/usr/bin/env python3
"""Structured observability for Sprite Lab.

Single place for the SaaS-facing telemetry contract:

- ``FRONTEND_EVENTS``: taxonomy of navigation/interaction events the web UI
  may send to ``POST /api/events`` (see web/app.js ``emitLabEvent``).
- ``BACKEND_EVENTS``: server-side lifecycle events emitted to stderr.
- JSON-lines logging to stderr (never stdout: stdout is reserved for the
  ``server.py`` boot banner), request IDs, an in-memory ring buffer plus
  counters exposed via ``GET /api/health``.

Environment overrides (all optional, used by tests and SaaS deploys):

- ``SPRITE_LAB_EVENTS_PATH``: JSONL file ingested frontend events are
  appended to. Defaults to ``state/frontend_events.jsonl`` next to this file.
- ``SPRITE_LAB_EVENTS_MAX_BYTES``: rotation threshold for the JSONL file.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DEFAULT_EVENTS_PATH = BASE / "state" / "frontend_events.jsonl"

SERVER_STARTED_AT = time.monotonic()


def _events_path() -> Path:
    configured = os.environ.get("SPRITE_LAB_EVENTS_PATH", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_EVENTS_PATH


def _events_max_bytes() -> int:
    try:
        return max(64 * 1024, int(os.environ.get("SPRITE_LAB_EVENTS_MAX_BYTES", "") or 5_242_880))
    except (TypeError, ValueError):
        return 5_242_880


# Maps ``web/app.js`` navigation and interaction points to the HTTP call each
# one triggers. Kept here (instead of only in chat/docs) so the frontend
# beacon and the backend validator share one contract.
FRONTEND_EVENTS: dict[str, str] = {
    # Navigation (app.js switchPage / PAGE_ROUTES).
    "page_view": "troca de página SPA (catalog/composition/sprites/env-atlas/gemini/postprocess)",
    # Catalog + 3D viewer (renderDetail/renderViewport/viewerConfig -> GET /assets/{id}/model).
    "viewer_model_loaded": "GLB canônico carregado no viewer principal",
    "viewer_model_error": "falha ao carregar o GLB no viewer principal",
    # Catalog inbox upload (POST /api/catalog/upload -> GET /api/catalog/uploads).
    "catalog_upload_started": "upload de ZIP iniciado",
    "catalog_upload_done": "upload de ZIP concluído (201)",
    "catalog_upload_error": "upload de ZIP falhou",
    # Semantic enrichment + bug reports.
    "annotation_saved": "POST /api/annotate bem-sucedido",
    "bug_reported": "POST /api/bug-reports bem-sucedido",
    # Compositions (POST /api/relationships[/delete]).
    "composition_saved": "composição salva",
    "composition_deleted": "composição removida",
    # Render jobs (POST /api/sprite-render|tile-render|prop-render|vfx-render, poll GET).
    "render_job_enqueued": "job de render enfileirado (202)",
    "render_job_done": "job de render concluído",
    "render_job_error": "job de render falhou",
    # AI render (POST /api/gemini-render, POST /api/ai-render-spec/compile).
    "ai_render_enqueued": "job de AI render enfileirado (202)",
    "ai_render_done": "job de AI render concluído",
    "ai_render_error": "job de AI render falhou",
    # Post-processing (POST /api/postprocess).
    "postprocess_enqueued": "job de pós-processamento enfileirado (202)",
    "postprocess_done": "job de pós-processamento concluído",
    "postprocess_error": "job de pós-processamento falhou",
    # Environment atlas (POST /api/env-atlas, sync ou async).
    "env_atlas_done": "atlas de ambiente concluído",
    "env_atlas_error": "atlas de ambiente falhou",
    # Maintenance queue (POST /api/reindex, /api/relationships[/delete] async).
    "reindex_done": "reindexação do catálogo concluída",
    "reindex_error": "reindexação do catálogo falhou",
}

BACKEND_EVENTS: dict[str, str] = {
    "http_access": "cada resposta HTTP (método, path, status, duração)",
    "request_completed": "alias legado de http_access; não emitido separadamente",
    "model_convert_started": "conversão FBX/glTF->GLB iniciada (model_cache)",
    "model_convert_done": "conversão concluída, com duração e bytes",
    "model_convert_error": "conversão falhou, com último log do Blender",
    "model_cache_hit": "GLB servido do cache sem Blender",
    "catalog_upload_received": "ZIP recebido no inbox do catálogo",
    "env_atlas_enqueued": "job de env-atlas assíncrono enfileirado (202)",
    "env_atlas_finished": "job de env-atlas assíncrono terminou (done/error)",
    "frontend_event": "evento de navegação ingerido via POST /api/events",
    "events_rate_limited": "ingestão recusada por rate-limit (429)",
}

# Sliding-window rate limiter for POST /api/events (SaaS abuse protection).
# The catalog UI emits a handful of events per interaction; sustained floods
# are bots or bugs. Tune via SPRITE_LAB_EVENTS_BURST (default 30/min/IP).
_EVENTS_WINDOW_S = 60.0
_rl_lock = threading.Lock()
_rl_hits: dict[str, list[float]] = {}

MAX_EVENT_NAME_LENGTH = 64
MAX_EVENT_PROPS_BYTES = 8 * 1024
MAX_INGEST_BODY_BYTES = 64 * 1024
_RING_CAPACITY = 500

_lock = threading.Lock()
_counters: dict[str, int] = {}
_ring: list[dict] = []


def new_request_id() -> str:
    """Short unique ID propagated via the ``X-Request-Id`` header."""
    return uuid.uuid4().hex[:16]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _record(name: str, fields: dict) -> dict:
    entry = {"ts": utc_now(), "event": name, **fields}
    with _lock:
        _counters[name] = _counters.get(name, 0) + 1
        _ring.append(entry)
        if len(_ring) > _RING_CAPACITY:
            del _ring[: len(_ring) - _RING_CAPACITY]
    return entry


def log_event(name: str, **fields) -> dict:
    """Emit a backend event as one JSON line on stderr and record it."""
    entry = _record(name, fields)
    try:
        sys.stderr.write(json.dumps(entry, ensure_ascii=False) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    return entry


def event_counts() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def recent_events(limit: int = 50) -> list[dict]:
    with _lock:
        count = max(1, min(int(limit), _RING_CAPACITY))
        return list(_ring[-count:])


def reset_state() -> None:
    """Clear in-memory counters/ring. Used by unit tests."""
    with _lock:
        _counters.clear()
        _ring.clear()


def events_burst_limit() -> int:
    try:
        return max(1, int(os.environ.get("SPRITE_LAB_EVENTS_BURST", "") or 30))
    except (TypeError, ValueError):
        return 30


def check_events_rate_limit(client_ip: str, *, now: float | None = None) -> bool:
    """Sliding-window gate: at most ``burst`` ingests per IP per minute.

    Returns True when the event may be ingested. Called by the HTTP layer
    *before* parsing the body so floods are cheap to refuse.
    """
    moment = time.monotonic() if now is None else now
    cutoff = moment - _EVENTS_WINDOW_S
    with _rl_lock:
        hits = [item for item in _rl_hits.get(client_ip, []) if item > cutoff]
        if len(hits) >= events_burst_limit():
            _rl_hits[client_ip] = hits
            return False
        hits.append(moment)
        _rl_hits[client_ip] = hits
        if len(_rl_hits) > 4096:  # bound memory under IP spoofing floods
            oldest = sorted(_rl_hits, key=lambda ip: _rl_hits[ip][-1] if _rl_hits[ip] else 0)
            for ip in oldest[: len(oldest) - 4096]:
                _rl_hits.pop(ip, None)
    return True


def reset_rate_limits() -> None:
    """Clear rate-limit state. Used by unit tests."""
    with _rl_lock:
        _rl_hits.clear()


def validate_frontend_event(body) -> tuple[str, dict]:
    """Validate an ingested navigation event.

    Returns ``(name, props)``. Raises ``ValueError`` on any contract breach
    so the HTTP layer can answer 400 without persisting garbage.
    """
    if not isinstance(body, dict):
        raise ValueError("evento deve ser um objeto JSON")
    name = str(body.get("name", "")).strip()
    if not name or len(name) > MAX_EVENT_NAME_LENGTH:
        raise ValueError("nome de evento inválido")
    if name not in FRONTEND_EVENTS:
        raise ValueError(f"evento desconhecido: {name}")
    props = body.get("props", {})
    if props is None:
        props = {}
    if not isinstance(props, dict):
        raise ValueError("props deve ser um objeto")
    encoded = json.dumps(props, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_EVENT_PROPS_BYTES:
        raise ValueError("props acima de 8 KB")
    return name, props


def ingest_frontend_event(name: str, props: dict, *, request_id: str | None = None) -> dict:
    """Persist one validated frontend event (JSONL) and record it in memory."""
    entry = {
        "ts": utc_now(),
        "event": name,
        "props": props,
        "request_id": request_id,
    }
    path = _events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    with _lock:
        try:
            if path.is_file() and path.stat().st_size + len(line.encode("utf-8")) > _events_max_bytes():
                rotated = path.with_suffix(path.suffix + ".1")
                if rotated.exists():
                    rotated.unlink()
                path.rename(rotated)
        except OSError:
            pass
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass
        _counters["frontend_event"] = _counters.get("frontend_event", 0) + 1
        _ring.append({"ts": entry["ts"], "event": "frontend_event", "name": name})
        if len(_ring) > _RING_CAPACITY:
            del _ring[: len(_ring) - _RING_CAPACITY]
    return entry


def health_snapshot(extra: dict | None = None) -> dict:
    """Side-effect-free payload for ``GET /api/health`` (load-balancer safe)."""
    snapshot = {
        "status": "ok",
        "service": "sprite-lab",
        "uptime_s": round(time.monotonic() - SERVER_STARTED_AT, 3),
        "time": utc_now(),
        "event_counts": event_counts(),
    }
    if extra:
        snapshot.update(extra)
    return snapshot
