"""Hugging Face model catalog and local weight resolution for Sprite Lab."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE = Path(__file__).resolve().parent
STATE = BASE / "state"
HF_CONFIG_PATH = STATE / "huggingface_config.json"
HF_CACHE_DIR = BASE / "work" / "huggingface-cache"


MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "anime_x4plus_6b": {
        "label": "Ilustração · RealESRGAN Anime 6B",
        "repo_id": "amd/realesrgan-x4plus-anime-6b",
        "filename": "RealESRGAN_x4plus_anime_6B.pth",
        "format": "pth",
        "architecture": "rrdb",
        "num_block": 6,
        "network_scale": 4,
        "source": "Hugging Face · AMD mirror of the official checkpoint",
    },
    "bicubic": {
        "label": "Conservador · Bicubic 2×",
        "architecture": "traditional",
        "network_scale": 2,
        "source": "OpenCV INTER_CUBIC · sem modelo neural",
    },
}


def profile(profile_id: str) -> dict[str, Any]:
    try:
        return MODEL_PROFILES[profile_id]
    except KeyError as error:
        available = ", ".join(sorted(MODEL_PROFILES))
        raise ValueError(f"perfil Real-ESRGAN desconhecido: {profile_id}; disponíveis: {available}") from error


def read_config() -> dict[str, Any]:
    if not HF_CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(HF_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def api_token() -> str:
    saved = str(read_config().get("api_key", "")).strip()
    return saved or os.environ.get("HF_TOKEN", "").strip() or os.environ.get("HUGGINGFACEHUB_API_TOKEN", "").strip()


def config_status() -> dict[str, Any]:
    saved = read_config()
    if str(saved.get("api_key", "")).strip():
        return {
            "configured": True,
            "source": "local",
            "updated_at": saved.get("updated_at"),
        }
    if os.environ.get("HF_TOKEN", "").strip() or os.environ.get("HUGGINGFACEHUB_API_TOKEN", "").strip():
        return {"configured": True, "source": "environment", "updated_at": None}
    return {"configured": False, "source": None, "updated_at": None}


def save_api_token(value: str) -> dict[str, Any]:
    token = str(value or "").strip()
    if len(token) > 500:
        raise ValueError("o token Hugging Face é muito longo")
    if token:
        payload = {
            "schema": "sprite_lab.huggingface_config/v1",
            "api_key": token,
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        HF_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = HF_CONFIG_PATH.with_name(f".{HF_CONFIG_PATH.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(HF_CONFIG_PATH)
            HF_CONFIG_PATH.chmod(0o600)
        finally:
            if temporary.exists():
                temporary.unlink()
    elif HF_CONFIG_PATH.exists():
        HF_CONFIG_PATH.unlink()
    return config_status()


def download_weight(profile_id: str) -> Path:
    selected = profile(profile_id)
    if selected["architecture"] == "traditional":
        raise ValueError(f"o perfil {profile_id} não possui pesos para baixar")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as error:
        raise RuntimeError("instale huggingface_hub para baixar modelos do Hugging Face") from error
    try:
        path = hf_hub_download(
            repo_id=str(selected["repo_id"]),
            filename=str(selected["filename"]),
            revision="main",
            cache_dir=HF_CACHE_DIR,
            token=api_token() or None,
        )
    except Exception as error:  # noqa: BLE001 - preserve the Hub error context.
        raise RuntimeError(
            f"não foi possível baixar {profile_id} de {selected['repo_id']}: {error}"
        ) from error
    return Path(path)
