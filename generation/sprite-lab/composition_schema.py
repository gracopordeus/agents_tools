"""Normalize and validate reusable Sprite Lab composition components."""
from __future__ import annotations

import math
import re
from typing import Any


COMPONENT_SCHEMA = "sprite_lab.composition_components/v1"
COMPONENT_ROLES = {"attachment", "prop", "shield", "weapon"}
ROOT_PARENTS = {"character", "scene"}
MAX_COMPONENTS = 64
TWO_HAND_AXES = {"x", "y", "z", "-x", "-y", "-z"}
_COMPONENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} deve conter números finitos") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} deve conter números finitos")
    return result


def _vector(value: Any, field: str, default: list[float]) -> list[float]:
    if value is None:
        value = default
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{field} deve ser um vetor com 3 números")
    return [_number(item, field) for item in value]


def normalize_transform(value: Any) -> dict[str, list[float]]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("transform deve ser um objeto")
    scale = _vector(value.get("scale"), "transform.scale", [1.0, 1.0, 1.0])
    if any(item <= 0.0 for item in scale):
        raise ValueError("transform.scale deve conter valores maiores que zero")
    return {
        "position": _vector(value.get("position"), "transform.position", [0.0, 0.0, 0.0]),
        "rotation": _vector(value.get("rotation"), "transform.rotation", [0.0, 0.0, 0.0]),
        "scale": scale,
    }


def normalize_fit(value: Any) -> dict[str, Any]:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("fit deve ser um objeto")
    mode = str(value.get("mode", "none")).strip().casefold()
    if mode not in {"none", "character_height"}:
        raise ValueError(f"fit.mode inválido: {mode}")
    ratio = _number(value.get("ratio", 1.0), "fit.ratio")
    if ratio <= 0.0:
        raise ValueError("fit.ratio deve ser maior que zero")
    return {"mode": mode, "ratio": ratio}


def normalize_component(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"components[{index}] deve ser um objeto")
    asset_id = str(value.get("asset_id") or "").strip()
    if not asset_id:
        raise ValueError(f"components[{index}].asset_id é obrigatório")
    component_id = str(value.get("id") or f"component_{index + 1}").strip()
    if not _COMPONENT_ID.fullmatch(component_id):
        raise ValueError(
            f"components[{index}].id inválido; use até 64 caracteres alfanuméricos, _ ou -"
        )
    role = str(value.get("role", "prop")).strip().casefold() or "prop"
    if role not in COMPONENT_ROLES:
        raise ValueError(f"components[{index}].role inválido: {role}")
    parent = str(value.get("parent", "character")).strip() or "character"
    attach_to = value.get("attach_to")
    if attach_to is not None:
        attach_to = str(attach_to).strip() or None
    attach_to_secondary = value.get("attach_to_secondary")
    if attach_to_secondary is not None:
        attach_to_secondary = str(attach_to_secondary).strip() or None
    if attach_to_secondary and not attach_to:
        raise ValueError(
            f"components[{index}].attach_to_secondary exige attach_to primário"
        )
    if attach_to_secondary and parent != "character":
        raise ValueError(
            f"components[{index}].attach_to_secondary só pode usar parent character"
        )
    two_hand_axis = str(value.get("two_hand_axis", "z")).strip().casefold() or "z"
    if two_hand_axis not in TWO_HAND_AXES:
        raise ValueError(
            f"components[{index}].two_hand_axis inválido: {two_hand_axis}"
        )
    transform = normalize_transform(value.get("transform"))
    legacy = bool(value.get("legacy", False))
    if (
        legacy
        and role == "weapon"
        and parent == "character"
        and attach_to in {"hand_r", "hand_l"}
        and not attach_to_secondary
    ):
        # Legacy hand offsets compensated for a wrist-based socket. Applying
        # them to the palm anchor would move the grip back to the wrist again.
        transform["position"] = [0.0, 0.0, 0.0]
    return {
        "id": component_id,
        "asset_id": asset_id,
        "role": role,
        "parent": parent,
        "attach_to": attach_to,
        "attach_to_secondary": attach_to_secondary,
        "two_hand_axis": two_hand_axis,
        "transform": transform,
        "fit": normalize_fit(value.get("fit")),
        "visible": bool(value.get("visible", True)),
        "legacy": legacy,
    }


def _legacy_components(relationship: dict[str, Any]) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    weapon_id = str(relationship.get("weapon_asset_id") or "").strip()
    if weapon_id:
        components.append(
            {
                "id": "weapon",
                "asset_id": weapon_id,
                "role": "weapon",
                "parent": "character",
                "attach_to": "hand_r",
                "fit": {"mode": "character_height", "ratio": 0.8},
                "legacy": True,
            }
        )
    shield_id = str(relationship.get("shield_asset_id") or "").strip()
    if shield_id:
        components.append(
            {
                "id": "shield",
                "asset_id": shield_id,
                "role": "shield",
                "parent": "character",
                "attach_to": "hand_l",
                "fit": {"mode": "character_height", "ratio": 0.65},
                "legacy": True,
            }
        )
    return components


def normalize_components(relationship: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a validated component list, including legacy weapon/shield data."""
    raw = relationship.get("components")
    if raw is None:
        raw = _legacy_components(relationship)
    if not isinstance(raw, list):
        raise ValueError("components deve ser uma lista")
    if len(raw) > MAX_COMPONENTS:
        raise ValueError(f"components não pode exceder {MAX_COMPONENTS} itens")
    components = [normalize_component(value, index) for index, value in enumerate(raw)]
    component_ids = {component["id"] for component in components}
    if len(component_ids) != len(components):
        raise ValueError("components.id deve ser único dentro da composição")
    valid_parents = ROOT_PARENTS | component_ids
    for component in components:
        if component["parent"] not in valid_parents:
            raise ValueError(
                f"components[{component['id']}].parent aponta para um componente inexistente"
            )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> None:
        if component_id in visiting:
            raise ValueError("components.parent não pode formar um ciclo")
        if component_id in visited:
            return
        visiting.add(component_id)
        parent = next(item["parent"] for item in components if item["id"] == component_id)
        if parent in component_ids:
            visit(parent)
        visiting.remove(component_id)
        visited.add(component_id)

    for component in components:
        visit(component["id"])
    return components


def legacy_asset_ids(components: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Return compatibility weapon/shield IDs derived from normalized components."""
    weapon = next((item["asset_id"] for item in components if item["role"] == "weapon"), None)
    shield = next((item["asset_id"] for item in components if item["role"] == "shield"), None)
    return weapon, shield
