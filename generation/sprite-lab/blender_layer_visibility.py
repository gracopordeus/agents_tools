"""Restorable Blender object visibility policies for layered sprite passes."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from collections.abc import Iterable, Iterator
from typing import Any


COMPONENT_ROOT_PREFIX = "sprite_component_"
COMPONENT_ROLE_PROPERTY = "conditioning_component_role"
COMPONENT_ID_PROPERTY = "conditioning_component_id"


def component_role(obj: Any) -> str | None:
    """Read the composition role inherited by an object without Blender APIs."""
    current = obj
    while current is not None:
        declared = str(current.get(COMPONENT_ROLE_PROPERTY, "")).strip().casefold()
        if declared:
            return declared
        if str(getattr(current, "name", "")).startswith(COMPONENT_ROOT_PREFIX):
            return "prop"
        current = getattr(current, "parent", None)
    return None


def component_id(obj: Any) -> str | None:
    """Read the composition id inherited by an object without Blender APIs."""
    current = obj
    while current is not None:
        declared = str(current.get(COMPONENT_ID_PROPERTY, "")).strip()
        if declared:
            return declared
        current = getattr(current, "parent", None)
    return None


def weapon_objects(objects: Iterable[Any]) -> list[Any]:
    """Return objects belonging to components explicitly declared as weapons."""
    return [obj for obj in objects if component_role(obj) == "weapon"]


def character_pass_metadata(
    render_metadata: dict[str, Any],
    effective_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Copy the alignment contract shared with the primary beauty pass."""
    return {
        "enabled": True,
        "channels": ["character_beauty", "character_lineart"],
        "directions": list(render_metadata["directions"]),
        "sampled_frames": list(render_metadata["sampled_frames"]),
        "camera": dict(render_metadata["camera"]),
        "cell": list(render_metadata["cell"]),
        "foot_anchor": (
            list(effective_profile["foot_anchor"])
            if effective_profile
            else None
        ),
        "transparent_background": True,
    }


def weapon_pass_metadata(
    render_metadata: dict[str, Any],
    component: dict[str, Any],
) -> dict[str, Any]:
    """Copy weapon identity, attachment and primary-pass alignment metadata."""
    return {
        "enabled": True,
        "channels": ["weapon_beauty", "weapon_silhouette"],
        "directions": list(render_metadata["directions"]),
        "sampled_frames": list(render_metadata["sampled_frames"]),
        "camera": dict(render_metadata["camera"]),
        "cell": list(render_metadata["cell"]),
        "component": {
            field: copy.deepcopy(component.get(field))
            for field in ("id", "asset_id", "role", "attach_to", "transform", "fit")
        },
        "cells": [
            {
                field: copy.deepcopy(cell.get(field))
                for field in (
                    "row",
                    "direction",
                    "column",
                    "frame",
                    "weapon_beauty_path",
                    "weapon_silhouette_path",
                )
                if field in cell
            }
            for cell in render_metadata["cells"]
        ],
        "transparent_background": True,
    }


@contextmanager
def character_only_visibility(objects: Iterable[Any]) -> Iterator[None]:
    """Hide weapon objects for one pass and restore every original flag."""
    objects = list(objects)
    original = [(obj, bool(obj.hide_render)) for obj in objects]
    try:
        for obj in weapon_objects(objects):
            obj.hide_render = True
        yield
    finally:
        for obj, hidden in original:
            obj.hide_render = hidden


@contextmanager
def weapon_only_visibility(
    objects: Iterable[Any],
    weapon_component_id: str,
) -> Iterator[list[Any]]:
    """Show only meshes of one weapon component and restore all flags."""
    objects = list(objects)
    selected = [
        obj
        for obj in objects
        if component_role(obj) == "weapon"
        and component_id(obj) == weapon_component_id
    ]
    if not selected:
        raise ValueError(
            f"weapon_component_id '{weapon_component_id}' não possui meshes renderizáveis"
        )
    selected_identities = {id(obj) for obj in selected}
    original = [(obj, bool(obj.hide_render)) for obj in objects]
    try:
        for obj in objects:
            obj.hide_render = id(obj) not in selected_identities
        yield selected
    finally:
        for obj, hidden in original:
            obj.hide_render = hidden
