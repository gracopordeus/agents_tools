"""Provider-neutral image generation adapters for the generation PoC.

The adapters are deliberately lazy: importing this module never requires a
cloud SDK. A dry-run provider is included for contract tests and for producing
reviewable request manifests before spending API credits.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class GenerationRequest:
    """One single-frame generation request."""

    job_id: str
    prompt: str
    input_images: tuple[Path, ...]
    output_path: Path
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationResult:
    status: str
    provider: str
    model: str
    output_path: Path | None
    response_metadata: dict[str, Any] = field(default_factory=dict)


class ImageGenerationProvider(Protocol):
    name: str

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate one raster image and return its local output path."""


def _write_request(request: GenerationRequest, provider: str) -> None:
    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": request.job_id,
        "provider": provider,
        "model": request.model,
        "prompt": request.prompt,
        "input_images": [str(path) for path in request.input_images],
        "output_path": str(request.output_path),
        "metadata": request.metadata,
    }
    request.output_path.with_suffix(".request.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


class DryRunProvider:
    """Write request files without calling a remote service."""

    name = "dry-run"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        for image in request.input_images:
            if not image.is_file():
                raise FileNotFoundError(image)
        _write_request(request, self.name)
        return GenerationResult(
            status="dry-run",
            provider=self.name,
            model=request.model,
            output_path=None,
            response_metadata={"request": str(request.output_path.with_suffix(".request.json"))},
        )


class OpenAIImageProvider:
    """Adapter for the OpenAI Images API edit/reference workflow.

    The SDK is imported only when this provider is used. GPT Image 2 processes
    image inputs at high fidelity; the adapter intentionally does not expose an
    input-fidelity knob that the model does not support.
    """

    name = "openai"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY não está configurada")

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("instale o pacote openai para usar o provider openai") from exc
        if not request.input_images:
            raise ValueError("o provider openai exige ao menos uma imagem de referência")
        _write_request(request, self.name)
        client = OpenAI(api_key=self.api_key)
        handles = [path.open("rb") for path in request.input_images]
        try:
            image_argument: Any = handles[0] if len(handles) == 1 else handles
            response = client.images.edit(
                model=request.model,
                image=image_argument,
                prompt=request.prompt,
                background="transparent",
                output_format="png",
                input_fidelity="high",
            )
        finally:
            for handle in handles:
                handle.close()
        item = response.data[0]
        encoded = getattr(item, "b64_json", None)
        if not encoded:
            raise RuntimeError("o provider openai não retornou b64_json; URL não é aceita pelo runner local")
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(base64.b64decode(encoded))
        return GenerationResult(
            status="ok",
            provider=self.name,
            model=request.model,
            output_path=request.output_path,
            response_metadata={"request": str(request.output_path.with_suffix(".request.json"))},
        )


class GoogleImageProvider:
    """Optional adapter for Google's GenAI image-capable models.

    The model ID is supplied by the caller because product aliases such as
    Nano Banana can map to different API model IDs over time.
    """

    name = "google"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (
            api_key
            or os.environ.get("GOOGLE_API_KEY", "").strip()
            or os.environ.get("GEMINI_API_KEY", "").strip()
        )
        if not self.api_key:
            raise RuntimeError("GOOGLE_API_KEY ou GEMINI_API_KEY não está configurada")

    def generate(self, request: GenerationRequest) -> GenerationResult:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("instale google-genai para usar o provider google") from exc
        if not request.input_images:
            raise ValueError("o provider google exige ao menos uma imagem de referência")
        _write_request(request, self.name)
        client = genai.Client(api_key=self.api_key)
        contents: list[Any] = [request.prompt]
        for path in request.input_images:
            contents.append(types.Part.from_bytes(data=path.read_bytes(), mime_type=_mime_type(path)))
        response = client.models.generate_content(
            model=request.model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio="1:1",
                    image_size="2K",
                ),
            ),
        )
        for candidate in response.candidates or []:
            for part in candidate.content.parts if candidate.content else []:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                if data:
                    request.output_path.parent.mkdir(parents=True, exist_ok=True)
                    request.output_path.write_bytes(data if isinstance(data, bytes) else base64.b64decode(data))
                    return GenerationResult(
                        status="ok",
                        provider=self.name,
                        model=request.model,
                        output_path=request.output_path,
                        response_metadata={"request": str(request.output_path.with_suffix(".request.json"))},
                    )
        raise RuntimeError("o provider google não retornou uma parte de imagem")


def _mime_type(path: Path) -> str:
    suffix = path.suffix.casefold()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def create_provider(name: str) -> ImageGenerationProvider:
    """Create a provider without importing optional SDKs prematurely."""
    normalized = name.strip().casefold()
    if normalized in {"dry", "dry-run", "none"}:
        return DryRunProvider()
    if normalized in {"openai", "gpt-image", "gpt-image-2"}:
        return OpenAIImageProvider()
    if normalized in {"google", "gemini", "nano-banana"}:
        return GoogleImageProvider()
    raise ValueError(f"provider desconhecido: {name!r}")
