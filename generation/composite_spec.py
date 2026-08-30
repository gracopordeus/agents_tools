#!/usr/bin/env python3
"""Composite spec — o equivalente ao COF do Diablo II para o catálogo 3D.

Declara a composição modular de um personagem: armadura (rig + meshes por
slot), arma + escudo (mão + wclass), e o mapa de animações por (modo, wclass),
espelhando o COF (camadas + ordem + arma) e o `alternategfx` (fallback) do D2.

O assembler (`blender_compose_render.py`) consome este contrato para montar a
cena e renderizar as células 8x8; o catálogo o indexa como proveniência.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_ID = "quaternius_composite/v1"

# Mapa (modo, wclass) -> ação na UAL. wclass usa a nomenclatura do D2.
# A UAL Standard nomeia: Walk_Loop, Jog_Fwd_Loop, Sprint_Loop, Idle_Loop,
# Sword_Idle, Sword_Attack, Punch_Cross, ...
DEFAULT_ACTION_MAP: dict[str, dict[str, str]] = {
    "idle": {"default": "Idle_Loop", "1hs": "Sword_Idle", "2hs": "Sword_Idle"},
    "run": {"default": "Jog_Fwd_Loop"},
    "walk": {"default": "Walk_Loop"},
    "attack": {"default": "Sword_Attack"},
}

BONE_ALIASES = {
    "root": ("root",),
    "hips": ("pelvis", "hips", "mixamorig:hips"),
    "spine": ("spine_02", "spine", "spine_01"),
    "head": ("head",),
    "hand_r": ("hand_r", "righthand", "right_hand"),
    "hand_l": ("hand_l", "lefthand", "left_hand"),
    "upperarm": ("upperarm_r", "upper_arm_r", "shoulder_r"),
    "thigh": ("thigh_r", "upper_leg_r"),
    "foot": ("foot_r",),
}


def resolve_action(animations: dict[str, Any], mode: str, wclass: str | None = None) -> str | None:
    """Ação da UAL para (modo, wclass), com fallback para o modo sem wclass."""
    if mode in animations:
        return animations[mode].get("action")
    return None


def build_spec(*, composite_id: str, rig_family: str, armature: dict[str, Any],
               slots: dict[str, Any], weapon: dict[str, Any] | None,
               shield: dict[str, Any] | None,
               animations: dict[str, Any]) -> dict[str, Any]:
    """Monta o composite spec validado."""
    spec: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "id": composite_id,
        "rig_family": rig_family,
        "armature": armature,
        "slots": slots,
        "weapon": weapon,
        "shield": shield,
        "animations": animations,
    }
    validate_spec(spec)
    return spec


def validate_spec(spec: dict[str, Any]) -> None:
    required = {"schema", "id", "rig_family", "armature", "slots", "animations"}
    missing = sorted(required - set(spec))
    if missing:
        raise ValueError(f"composite spec incompleto; faltando: {', '.join(missing)}")
    if spec["schema"] != SCHEMA_ID:
        raise ValueError(f"schema incompatível: {spec['schema']!r}")
    arm = spec["armature"]
    for key in ("source", "member", "path"):
        if key not in arm:
            raise ValueError(f"armature sem campo obrigatório: {key}")
    for mode, entry in spec["animations"].items():
        if "action" not in entry:
            raise ValueError(f"animação '{mode}' sem action")
    if "weapon" in spec and spec["weapon"] is not None:
        w = spec["weapon"]
        for key in ("source", "fbx", "hand", "wclass"):
            if key not in w:
                raise ValueError(f"weapon sem campo obrigatório: {key}")


def write_spec(path: Path, spec: dict[str, Any]) -> None:
    validate_spec(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_spec(path: Path) -> dict[str, Any]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    validate_spec(spec)
    return spec


if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Gera o composite spec de um combo.")
    p.add_argument("--id", required=True)
    p.add_argument("--arm-source")
    p.add_argument("--arm-member")
    p.add_argument("--arm-path", required=True)
    p.add_argument("--weapon-source")
    p.add_argument("--weapon-fbx")
    p.add_argument("--weapon-path")
    p.add_argument("--weapon-wclass", default="1hs")
    p.add_argument("--weapon-hand", default="right")
    p.add_argument("--shield-source")
    p.add_argument("--shield-fbx")
    p.add_argument("--shield-path")
    p.add_argument("--anim-source")
    p.add_argument("--anim-fbx")
    p.add_argument("--anim-path")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    spec = build_spec(
        composite_id=args.id,
        rig_family="quaternius",
        armature={
            "source": args.arm_source or "",
            "member": args.arm_member or "",
            "path": args.arm_path,
            "root_bone": "root",
            "hips_bone": "pelvis",
            "hand_bones": {"right": "hand_r", "left": "hand_l"},
        },
        slots={
            "head": {"mesh": "Male_Ranger_Head_Hood", "bone": "head", "optional": False},
            "torso": {"mesh": "Male_Ranger_Body", "bone": "spine_02"},
            "arms": {"mesh": "Male_Ranger_Arms", "bones": ["upperarm_r", "upperarm_l"]},
            "legs": {"mesh": "Male_Ranger_Legs", "bones": ["thigh_r", "thigh_l"]},
            "feet": {"mesh": "Male_Ranger_Feet_Boots", "bones": ["foot_r", "foot_l"]},
            "shoulders": {"mesh": "Male_Ranger_Acc_Pauldron", "bones": ["clavicle_r", "clavicle_l"],
                          "optional": True},
        },
        weapon={
            "source": args.weapon_source or "",
            "fbx": args.weapon_fbx or "",
            "path": args.weapon_path or "",
            "hand": args.weapon_hand,
            "grip": "one_hand",
            "wclass": args.weapon_wclass,
        },
        shield={
            "source": args.shield_source or "",
            "fbx": args.shield_fbx or "",
            "path": args.shield_path or "",
            "hand": "left",
            "wclass": "sh",
        } if args.shield_fbx else None,
        animations={
            "run": {"action": "Jog_Fwd_Loop", "source": args.anim_source or "",
                    "fbx": args.anim_fbx or "", "path": args.anim_path or "",
                    "fps": 10, "loop": True, "wclasses": ["1hs"]},
            "idle": {"action": "Sword_Idle", "source": args.anim_source or "",
                     "fbx": args.anim_fbx or "", "path": args.anim_path or "",
                     "fps": 10, "loop": True, "wclasses": ["1hs"]},
            "attack": {"action": "Sword_Attack", "source": args.anim_source or "",
                       "fbx": args.anim_fbx or "", "path": args.anim_path or "",
                       "fps": 10, "loop": False, "wclasses": ["1hs"]},
        },
    )
    write_spec(Path(args.out), spec)
    print(f"spec escrito: {args.out}")
    sys.exit(0)
