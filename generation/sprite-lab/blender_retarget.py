"""Blender-side Action retargeting between compatible humanoid rigs.

The worker uses the source and target rest-pose bone bases to transfer local
pose deltas, then bakes the result into a standalone Action owned by the
target armature.  No source FBX or source armature is modified.
"""
from __future__ import annotations

import json
import math
from typing import Any

import bpy
from mathutils import Matrix, Vector

from rig_compatibility import (
    RETARGET_VERSION,
    bone_role,
    compatibility_report,
)


def _set_action(armature: bpy.types.Object, action: bpy.types.Action | None) -> None:
    if armature.animation_data is None:
        armature.animation_data_create()
    armature.animation_data.action = action
    slots = getattr(action, "slots", ()) if action is not None else ()
    if slots and hasattr(armature.animation_data, "action_slot"):
        armature.animation_data.action_slot = slots[0]


def _frame_range(action: bpy.types.Action) -> tuple[int, int]:
    start = max(1, math.floor(float(action.frame_range[0])))
    end = max(start, math.ceil(float(action.frame_range[1])))
    return start, end


def _world_position(armature: bpy.types.Object, bone: bpy.types.PoseBone) -> Vector:
    return (armature.matrix_world @ bone.matrix).translation.copy()


def _skeleton_height(armature: bpy.types.Object) -> float:
    pelvis = next(
        (bone for bone in armature.pose.bones if bone_role(bone.name) == "pelvis"),
        None,
    )
    head = next(
        (bone for bone in armature.pose.bones if bone_role(bone.name) == "head"),
        None,
    )
    if pelvis is None or head is None:
        return 1.0
    height = (_world_position(armature, head) - _world_position(armature, pelvis)).length
    return max(float(height), 1e-6)


def _set_target_motion(
    target: bpy.types.Object,
    target_bone: bpy.types.PoseBone,
    rest_location: Vector,
    source_displacement: Vector,
    scale: float,
) -> None:
    # Root and Mixamo Hips are both top-level bones, so the target location
    # channel is expressed in armature space.  The parent-aware conversion
    # keeps this correct for a future rig with an extra control bone.
    displacement_world = source_displacement * scale
    parent = target_bone.parent
    if parent is None:
        local_delta = target.matrix_world.inverted().to_3x3() @ displacement_world
    else:
        parent_world = target.matrix_world @ parent.matrix
        local_delta = parent_world.to_3x3().inverted() @ displacement_world
    target_bone.location = rest_location + local_delta


def _keyframe_bone(bone: bpy.types.PoseBone, frame: int) -> None:
    bone.keyframe_insert(data_path="location", frame=frame)
    bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
    bone.keyframe_insert(data_path="scale", frame=frame)


def retarget_action(
    target: bpy.types.Object,
    source: bpy.types.Object,
    source_action: bpy.types.Action,
    label: str = "animation",
) -> tuple[bpy.types.Action, dict[str, Any]]:
    """Bake ``source_action`` onto ``target`` and return the new Action."""
    source_names = [bone.name for bone in source.data.bones]
    target_names = [bone.name for bone in target.data.bones]
    report = compatibility_report(source_names, target_names)
    if not report["compatible"]:
        missing = ", ".join(report["missing_critical_roles"])
        raise RuntimeError(f"rigs incompatíveis; bones críticos ausentes: {missing}")

    mapping = report["mapping"]
    previous_source_action = source.animation_data.action if source.animation_data else None
    previous_target_action = target.animation_data.action if target.animation_data else None
    _set_action(source, None)
    _set_action(target, None)
    scene = bpy.context.scene
    scene.frame_set(1)
    bpy.context.view_layer.update()
    source_rest = {
        source_name: source.pose.bones[source_name].matrix_basis.copy()
        for source_name in mapping.values()
    }
    target_rest = {
        target_name: target.pose.bones[target_name].matrix_basis.copy()
        for target_name in mapping
    }
    target_rest_locations = {
        target_name: target.pose.bones[target_name].location.copy()
        for target_name in mapping
    }
    source_pelvis = next(
        (bone for bone in source.pose.bones if bone_role(bone.name) == "pelvis"),
        None,
    )
    target_motion = next(
        (
            bone
            for bone in target.pose.bones
            if bone_role(bone.name) in {"root", "pelvis"}
        ),
        None,
    )
    if source_pelvis is None or target_motion is None:
        raise RuntimeError("retargeting exige ossos pelvis/root em ambos os rigs")
    target_motion_rest_location = target_motion.location.copy()
    source_pelvis_rest = _world_position(source, source_pelvis)
    target_family = report["target_rig_family"]
    source_family = report["source_rig_family"]
    height_scale = _skeleton_height(target) / _skeleton_height(source)

    _set_action(source, source_action)
    start, end = _frame_range(source_action)
    scene.frame_start = start
    scene.frame_end = end
    action_name = f"RETARGET|{target_family}|{source_family}|{label}"
    action = bpy.data.actions.new(action_name)
    _set_action(target, action)

    for target_name in mapping:
        target.pose.bones[target_name].rotation_mode = "QUATERNION"

    for frame in range(start, end + 1):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        for target_name, source_name in mapping.items():
            target_bone = target.pose.bones[target_name]
            source_bone = source.pose.bones[source_name]
            # matrix_basis contains the imported rest-pose basis. Removing it
            # before transfer prevents Mixamo's bone-axis conventions from
            # leaking into UAL1, and multiplying by the target basis restores
            # the target rig's own rest orientation.
            delta = source_bone.matrix_basis @ source_rest[source_name].inverted()
            delta.translation = (0.0, 0.0, 0.0)
            target_bone.matrix_basis = target_rest[target_name] @ delta

        source_displacement = _world_position(source, source_pelvis) - source_pelvis_rest
        _set_target_motion(
            target,
            target_motion,
            target_rest_locations.get(target_motion.name, target_motion_rest_location),
            source_displacement,
            height_scale,
        )
        bpy.context.view_layer.update()
        for target_bone in target.pose.bones:
            if target_bone.name in mapping or target_bone is target_motion:
                _keyframe_bone(target_bone, frame)

    action["retarget_schema"] = "sprite_lab.rig_compatibility/v1"
    action["retarget_version"] = RETARGET_VERSION
    action["source_action"] = source_action.name
    action["source_rig_family"] = source_family
    action["target_rig_family"] = target_family
    action["bone_map"] = json.dumps(mapping, ensure_ascii=False, sort_keys=True)
    action["height_scale"] = height_scale
    # Ensure the action evaluates at its first frame before source objects are
    # removed by the caller.
    scene.frame_set(start)
    bpy.context.view_layer.update()
    if previous_source_action is not None and previous_source_action != source_action:
        _set_action(source, source_action)
    if previous_target_action is not None and previous_target_action != action:
        _set_action(target, action)
    return action, report
