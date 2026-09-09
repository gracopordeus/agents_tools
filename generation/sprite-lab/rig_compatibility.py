"""Rig-family detection and bone-role mapping for animation retargeting.

The catalog intentionally keeps the original armatures untouched.  This
module only describes their semantic bone roles so the Blender worker can
retarget an Action between rigs with different names and root conventions.
"""
from __future__ import annotations

import re
from typing import Iterable


RETARGET_SCHEMA = "sprite_lab.rig_compatibility/v1"
RETARGET_VERSION = "humanoid-world-v2"

CRITICAL_ROLES = {
    "pelvis",
    "spine_01",
    "head",
    "upperarm_l",
    "upperarm_r",
    "lowerarm_l",
    "lowerarm_r",
    "hand_l",
    "hand_r",
    "thigh_l",
    "thigh_r",
    "calf_l",
    "calf_r",
    "foot_l",
    "foot_r",
}


def _compact(value: str) -> str:
    value = str(value or "").casefold()
    value = value.rsplit(":", 1)[-1]
    value = re.sub(r"mixamorig\d*", "", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def _side_and_body(value: str) -> tuple[str | None, str]:
    compact = _compact(value)
    if compact.startswith("left"):
        return "l", compact[4:]
    if compact.startswith("right"):
        return "r", compact[5:]
    if compact.endswith("left"):
        return "l", compact[:-4]
    if compact.endswith("right"):
        return "r", compact[:-5]
    if compact.endswith("l") and len(compact) > 1:
        return "l", compact[:-1]
    if compact.endswith("r") and len(compact) > 1:
        return "r", compact[:-1]
    return None, compact


def bone_role(name: str) -> str | None:
    """Return a stable semantic role for a Mixamo/UAL-style bone name."""
    compact = _compact(name)
    if compact in {"root", "master"}:
        return "root"
    if compact in {"hip", "hips", "pelvis"}:
        return "pelvis"
    if compact in {"spine", "spine01"}:
        return "spine_01"
    if compact in {"spine1", "spine02"}:
        return "spine_02"
    if compact in {"spine2", "spine03"}:
        return "spine_03"
    if compact in {"chest", "upperchest", "thorax"}:
        return "spine_02"
    if compact in {"neck", "neck01"}:
        return "neck_01"
    if compact == "head":
        return "head"
    # End markers are not deforming controls and should not receive keys.
    if compact in {"headtopend", "headend"}:
        return None

    side, body = _side_and_body(compact)
    if side is None:
        return None
    if body in {"clavicle", "shoulder"}:
        return f"clavicle_{side}"
    if body in {"arm", "upperarm"}:
        return f"upperarm_{side}"
    if body in {"forearm", "lowerarm"}:
        return f"lowerarm_{side}"
    if body == "hand":
        return f"hand_{side}"
    if body in {"upleg", "thigh", "upperleg"}:
        return f"thigh_{side}"
    if body in {"leg", "calf", "lowerleg"}:
        return f"calf_{side}"
    if body == "foot":
        return f"foot_{side}"
    if body in {"ball", "toebase", "toe"}:
        return f"ball_{side}"
    if body in {"ballleaf", "toeend", "toebaseend"}:
        return f"ball_leaf_{side}"

    finger_match = re.fullmatch(r"(thumb|index|middle|ring|pinky|little)(\d+)(?:leaf|end)?", body)
    if finger_match:
        finger, number = finger_match.groups()
        if finger == "little":
            finger = "pinky"
        return f"{finger}_{int(number):02d}_{side}"
    finger_match = re.fullmatch(
        r"hand(?:thumb|index|middle|ring|pinky|little)(\d+)", body
    )
    if finger_match:
        # This branch is retained for exporters that keep ``Hand`` in the
        # finger name after the side prefix has been removed.
        prefix = body.removeprefix("hand")
        finger = re.match(r"[a-z]+", prefix)
        number = finger_match.group(1)
        if finger:
            value = finger.group(0)
            if value == "little":
                value = "pinky"
            return f"{value}_{int(number):02d}_{side}"
    return None


def infer_rig_family(names: Iterable[str]) -> str:
    names = [str(name) for name in names]
    values = [_compact(name) for name in names]
    if any("mixamorig" in name.casefold() for name in names):
        return "mixamo"
    if any(value in {"pelvis", "spine01", "upperarml", "calfl"} for value in values):
        return "ual1"
    return "generic"


def build_bone_map(source_names: Iterable[str], target_names: Iterable[str]) -> dict[str, str]:
    """Build ``target bone -> source bone`` mapping using semantic roles."""
    source_by_role: dict[str, str] = {}
    for name in source_names:
        role = bone_role(name)
        if role and role not in source_by_role:
            source_by_role[role] = str(name)
    result: dict[str, str] = {}
    for name in target_names:
        role = bone_role(name)
        if role in source_by_role:
            result[str(name)] = source_by_role[role]
    return result


def compatibility_report(source_names: Iterable[str], target_names: Iterable[str], mapping_override: dict | None = None) -> dict:
    source_names = [str(name) for name in source_names]
    target_names = [str(name) for name in target_names]
    mapping = build_bone_map(source_names, target_names)
    if mapping_override:
        for target, source in mapping_override.items():
            if target not in target_names or source not in source_names:
                raise ValueError(f"osso inexistente no mapeamento: {target} -> {source}")
        mapping.update(mapping_override)
    mapped_roles = {bone_role(name) for name in mapping.values()}
    missing_critical = sorted(CRITICAL_ROLES - mapped_roles)
    return {
        "schema": RETARGET_SCHEMA,
        "version": RETARGET_VERSION,
        "source_rig_family": infer_rig_family(source_names),
        "target_rig_family": infer_rig_family(target_names),
        "source_bone_count": len(source_names),
        "target_bone_count": len(target_names),
        "mapped_bone_count": len(mapping),
        "missing_critical_roles": missing_critical,
        "compatible": not missing_critical,
        "mapping": mapping,
    }
