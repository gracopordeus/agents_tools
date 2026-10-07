#!/usr/bin/env python3
"""Filtro do canal bones — puro (sem ``bpy``), testável fora do Blender.

O canal bones é uma projeção direta da armature do Blender. O exporter
iterava todos os ``pose.bones`` sem filtro: 65 bones no asset atual, ~20% do
traço em falanges de 0–2px (ruído sub-pixel a 256²), mais ``root`` e eventuais
controladores (IK/pole/target) que não são parte do esqueleto corporal.

Regra: mantém só a cadeia corporal de deformação.
"""
from __future__ import annotations

#: Nomes exatos sempre ignorados (comparação case-insensitive).
SKIP_EXACT = frozenset({"root"})

#: Substrings que marcam bones a ignorar (case-insensitive).
SKIP_SUBSTRINGS = frozenset({
    # Dedos e ossos terminais — não definem o esqueleto corporal principal.
    "index_", "middle_", "pinky_", "ring_", "thumb_", "finger",
    "toe", "ball", "leaf",  # terminais (*_leaf, toes, ball of foot)
    # Controladores de rig — não são pose.
    "ik", "pole", "target", "ctrl", "control", "helper",
    "socket", "attach", "handle",
})


def keep_skeleton_bone(name: str, use_deform: bool = True) -> bool:
    """Diz se o bone entra no canal bones do condicionamento."""
    lowered = str(name or "").casefold()
    if not lowered:
        return False
    if not use_deform:
        return False
    if lowered in SKIP_EXACT:
        return False
    return not any(mark in lowered for mark in SKIP_SUBSTRINGS)
