"""Validate, persist and resume the isolated weapon generation stage."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from image_generation_provider import GenerationRequest, GenerationResult
from character_layer_persistence import validate_character_output


class WeaponPrerequisiteError(ValueError):
    """The character checkpoint is absent, incomplete or no longer trustworthy."""


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request_fingerprint(request: GenerationRequest) -> str:
    encoded = json.dumps(
        _jsonable(asdict(request)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_approved_character(directory: Path) -> tuple[Path, dict[str, Any]]:
    """Expose the character checkpoint preflight to the orchestration worker."""
    return _approved_character(directory)


def record_weapon_blocked(
    directory: Path,
    error: str,
    *,
    request_fingerprint: str = "",
) -> dict[str, Any]:
    """Persist a durable blocked checkpoint before a weapon request is built."""
    directory.mkdir(parents=True, exist_ok=True)
    response = {
        "status": "weapon_blocked",
        "generation_role": "weapon",
        "error": str(error),
        "resumable": True,
    }
    if request_fingerprint:
        response["request_fingerprint"] = request_fingerprint
    _write_json(directory / "weapon_response.json", response)
    return response


def record_weapon_failed(
    directory: Path,
    error: str,
    *,
    request_fingerprint: str = "",
) -> dict[str, Any]:
    """Persist a durable failed checkpoint for request/preflight errors."""
    directory.mkdir(parents=True, exist_ok=True)
    response = {
        "status": "weapon_failed",
        "generation_role": "weapon",
        "error": str(error),
        "resumable": True,
    }
    if request_fingerprint:
        response["request_fingerprint"] = request_fingerprint
    _write_json(directory / "weapon_response.json", response)
    return response


def _approved_character(directory: Path) -> tuple[Path, dict[str, Any]]:
    canonical = directory / "character_full.png"
    response_path = directory / "character_response.json"
    if not response_path.is_file():
        raise WeaponPrerequisiteError("character_full aprovado ausente: character_response.json")
    try:
        response = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WeaponPrerequisiteError("character_full aprovado possui response inválido") from exc
    if response.get("status") not in {"character_complete", "character_completed"}:
        raise WeaponPrerequisiteError("character_full não está aprovado")
    if not canonical.is_file():
        raise WeaponPrerequisiteError("character_full aprovado ausente: character_full.png")
    expected = str(response.get("sha256") or "")
    actual = _sha256(canonical)
    if not expected or expected != actual:
        raise WeaponPrerequisiteError("hash do character_full aprovado não corresponde ao checkpoint")
    return canonical, response


def _character_contamination(source: Path, character: Path) -> dict[str, int | float]:
    """Detect a copied/detectable character envelope in a weapon response.

    Exact semantic segmentation is provider-specific. The persisted character
    is therefore used as a conservative envelope check: a large overlap of
    foreground alpha (with the same pixels or silhouette) means the provider
    returned the character pass instead of an isolated weapon.
    """
    try:
        with Image.open(source) as weapon_opened, Image.open(character) as character_opened:
            weapon = weapon_opened.convert("RGBA")
            reference = character_opened.convert("RGBA")
            if weapon.size != reference.size:
                weapon.close()
                reference.close()
                return {"weapon_foreground": 0, "character_foreground": 0, "overlap": 0, "same_pixels": 0}
            weapon_pixels = weapon.load()
            character_pixels = reference.load()
            weapon_foreground = 0
            character_foreground = 0
            overlap = 0
            same_pixels = 0
            for y in range(weapon.height):
                for x in range(weapon.width):
                    wp = weapon_pixels[x, y]
                    cp = character_pixels[x, y]
                    w_alpha = wp[3] >= 24
                    c_alpha = cp[3] >= 24
                    weapon_foreground += int(w_alpha)
                    character_foreground += int(c_alpha)
                    if w_alpha and c_alpha:
                        overlap += 1
                        same_pixels += int(wp == cp)
            weapon.close()
            reference.close()
    except OSError:
        return {"weapon_foreground": 0, "character_foreground": 0, "overlap": 0, "same_pixels": 0}
    return {
        "weapon_foreground": weapon_foreground,
        "character_foreground": character_foreground,
        "overlap": overlap,
        "same_pixels": same_pixels,
    }


def validate_weapon_output(
    source: Path,
    validation_path: Path,
    expected_size: tuple[int, int],
    character_path: Path,
) -> dict[str, Any]:
    report = validate_character_output(source, validation_path, expected_size)
    contamination = _character_contamination(source, character_path)
    weapon_foreground = int(contamination["weapon_foreground"])
    character_foreground = int(contamination["character_foreground"])
    overlap = int(contamination["overlap"])
    same_pixels = int(contamination["same_pixels"])
    if weapon_foreground and character_foreground:
        overlap_ratio = overlap / weapon_foreground
        same_ratio = same_pixels / overlap if overlap else 0.0
        character_ratio = overlap / character_foreground
        if (
            (overlap_ratio >= 0.85 and character_ratio >= 0.85)
            or (same_ratio >= 0.90 and overlap_ratio >= 0.50)
        ):
            validation_path.unlink(missing_ok=True)
            raise ValueError(
                "personagem detectável no envelope da camada da arma: "
                f"overlap={overlap_ratio:.3f}, same_pixels={same_ratio:.3f}"
            )
    report["character_envelope"] = {
        **contamination,
        "overlap_ratio": overlap / weapon_foreground if weapon_foreground else 0.0,
        "same_pixel_ratio": same_pixels / overlap if overlap else 0.0,
    }
    return report


def run_weapon_stage(
    directory: Path,
    request: GenerationRequest,
    generate: Callable[[GenerationRequest], GenerationResult],
    update_state: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run only the weapon pass after a verified character checkpoint."""
    directory.mkdir(parents=True, exist_ok=True)
    canonical = directory / "weapon_full.png"
    validation = directory / "weapon_validation.png"
    request_path = directory / "weapon.request.json"
    response_path = directory / "weapon_response.json"
    fingerprint = _request_fingerprint(request)
    request_payload = _jsonable(asdict(request))
    try:
        character, character_response = require_approved_character(directory)
    except WeaponPrerequisiteError as exc:
        record_weapon_blocked(
            directory, str(exc), request_fingerprint=fingerprint
        )
        raise
    dependency = {
        "path": "character_full.png",
        "sha256": _sha256(character),
        "request_fingerprint": character_response.get("request_fingerprint"),
    }

    if response_path.is_file() and canonical.is_file():
        try:
            saved = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
        if (
            saved.get("status") == "weapon_complete"
            and saved.get("request_fingerprint") == fingerprint
            and saved.get("sha256") == _sha256(canonical)
            and saved.get("character_dependency", {}).get("sha256") == dependency["sha256"]
        ):
            return {**saved, "resumed": True}

    _write_json(request_path, {**request_payload, "request_fingerprint": fingerprint, "character_dependency": dependency})
    provider_output = directory / ".weapon_full.provider.png"
    approved_output = directory / ".weapon_full.approved.png"
    provider_output.unlink(missing_ok=True)
    approved_output.unlink(missing_ok=True)
    provider_request = replace(request, output_path=provider_output)
    try:
        result = generate(provider_request)
        output = result.output_path
        if output is None:
            raise ValueError("resposta da arma sem arquivo de imagem")
        output = output.resolve()
        if output == canonical.resolve():
            output.replace(provider_output)
            output = provider_output.resolve()
        expected = request.metadata.get("output_size", [2048, 2048])
        expected_size = (int(expected[0]), int(expected[1]))
        if update_state is not None:
            update_state({"stage": "validating_weapon", "percent": 80})
        validation_report = validate_weapon_output(output, validation, expected_size, character)
        shutil.copy2(output, approved_output)
        approved_output.replace(canonical)
        response = {
            "status": "weapon_complete",
            "generation_role": "weapon",
            "canonical_input": "weapon_full.png",
            "sha256": _sha256(canonical),
            "request_fingerprint": fingerprint,
            "character_dependency": dependency,
            "bytes": canonical.stat().st_size,
            "validation": validation_report,
            "validation_overlay": {
                "path": "weapon_validation.png",
                "presentation_only": True,
                "allowed_consumers": ["ai_render_preview", "ai_render_history"],
            },
            "provider": result.provider,
            "model": result.model,
            "response_metadata": _jsonable(result.response_metadata),
            "resumed": False,
        }
        _write_json(response_path, response)
        return response
    except WeaponPrerequisiteError:
        raise
    except Exception as exc:
        validation.unlink(missing_ok=True)
        provider_output.unlink(missing_ok=True)
        approved_output.unlink(missing_ok=True)
        _write_json(response_path, {
            "status": "weapon_failed",
            "generation_role": "weapon",
            "error": str(exc),
            "resumable": True,
            "request_fingerprint": fingerprint,
            "character_dependency": dependency,
        })
        raise
    finally:
        provider_output.unlink(missing_ok=True)
        approved_output.unlink(missing_ok=True)
