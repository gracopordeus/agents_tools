"""Rest-calibrated world-space humanoid retargeting with target-length FK."""
from __future__ import annotations

import json
import math
import bpy
from mathutils import Matrix, Vector
from rig_compatibility import RETARGET_SCHEMA, RETARGET_VERSION, bone_role, compatibility_report


def _set_action(armature, action):
    armature.animation_data_create()
    armature.animation_data.action = action
    slots = getattr(action, 'slots', ()) if action else ()
    if slots and hasattr(armature.animation_data, 'action_slot'):
        armature.animation_data.action_slot = slots[0]


def _body_frame(rest, roles):
    up = (rest[roles['head']].translation - rest[roles['pelvis']].translation).normalized()
    right = rest[roles['thigh_r']].translation - rest[roles['thigh_l']].translation
    right = (right - up * right.dot(up)).normalized()
    if right.length < 0.5 or up.length < 0.5:
        raise ValueError('pose de referência degenerada: cabeça/quadril/pernas')
    forward = up.cross(right).normalized()
    return Matrix((right, forward, up)).transposed().to_quaternion()


def _leg_height(rest, roles):
    return sum((rest[roles[a]].translation - rest[roles[b]].translation).length
               for a, b in [('thigh_l', 'calf_l'), ('calf_l', 'foot_l')])


_TRUNK_ROLES = {'spine_01', 'spine_02', 'spine_03', 'neck_01', 'head'}


def _bone_rest_dir(armature, bone_name, mapped_names):
    """Return a stable rest direction in armature space.

    FBX/glTF importers may synthesize a bone tail differently between rigs.
    Using that tail for calibration can rotate a pelvis or shoulder by 90°.
    Prefer the next mapped joint in the semantic chain, then mapped children,
    then the parent direction, and only use the imported tail as a last resort.
    """
    bone = armature.data.bones[bone_name]
    children = [child for child in bone.children if child.name in mapped_names]
    trunk = [child for child in children if bone_role(child.name) in _TRUNK_ROLES]
    candidates = trunk or children
    if candidates:
        direction = sum(
            (child.head_local - bone.head_local for child in candidates),
            Vector((0.0, 0.0, 0.0)),
        )
        if direction.length > 1e-8:
            return direction.normalized()
    if bone.parent is not None:
        direction = bone.head_local - bone.parent.head_local
        if direction.length > 1e-8:
            return direction.normalized()
    direction = bone.tail_local - bone.head_local
    if direction.length <= 1e-8:
        return Vector((0.0, 0.0, 1.0))
    return direction.normalized()


def _axis_correction(source_rest, target_rest, source_dir, target_dir):
    """Build the fixed source-rest → target-rest correction for one bone.

    The swing between rest directions is removed before applying the source
    pose. This prevents a Mixamo T-pose/A-pose difference from being applied a
    second time on top of the target's own rest pose.
    """
    swing = source_dir.rotation_difference(target_dir)
    return source_rest.inverted() @ swing.inverted() @ target_rest


def _solve_target_basis(
    source_pose,
    rest_correction,
    target_rest,
    *,
    target_parent_pose=None,
    target_parent_rest=None,
):
    """Convert a source global pose quaternion into target local basis."""
    desired_pose = source_pose @ rest_correction
    if target_parent_pose is None or target_parent_rest is None:
        rest_chain = target_rest
    else:
        rest_chain = target_parent_pose @ target_parent_rest.inverted() @ target_rest
    return rest_chain.inverted() @ desired_pose


def _location_pair(mapping):
    """Resolve hips location by semantic role, never by source bone name."""
    for target_name, source_name in mapping.items():
        if bone_role(target_name) == 'pelvis':
            return target_name, source_name
    return None, None


def _bone_rest_height(armature, bone_name):
    """Return a stable character-height signal for hip location scaling."""
    head = armature.data.bones[bone_name].head_local
    return max(abs(float(head.y)), abs(float(head.z)), 1e-3)


