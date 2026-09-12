"""Validate and persist the resumable isolated-character generation stage."""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

from image_generation_provider import GenerationRequest, GenerationResult


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
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request_fingerprint(request: GenerationRequest) -> str:
    encoded = json.dumps(
        _jsonable(asdict(request)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_character_output(
    source: Path,
    validation_path: Path,
    expected_size: tuple[int, int],
) -> dict[str, Any]:
    if not source.is_file():
        raise ValueError("resposta sem arquivo de imagem")
    try:
        with Image.open(source) as opened:
            if opened.format != "PNG":
                raise ValueError("resposta deve estar em formato PNG")
            if opened.size != expected_size:
                raise ValueError(
                    f"dimensões inválidas: {opened.size}; esperado {expected_size}"
                )
            if "A" not in opened.getbands():
                raise ValueError("resposta PNG deve possuir canal alpha")
            image = opened.convert("RGBA")
            alpha_min, _alpha_max = image.getchannel("A").getextrema()
            if alpha_min != 0:
                image.close()
                raise ValueError("fundo deve possuir transparência real")
    except OSError as exc:
        raise ValueError(f"arquivo de imagem inválido: {exc}") from None

    width, height = image.size
    cell_width, cell_height = width // 8, height // 8
    pixels = image.load()
    violations = []
    tolerance = 4
    for row in range(8):
        for column in range(8):
            left, top = column * cell_width, row * cell_height
            right, bottom = left + cell_width - 1, top + cell_height - 1
            border = (
                (x, y)
                for x in range(left, right + 1)
                for y in range(top, bottom + 1)
                if x < left + tolerance or x > right - tolerance
                or y < top + tolerance or y > bottom - tolerance
            )
            if any(pixels[x, y][3] >= 24 for x, y in border):
                violations.append({"row": row, "column": column})
    if violations:
        image.close()
        raise ValueError(f"bleed detectado entre células: {len(violations)} violações")

    overlay = image.copy()
    draw = ImageDraw.Draw(overlay, "RGBA")
    for index in range(9):
        draw.line((index * cell_width, 0, index * cell_width, height - 1), fill=(220, 226, 234, 210), width=2)
        draw.line((0, index * cell_height, width - 1, index * cell_height), fill=(220, 226, 234, 210), width=2)
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(validation_path, format="PNG")
    image.close()
    overlay.close()
    return {"size": [width, height], "format": "PNG", "alpha": True, "bleed": False}


def run_character_stage(
    directory: Path,
    request: GenerationRequest,
    generate: Callable[[GenerationRequest], GenerationResult],
    update_state: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Generate once, validate, persist, and resume from a verified checkpoint."""
    directory.mkdir(parents=True, exist_ok=True)
    canonical = directory / "character_full.png"
    validation = directory / "character_validation.png"
    request_path = directory / "character.request.json"
    response_path = directory / "character_response.json"
    request_payload = _jsonable(asdict(request))
    request_fingerprint = _request_fingerprint(request)
    if response_path.is_file() and canonical.is_file():
        saved = json.loads(response_path.read_text())
        if (
            saved.get("status") == "character_complete"
            and saved.get("request_fingerprint") == request_fingerprint
            and saved.get("sha256") == _sha256(canonical)
        ):
            return {**saved, "resumed": True}

    _write_json(request_path, {
        **request_payload,
        "request_fingerprint": request_fingerprint,
    })
    provider_output = directory / ".character_full.provider.png"
    approved_output = directory / ".character_full.approved.png"
    provider_output.unlink(missing_ok=True)
    approved_output.unlink(missing_ok=True)
    provider_request = replace(request, output_path=provider_output)
    try:
        result = generate(provider_request)
        expected = request.metadata.get("output_size", [2048, 2048])
        expected_size = (int(expected[0]), int(expected[1]))
        output = result.output_path
        if output is None:
            raise ValueError("resposta sem arquivo de imagem")
        output = output.resolve()
        if output == canonical.resolve():
            output.replace(provider_output)
            output = provider_output.resolve()
        if update_state is not None:
            update_state({"stage": "validating_character", "percent": 30})
        validation_report = validate_character_output(output, validation, expected_size)
        shutil.copy2(output, approved_output)
        approved_output.replace(canonical)
        response = {
            "status": "character_complete",
            "generation_role": "character",
            "canonical_input": "character_full.png",
            "sha256": _sha256(canonical),
            "request_fingerprint": request_fingerprint,
            "bytes": canonical.stat().st_size,
            "validation": validation_report,
            "validation_overlay": {
                "path": "character_validation.png",
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
    except Exception as exc:
        validation.unlink(missing_ok=True)
        provider_output.unlink(missing_ok=True)
        approved_output.unlink(missing_ok=True)
        _write_json(response_path, {
            "status": "character_failed",
            "generation_role": "character",
            "error": str(exc),
            "resumable": True,
            "request_fingerprint": request_fingerprint,
        })
        raise
    finally:
        provider_output.unlink(missing_ok=True)
        approved_output.unlink(missing_ok=True)
