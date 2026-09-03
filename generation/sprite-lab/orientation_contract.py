"""Stable character-facing calibration shared by Sprite Lab renderers."""

from __future__ import annotations

import math
from typing import Any


ORIENTATION_SCHEMA = "sprite_lab.orientation_contract/v1"
DEFAULT_LOCAL_FORWARD_AXIS = "-Y"
DEFAULT_REFERENCE_BONE = "root"
DEFAULT_YAW_OFFSET_DEGREES = 0.0
VALID_LOCAL_FORWARD_AXES = ("+X", "-X", "+Y", "-Y")


def axis_vector(axis: str) -> tuple[float, float, float]:
    """Return a horizontal unit vector for a local armature axis."""
    normalized = str(axis or DEFAULT_LOCAL_FORWARD_AXIS).strip().upper()
    if normalized not in VALID_LOCAL_FORWARD_AXES:
        raise ValueError(
            "local_forward_axis deve ser +X, -X, +Y ou -Y"
        )
    vectors = {
        "+X": (1.0, 0.0, 0.0),
        "-X": (-1.0, 0.0, 0.0),
        "+Y": (0.0, 1.0, 0.0),
        "-Y": (0.0, -1.0, 0.0),
    }
    return vectors[normalized]


def _finite_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} deve ser um número finito") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} deve ser um número finito")
    return result


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def normalize_orientation(value: Any = None) -> dict[str, Any]:
    """Normalize a rest-pose orientation manifest.

    The direction of an animation is intentionally absent from this contract.
    Animation/root-motion data controls temporal movement and frame placement;
    this manifest controls only the character's authored facing axis.
    """
    incoming = value if isinstance(value, dict) else {}
    source = str(incoming.get("source") or "rest_pose").strip().casefold()
    if source not in {"rest_pose", "explicit", "default"}:
        raise ValueError("orientation.source inválido")
    axis = str(
        incoming.get("local_forward_axis") or DEFAULT_LOCAL_FORWARD_AXIS
    ).strip().upper()
    axis_vector(axis)
    reference_bone = str(
        incoming.get("reference_bone") or DEFAULT_REFERENCE_BONE
    ).strip()
    if not reference_bone:
        raise ValueError("orientation.reference_bone é obrigatório")
    return {
        "schema": ORIENTATION_SCHEMA,
        "source": source,
        "reference_bone": reference_bone,
        "local_forward_axis": axis,
        "yaw_offset_degrees": _finite_number(
            incoming.get("yaw_offset_degrees", DEFAULT_YAW_OFFSET_DEGREES),
            "orientation.yaw_offset_degrees",
        ),
        "character_asset_id": _optional_text(incoming.get("character_asset_id")),
        "rest_pose_id": _optional_text(incoming.get("rest_pose_id")),
        "rest_pose_asset_id": _optional_text(
            incoming.get("rest_pose_asset_id")
        ),
        "rest_pose_action_name": _optional_text(
            incoming.get("rest_pose_action_name")
        ),
    }