def rigs_share_bind(source, target, tolerance=1e-5):
    """Names alone don't establish compatible local channels (FBX vs glTF)."""
    if set(source.data.bones.keys()) != set(target.data.bones.keys()):
        return False
    if any(abs(source.matrix_world[i][j] - target.matrix_world[i][j]) > tolerance
           for i in range(4) for j in range(4)):
        return False
    for b in source.data.bones:
        t = target.data.bones[b.name]
        if (b.parent.name if b.parent else '') != (t.parent.name if t.parent else ''):
            return False
        if any(abs(b.matrix_local[i][j] - t.matrix_local[i][j]) > tolerance
               for i in range(4) for j in range(4)):
            return False
    return True


def retarget_action(target, source, source_action, label='animation', mapping_override=None, in_place=True):
    report = compatibility_report(source.data.bones.keys(), target.data.bones.keys(), mapping_override)
    if not report['compatible']:
        raise ValueError('Mapeamento humanoide incompleto: ' + ', '.join(report['missing_critical_roles']))
    # A root imported from Mixamo/UAL is not a deforming animation control.
    # Keep the target root static at the feet; transferring its FBX axis
    # rotation is the classic ±90° Y/Z failure that moves the origin to the
    # waist. All other bones are solved from rest-calibrated global poses.
    mapping = {
        target_name: source_name
        for target_name, source_name in report['mapping'].items()
        if bone_role(target_name) != 'root'
    }
    scene = bpy.context.scene
    source_roles = {bone_role(name): name for name in mapping.values() if bone_role(name)}
    target_roles = {bone_role(name): name for name in mapping if bone_role(name)}
    # Mixamo FBX and UAL glTF can arrive with opposite forward axes even
    # though both are Z-up. Align the source's rest body frame to the target
    # before solving rotations; otherwise the animation is mirrored/turned
    # while its segment lengths still look numerically correct.
    source_world_rest = {
        bone.name: source.matrix_world @ bone.matrix_local
        for bone in source.data.bones
    }
    target_world_rest = {
        bone.name: target.matrix_world @ bone.matrix_local
        for bone in target.data.bones
    }
    # FBX import commonly keeps Mixamo's Y-up correction on the armature
    # object, while the UAL armature is already Z-up. Work in target-armature
    # space so the correction includes that object transform; comparing only
    # raw data-bone matrices makes a vertical spine look horizontal.
    source_to_target = target.matrix_world.inverted() @ source.matrix_world
    source_common_rest = {
        bone.name: source_to_target @ bone.matrix_local
        for bone in source.data.bones
    }
    target_common_rest = {bone.name: bone.matrix_local for bone in target.data.bones}
    alignment = _body_frame(target_common_rest, target_roles) @ _body_frame(
        source_common_rest, source_roles
    ).inverted()
    scale = _leg_height(
        target_world_rest,
        target_roles,
    ) / max(
        _leg_height(
            source_world_rest,
            source_roles,
        ),
        1e-8,
    )
    source_rest = {
        bone.name: alignment @ (source_to_target @ bone.matrix_local).to_quaternion()
        for bone in source.data.bones
    }
    target_rest = {b.name: b.matrix_local.to_quaternion() for b in target.data.bones}
    source_mapped = set(mapping.values())
    target_mapped = set(mapping)
    corrections = {
        target_name: _axis_correction(
            source_rest[source_name],
            target_rest[target_name],
            alignment @ (
                source_to_target.to_3x3()
                @ _bone_rest_dir(source, source_name, source_mapped)
            ),
            _bone_rest_dir(target, target_name, target_mapped),
        )
        for target_name, source_name in mapping.items()
    }
    target_parent = {
        bone.name: bone.parent.name if bone.parent else None
        for bone in target.data.bones
    }
    full_order = sorted(target.pose.bones, key=lambda bone: len(bone.parent_recursive))
    target_pelvis, source_pelvis = _location_pair(mapping)
    if target_pelvis is not None:
        location_scale = _bone_rest_height(target, target_pelvis) / _bone_rest_height(source, source_pelvis)
        location_conversion = (
            target.data.bones[target_pelvis].matrix_local.to_3x3().inverted()
            @ alignment.to_matrix()
            @ source_to_target.to_3x3()
            @ source.data.bones[source_pelvis].matrix_local.to_3x3()
        )
        target_location_up = (
            target.data.bones[target_pelvis].matrix_local.to_3x3().inverted()
            @ Vector((0.0, 0.0, 1.0))
        ).normalized()
    else:
        location_scale = 1.0
        location_conversion = None
        target_location_up = None
    _set_action(target, None)
    target.animation_data.use_nla = False
    source.animation_data_create()
    source.animation_data.use_nla = False
    for bone in target.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
        bone.rotation_mode = 'QUATERNION'
    _set_action(source, source_action)
    start, end = math.floor(source_action.frame_range[0]), math.ceil(source_action.frame_range[1])
    scene.frame_set(start)
    bpy.context.view_layer.update()
    first_location = (
        source.pose.bones[source_pelvis].location.copy()
        if source_pelvis is not None
        else Vector((0.0, 0.0, 0.0))
    )
    action = bpy.data.actions.new(f'RETARGET|{label}')
    _set_action(target, action)
    previous = {}
    for frame in range(start, end + 1):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        pose_global = {}
        for bone in full_order:
            name = bone.name
            parent = target_parent[name]
            if parent is None:
                rest_chain = target_rest[name]
            else:
                rest_chain = pose_global[parent] @ target_rest[parent].inverted() @ target_rest[name]
            if name not in mapping:
                pose_global[name] = rest_chain
                continue
            source_pose = alignment @ (
                source_to_target @ source.pose.bones[mapping[name]].matrix
            ).to_quaternion()
            if parent is None:
                basis = _solve_target_basis(source_pose, corrections[name], target_rest[name])
            else:
                basis = _solve_target_basis(
                    source_pose,
                    corrections[name],
                    target_rest[name],
                    target_parent_pose=pose_global[parent],
                    target_parent_rest=target_rest[parent],
                )
            basis.normalize()
            if name in previous:
                basis.make_compatible(previous[name])
            previous[name] = basis.copy()
            pose_global[name] = rest_chain @ basis
            pose_bone = target.pose.bones[name]
            pose_bone.rotation_quaternion = basis
            pose_bone.keyframe_insert(data_path='rotation_quaternion', frame=frame)
            if name == target_pelvis and location_conversion is not None:
                location = location_conversion @ (
                    source.pose.bones[source_pelvis].location - first_location
                )
                location *= location_scale
                if in_place and target_location_up is not None:
                    location = target_location_up * location.dot(target_location_up)
                pose_bone.location = location
                pose_bone.keyframe_insert(data_path='location', frame=frame)
    action['retarget_schema'] = RETARGET_SCHEMA
    action['retarget_version'] = RETARGET_VERSION
    action['source_action'] = source_action.name
    action['source_rig_family'] = report['source_rig_family']
    action['target_rig_family'] = report['target_rig_family']
    action['bone_map'] = json.dumps(mapping, sort_keys=True)
    action['height_scale'] = scale
    action['in_place'] = in_place
    action['root_policy'] = 'target_static_root_at_feet'
    action['location_policy'] = 'role_based_hips_rest_space'
    for layer in getattr(action, 'layers', []):
        for strip in layer.strips:
            for slot in action.slots:
                bag = strip.channelbag(slot)
                if bag:
                    for curve in bag.fcurves:
                        for key in curve.keyframe_points:
                            key.interpolation = 'LINEAR'
    scene.frame_start, scene.frame_end = start, end
    scene.frame_set(start)
    bpy.context.view_layer.update()
    return action, report
