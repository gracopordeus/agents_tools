"""Provider-neutral image generation adapters for the generation PoC.

The adapters are deliberately lazy: importing this module never requires a
cloud SDK. A dry-run provider is included for contract tests and for producing
reviewable request manifests before spending API credits.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


DEFAULT_MODELS = {
    "google": "gemini-3.1-flash-image",
    "openai": "gpt-image-2",
    "qwen": "qwen-image-3.0-pro",
}

OPENAI_MASK_COLOR = "#000000"
OPENAI_MASK_GUARD_PX = 8
OPENAI_MASK_GRID_COUNT = 8
GEMINI_TEMPERATURE = 1.0
GEMINI_TOP_K = 64

_PROVIDER_ALIASES = {
    "dry": "dry-run",
    "dry-run": "dry-run",
    "none": "dry-run",
    "openai": "openai",
    "gpt-image": "openai",
    "gpt-image-2": "openai",
    "google": "google",
    "gemini": "google",
    "nano-banana": "google",
    "qwen": "qwen",
    "qwen-image": "qwen",
    "qwen-image-edit": "qwen",
}


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


def _write_request(
    request: GenerationRequest,
    provider: str,
    *,
    input_images: tuple[Path, ...] | None = None,
    mask_path: Path | None = None,
) -> None:
    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": request.job_id,
        "provider": provider,
        "model": request.model,
        "prompt": request.prompt,
        "input_images": [str(path) for path in (input_images or request.input_images)],
        "output_path": str(request.output_path),
        "metadata": request.metadata,
    }
    if mask_path is not None:
        payload["mask"] = {
            "path": str(mask_path),
            "color": OPENAI_MASK_COLOR,
            "mode": "cell_boundary_guard",
            "guard_px": OPENAI_MASK_GUARD_PX,
        }
    request.output_path.with_suffix(".request.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _create_openai_cell_mask(reference_path: Path, mask_path: Path) -> None:
    """Create a black RGBA guard mask for the 8x8 cell boundaries.

    OpenAI applies an image mask to the first input image. Opaque black bands
    protect the edges of every logical cell, while transparent interiors leave
    room for the model to render the subject. The mask is kept at the exact
    dimensions of that first input as required by the Images API.
    """
    from PIL import Image, ImageDraw

    with Image.open(reference_path) as source:
        width, height = source.size
    if width <= 0 or height <= 0:
        raise ValueError("a imagem-base da máscara precisa ter dimensões válidas")

    mask = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    draw = ImageDraw.Draw(mask)
    cell_width = width / OPENAI_MASK_GRID_COUNT
    cell_height = height / OPENAI_MASK_GRID_COUNT
    guard_x = max(1, round(width * OPENAI_MASK_GUARD_PX / 2048))
    guard_y = max(1, round(height * OPENAI_MASK_GUARD_PX / 2048))
    for row in range(OPENAI_MASK_GRID_COUNT):
        for column in range(OPENAI_MASK_GRID_COUNT):
            left = round(column * cell_width)
            right = round((column + 1) * cell_width) - 1
            top = round(row * cell_height)
            bottom = round((row + 1) * cell_height) - 1
            inner_left = left + guard_x
            inner_right = right - guard_x
            inner_top = top + guard_y
            inner_bottom = bottom - guard_y
            if inner_left <= inner_right and inner_top <= inner_bottom:
                # RGB stays #000000 even in transparent pixels, which keeps
                # the generated mask unambiguous when inspected as RGB.
                draw.rectangle(
                    (inner_left, inner_top, inner_right, inner_bottom),
                    fill=(0, 0, 0, 0),
                )
    mask_path.parent.mkdir(parents=True, exist_ok=True)
    mask.save(mask_path, format="PNG")
    mask.close()


def _prepare_openai_base_image(reference_path: Path, output_path: Path) -> Path:
    """Return a PNG base image so it matches the RGBA mask format."""
    if reference_path.suffix.casefold() == ".png":
        return reference_path
    from PIL import Image

    with Image.open(reference_path) as source:
        source.convert("RGBA").save(output_path, format="PNG")
    return output_path


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

    The SDK is imported only when this provider is used. GPT Image 2 does not
    support the legacy ``input_fidelity`` parameter, so it is not sent here.
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
        base_image_path = _prepare_openai_base_image(
            request.input_images[0],
            request.output_path.with_name("openai_base_image.png"),
        )
        input_images = (base_image_path, *request.input_images[1:])
        _write_request(
            request,
            self.name,
            input_images=input_images,
        )
        client = OpenAI(api_key=self.api_key)
        handles = [path.open("rb") for path in input_images]
        try:
            image_argument: Any = handles[0] if len(handles) == 1 else handles
            response = client.images.edit(
                model=request.model,
                image=image_argument,
                prompt=request.prompt,
                # Preserve the successful first-run behavior: empty areas must
                # remain transparent for direct use as a game sprite asset.
                background="transparent",
                output_format="png",
                size="2048x2048",
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
            response_metadata={
                "request": str(request.output_path.with_suffix(".request.json")),
                "base_image": str(base_image_path),
                "request_id": getattr(response, "_request_id", None)
                or _object_field(response, "request_id"),
                "usage": _jsonable(_object_field(response, "usage")),
                "cost": _jsonable(_object_field(response, "cost")),
                "seed": _object_field(response, "seed"),
            },
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
                temperature=float(request.metadata.get("gemini_temperature", GEMINI_TEMPERATURE)),
                top_k=int(request.metadata.get("gemini_top_k", GEMINI_TOP_K)),
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


class QwenImageProvider:
    """QwenCloud API adapter for multi-reference image editing."""

    name = "qwen"

    def __init__(self, api_key: str | None = None) -> None:
        raw_api_key = (
            api_key
            or os.environ.get("DASHSCOPE_API_KEY", "").strip()
            or os.environ.get("QWEN_API_KEY", "").strip()
        )
        self.api_key = "".join(raw_api_key.split())
        if not self.api_key:
            raise RuntimeError("DASHSCOPE_API_KEY ou QWEN_API_KEY não está configurada")

    def _input_paths(self, request: GenerationRequest) -> tuple[list[Path], Path | None]:
        paths = list(request.input_images)
        if len(paths) > 3:
            raise ValueError(
                "o provider qwen aceita no máximo três imagens; "
                "reduza as referências do Blender selecionadas"
            )
        return paths, None

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not request.input_images:
            raise ValueError("o provider qwen exige ao menos uma imagem de referência")
        for path in request.input_images:
            if not path.is_file():
                raise FileNotFoundError(path)

        input_paths, guide_path = self._input_paths(request)
        if len(input_paths) > 3:
            raise ValueError("o provider qwen aceita no máximo três imagens por chamada")
        _write_request(request, self.name)

        try:
            import dashscope
            from dashscope import MultiModalConversation
        except ImportError as exc:
            raise RuntimeError("instale dashscope para usar o provider qwen") from exc

        messages = [{
            "role": "user",
            "content": [
                *({"image": _image_data_url(path)} for path in input_paths),
                {"text": request.prompt},
            ],
        }]
        output_size = request.metadata.get("output_size", [2048, 2048])
        if (
            not isinstance(output_size, (list, tuple))
            or len(output_size) != 2
            or any(int(value) <= 0 for value in output_size)
        ):
            raise ValueError("output_size inválido para o provider qwen")
        size = f"{int(output_size[0])}*{int(output_size[1])}"
        negative_prompt = str(
            request.metadata.get(
                "qwen_negative_prompt",
                os.environ.get("QWEN_NEGATIVE_PROMPT", " "),
            )
        )[:500]
        seed = request.metadata.get("qwen_seed")
        if seed is None and os.environ.get("QWEN_SEED", "").strip():
            seed = int(os.environ["QWEN_SEED"])
        call_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "model": request.model,
            "messages": messages,
            "stream": False,
            "n": 1,
            "watermark": False,
            "negative_prompt": negative_prompt,
            # Do not rewrite the detailed grid/direction contract.
            "prompt_extend": False,
            "size": size,
        }
        if seed is not None:
            call_kwargs["seed"] = int(seed)
        dashscope.base_http_api_url = _qwen_api_base_url(self.api_key)
        response = MultiModalConversation.call(**call_kwargs)
        status_code = int(getattr(response, "status_code", 200) or 200)
        if status_code != 200:
            code = str(getattr(response, "code", "unknown"))
            message = str(getattr(response, "message", "erro desconhecido"))
            raise RuntimeError(f"Qwen Cloud retornou {code}: {message}")
        image_url = _qwen_image_url(response)
        if not image_url:
            raise RuntimeError("o provider qwen não retornou uma URL de imagem")
        try:
            image_bytes = _download_image(image_url)
        except (OSError, urllib.error.URLError) as exc:
            raise RuntimeError("não foi possível baixar o output temporário do Qwen Cloud") from exc
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(image_bytes)
        response_path = request.output_path.with_suffix(".response.json")
        response_path.write_text(
            json.dumps(_jsonable(response), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        usage = _object_field(response, "usage")

        return GenerationResult(
            status="ok",
            provider=self.name,
            model=request.model,
            output_path=request.output_path,
            response_metadata={
                "request": str(request.output_path.with_suffix(".request.json")),
                "response": str(response_path),
                "request_id": _object_field(response, "request_id"),
                "usage": _jsonable(usage),
                "cost": _jsonable(_object_field(response, "cost")),
                "seed": _object_field(response, "seed")
                if _object_field(response, "seed") is not None
                else seed,
                "input_image_count": len(input_paths),
                "original_input_image_count": len(request.input_images),
                "structural_guide": str(guide_path) if guide_path else None,
                "size": size,
                "prompt_extend": False,
                "watermark": False,
            },
        )


def _image_data_url(path: Path) -> str:
    data = path.read_bytes()
    if len(data) > 10 * 1024 * 1024:
        raise ValueError(f"a imagem {path.name} excede o limite de 10 MB do Qwen Cloud")
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{_mime_type(path)};base64,{encoded}"


def _object_field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _qwen_image_url(response: Any) -> str | None:
    output = _object_field(response, "output")
    choices = _object_field(output, "choices", []) or []
    for choice in choices:
        message = _object_field(choice, "message")
        content = _object_field(message, "content", []) or []
        for item in content:
            image_url = _object_field(item, "image")
            if image_url:
                return str(image_url)
    return None


def _download_image(url: str) -> bytes:
    if not url.startswith(("https://", "http://")):
        raise ValueError("URL de imagem Qwen inválida")
    request = urllib.request.Request(url, headers={"User-Agent": "SpriteLab/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def _qwen_api_base_url(api_key: str) -> str:
    """Select the endpoint that matches the Qwen credential type."""
    explicit = (
        os.environ.get("QWEN_API_BASE_URL", "").strip()
        or os.environ.get("DASHSCOPE_HTTP_BASE_URL", "").strip()
    )
    if explicit:
        return explicit.rstrip("/")

    plan = os.environ.get("QWEN_API_PLAN", "").strip().casefold()
    if plan in {"coding", "coding-plan"}:
        return "https://coding-intl.dashscope.aliyuncs.com/api/v1"
    if plan in {"token", "token-plan"} or api_key.startswith("sk-sp-"):
        return "https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1"
    return "https://dashscope-intl.aliyuncs.com/api/v1"


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        return _jsonable(attributes)
    return str(value)


def _mime_type(path: Path) -> str:
    suffix = path.suffix.casefold()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def normalize_provider(name: str) -> str:
    normalized = str(name or "").strip().casefold()
    try:
        return _PROVIDER_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"provider desconhecido: {name!r}") from exc


def default_model(provider: str) -> str:
    normalized = normalize_provider(provider)
    if normalized == "dry-run":
        return "dry-run"
    return DEFAULT_MODELS[normalized]


def create_provider(name: str) -> ImageGenerationProvider:
    """Create a provider without importing optional SDKs prematurely."""
    normalized = normalize_provider(name)
    if normalized == "dry-run":
        return DryRunProvider()
    if normalized == "openai":
        return OpenAIImageProvider()
    if normalized == "google":
        return GoogleImageProvider()
    if normalized == "qwen":
        return QwenImageProvider()
    raise AssertionError(f"provider sem implementação: {normalized}")
