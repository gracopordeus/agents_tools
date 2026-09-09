"""Hugging Face model catalog and local weight resolution for Sprite Lab."""
from __future__ import annotations

import json
import hashlib
import io
import os
import tempfile
import urllib.request
import zipfile
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
        "revision": "b14ff5f8ecb5a4b56ce4049a58d0bca1f8814690",
    },
    "swinir_light_x2": {
        "label": "POC · SwinIR Lightweight 2×",
        "architecture": "swinir", "network_scale": 2,
        "local_file": "swinir_light_x2.pth",
        "url": "https://github.com/JingyunLiang/SwinIR/releases/download/v0.0/002_lightweightSR_DIV2K_s64w8_SwinIR-S_x2.pth",
        "sha256": "193b229909ca89cd8b55de9c9e7fce146ae759d59dfcd78d8feb9dd1d6fa0fd7",
        "source": "JingyunLiang/SwinIR · official v0.0 release",
    },
    "swinir_m_classical_df2k_x2": {
        "label": "POC · SwinIR-M ClassicalSR DF2K 2×",
        "architecture": "swinir", "network_scale": 2,
        "local_file": "swinir_m_classical_df2k_x2.pth",
        "url": "https://github.com/JingyunLiang/SwinIR/releases/download/v0.0/001_classicalSR_DF2K_s64w8_SwinIR-M_x2.pth",
        "sha256": "2032ebf8f401dd3ce2fae5f3852117cb72101ec6ed8358faa64c2a3fa09ed4ac",
        "source": "JingyunLiang/SwinIR · official v0.0 release",
    },
    "realcugan_x2": {
        "label": "POC · Real-CUGAN 2× sem denoise",
        "architecture": "realcugan", "network_scale": 2,
        "local_file": "updated_weights/up2x-latest-no-denoise.pth",
        "archive_member": "updated_weights/up2x-latest-no-denoise.pth",
        "url": "https://github.com/bilibili/ailab/releases/download/Real-CUGAN/updated_weights.zip",
        "sha256": "f491f9ecf6964ead9f3a36bf03e83527f32c6a341b683f7378ac6c1e2a5f0d16",
        "source": "bilibili/ailab · official Real-CUGAN release",
    },
    "realcugan_conservative_x2": {
        "label": "POC · Real-CUGAN 2× conservador",
        "architecture": "realcugan", "network_scale": 2,
        "local_file": "updated_weights/up2x-latest-conservative.pth",
        "archive_member": "updated_weights/up2x-latest-conservative.pth",
        "url": "https://github.com/bilibili/ailab/releases/download/Real-CUGAN/updated_weights.zip",
        "sha256": "6cfe3b23687915d08ba96010f25198d9cfe8a683aa4131f1acf7eaa58ee1de93",
        "source": "bilibili/ailab · official Real-CUGAN release",
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
    if "local_file" in selected:
        target = BASE / "work" / "upscale-models" / selected["local_file"]
        if not target.is_file():
            with urllib.request.urlopen(selected["url"], timeout=120) as response:
                data = response.read()
            if "archive_member" in selected:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    data = archive.read(selected["archive_member"])
            if hashlib.sha256(data).hexdigest() != selected["sha256"]:
                raise RuntimeError(f"Hash inválido para {profile_id}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
                temporary.write(data)
            os.replace(temporary.name, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != selected["sha256"]:
            raise RuntimeError(f"Checkpoint local alterado: {target}")
        return target
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as error:
        raise RuntimeError("instale huggingface_hub para baixar modelos do Hugging Face") from error
    try:
        path = hf_hub_download(
            repo_id=str(selected["repo_id"]),
            filename=str(selected["filename"]),
            revision=selected.get("revision", "main"),
            cache_dir=HF_CACHE_DIR,
            token=api_token() or None,
        )
    except Exception as error:  # noqa: BLE001 - preserve the Hub error context.
        raise RuntimeError(
            f"não foi possível baixar {profile_id} de {selected['repo_id']}: {error}"
        ) from error
    return Path(path)
