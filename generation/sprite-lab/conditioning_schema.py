"""Schema and validation helpers for the generation reference pack.

The project consumes renders from a 3D source and produces 2D raster outputs.
This module deliberately contains no provider-specific or engine-specific code;
the manifest is the stable boundary between Blender, image providers and the
post-processing stage.
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any


PACK_SCHEMA = "generation.reference_conditioning_pack/v1"
PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,95}$")
FRAME_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
CHANNELS = ("beauty", "silhouette", "segmentation", "depth", "skeleton")
REQUIRED_CHANNELS = ("beauty", "silhouette", "segmentation")
CONDITIONS = ("rgb", "silhouette", "segmentation", "depth", "skeleton")


class ConditioningSchemaError(ValueError):
    """Raised when a reference pack violates the PoC contract."""


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConditioningSchemaError(f"{field} deve ser texto não vazio")
    return value.strip()


def _positive_int_pair(value: Any, field: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ConditioningSchemaError(f"{field} deve conter dois inteiros")
    if any(isinstance(item, bool) for item in value):
        raise ConditioningSchemaError(f"{field} deve conter dois inteiros")
    try:
        result = [int(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ConditioningSchemaError(f"{field} deve conter dois inteiros") from exc
    if any(float(original) != converted for original, converted in zip(value, result)):
        raise ConditioningSchemaError(f"{field} deve conter dois inteiros")
    if any(item <= 0 for item in result):
        raise ConditioningSchemaError(f"{field} deve ser positivo")
    return result


def _coordinate_pair(value: Any, field: str) -> list[int]:
    """Validate a pixel coordinate pair, allowing the canvas edge."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ConditioningSchemaError(f"{field} deve conter dois inteiros")
    if any(isinstance(item, bool) for item in value):
        raise ConditioningSchemaError(f"{field} deve conter dois inteiros")
    result: list[int] = []
    for item in value:
        try:
            number = float(item)
            integer = int(item)
        except (TypeError, ValueError) as exc:
            raise ConditioningSchemaError(f"{field} deve conter dois inteiros") from exc
        if not math.isfinite(number) or number != integer or integer < 0:
            raise ConditioningSchemaError(
                f"{field} deve conter coordenadas inteiras não negativas"
            )
        result.append(integer)
    return result


def _finite_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConditioningSchemaError(f"{field} deve ser numérico") from exc
    if not math.isfinite(number):
        raise ConditioningSchemaError(f"{field} deve ser finito")
    return number


def _safe_relative_path(value: Any, field: str) -> str:
    path = _required_text(value, field)
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ConditioningSchemaError(f"{field} deve ser um caminho relativo seguro")
    return candidate.as_posix()


def _validate_frame(frame: Any, index: int, expected_channels: set[str]) -> dict[str, Any]:
    if not isinstance(frame, dict):
        raise ConditioningSchemaError(f"frames[{index}] deve ser um objeto")
    frame_id = _required_text(frame.get("id"), f"frames[{index}].id")
    if not FRAME_ID_RE.fullmatch(frame_id):
        raise ConditioningSchemaError(f"frames[{index}].id inválido: {frame_id!r}")
    frame_index = frame.get("index", index)
    if isinstance(frame_index, bool):
        raise ConditioningSchemaError(f"frames[{index}].index deve ser inteiro")
    try:
        frame_index = int(frame_index)
    except (TypeError, ValueError) as exc:
        raise ConditioningSchemaError(f"frames[{index}].index deve ser inteiro") from exc
    if frame_index != index or frame_index < 0:
        raise ConditioningSchemaError(
            f"frames[{index}].index deve ser sequencial e começar em zero"
        )
    channels = frame.get("channels")
    if not isinstance(channels, dict):
        raise ConditioningSchemaError(f"frames[{index}].channels deve ser objeto")
    normalized_channels: dict[str, str] = {}
    for channel, path in channels.items():
        if channel not in CHANNELS:
            raise ConditioningSchemaError(
                f"frames[{index}].channels possui canal desconhecido: {channel!r}"
            )
        normalized_channels[channel] = _safe_relative_path(
            path, f"frames[{index}].channels.{channel}"
        )
    missing = sorted(expected_channels - set(normalized_channels))
    if missing:
        raise ConditioningSchemaError(
            f"frames[{index}] não possui canais obrigatórios: {', '.join(missing)}"
        )
    result: dict[str, Any] = {"id": frame_id, "index": frame_index, "channels": normalized_channels}
    if "landmarks" in frame:
        if not isinstance(frame["landmarks"], dict):
            raise ConditioningSchemaError(f"frames[{index}].landmarks deve ser objeto")
        result["landmarks"] = copy.deepcopy(frame["landmarks"])
    return result


def validate_manifest(value: Any) -> dict[str, Any]:
    """Validate and normalize a reference pack manifest."""
    if not isinstance(value, dict):
        raise ConditioningSchemaError("manifest deve ser um objeto")
    if value.get("schema") != PACK_SCHEMA:
        raise ConditioningSchemaError(f"schema inválido: {value.get('schema')!r}")
    pack_id = _required_text(value.get("id"), "id")
    if not PACK_ID_RE.fullmatch(pack_id):
        raise ConditioningSchemaError("id deve usar lowercase, números, _ ou -")
    project = _required_text(value.get("project", "generation"), "project")
    if project != "generation":
        raise ConditioningSchemaError("project deve ser 'generation'")
    action = _required_text(value.get("action"), "action")
    direction = _required_text(value.get("direction"), "direction")
    cell_size = _positive_int_pair(value.get("cell_size"), "cell_size")
    if cell_size[0] != cell_size[1]:
        raise ConditioningSchemaError("cell_size deve ser quadrado")
    foot_anchor = _coordinate_pair(
        value.get("foot_anchor", [cell_size[0] // 2, round(cell_size[1] * 0.86)]),
        "foot_anchor",
    )
    if foot_anchor[0] > cell_size[0] or foot_anchor[1] > cell_size[1]:
        raise ConditioningSchemaError("foot_anchor deve estar dentro do canvas")
    frame_count = value.get("frame_count")
    if isinstance(frame_count, bool):
        raise ConditioningSchemaError("frame_count deve ser inteiro")
    try:
        frame_count = int(frame_count)
    except (TypeError, ValueError) as exc:
        raise ConditioningSchemaError("frame_count deve ser inteiro") from exc
    if not 1 <= frame_count <= 64:
        raise ConditioningSchemaError("frame_count deve ficar entre 1 e 64")
    fps = _finite_number(value.get("fps", 10.0), "fps")
    if fps <= 0:
        raise ConditioningSchemaError("fps deve ser maior que zero")

    frames = value.get("frames")
    if not isinstance(frames, list) or len(frames) != frame_count:
        raise ConditioningSchemaError("frames deve ter exatamente frame_count itens")
    channels_declared = value.get("channels")
    if not isinstance(channels_declared, list) or not channels_declared:
        raise ConditioningSchemaError("channels deve listar os canais disponíveis")
    channels = [_required_text(item, "channels[]") for item in channels_declared]
    if len(set(channels)) != len(channels) or any(item not in CHANNELS for item in channels):
        raise ConditioningSchemaError("channels contém canais inválidos ou duplicados")
    missing_required = sorted(set(REQUIRED_CHANNELS) - set(channels))
    if missing_required:
        raise ConditioningSchemaError(
            f"channels não possui: {', '.join(missing_required)}"
        )

    normalized_frames = [
        _validate_frame(frame, index, set(REQUIRED_CHANNELS))
        for index, frame in enumerate(frames)
    ]
    frame_ids = [frame["id"] for frame in normalized_frames]
    if len(set(frame_ids)) != len(frame_ids):
        raise ConditioningSchemaError("frames.id deve ser único")

    target = value.get("target_reference")
    if not isinstance(target, dict):
        raise ConditioningSchemaError("target_reference deve ser um objeto")
    target_path = _safe_relative_path(target.get("path"), "target_reference.path")
    target_role = _required_text(target.get("role", "identity"), "target_reference.role")

    prompt = value.get("prompt")
    if not isinstance(prompt, dict):
        raise ConditioningSchemaError("prompt deve ser um objeto")
    prompt_version = _required_text(prompt.get("version", "v1"), "prompt.version")
    prompt_template = _required_text(prompt.get("template"), "prompt.template")

    authority = value.get("authority")
    if not isinstance(authority, dict):
        raise ConditioningSchemaError("authority deve ser um objeto")
    identity = authority.get("identity", ["target_reference"])
    structure = authority.get("structure", list(channels))
    if not all(isinstance(group, list) and all(isinstance(item, str) for item in group) for group in (identity, structure)):
        raise ConditioningSchemaError("authority.identity/structure devem ser listas de texto")

    normalized = copy.deepcopy(value)
    normalized.update(
        {
            "schema": PACK_SCHEMA,
            "project": project,
            "id": pack_id,
            "action": action,
            "direction": direction,
            "cell_size": cell_size,
            "foot_anchor": foot_anchor,
            "frame_count": frame_count,
            "fps": fps,
            "channels": channels,
            "frames": normalized_frames,
            "target_reference": {"path": target_path, "role": target_role},
            "prompt": {"version": prompt_version, "template": prompt_template},
            "authority": {"identity": list(identity), "structure": list(structure)},
        }
    )
    return normalized


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and validate a manifest from JSON."""
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConditioningSchemaError(f"JSON inválido: {path}") from exc
    return validate_manifest(value)


def write_manifest(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    """Validate and atomically write a manifest."""
    normalized = validate_manifest(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return normalized
