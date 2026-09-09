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
    mapping = report['mapping']
    source_roles = {bone_role(s): s for s in mapping.values() if bone_role(s)}
    target_roles = {bone_role(t): t for t in mapping if bone_role(t)}
    scene = bpy.context.scene
    # Removing an Action does not clear its evaluated channels. Reference
    # matrices come exclusively from the immutable data-bone bind pose.
    sw = source.matrix_world.copy()
    tw = target.matrix_world.copy()
    sr = {b.name: sw @ b.matrix_local for b in source.data.bones}
    tr = {b.name: tw @ b.matrix_local for b in target.data.bones}
    alignment = _body_frame(tr, target_roles) @ _body_frame(sr, source_roles).inverted()
    scale = _leg_height(tr, target_roles) / max(_leg_height(sr, source_roles), 1e-8)
    target_object_rotation_inverse = tw.to_quaternion().inverted()
    source_pelvis = source_roles['pelvis']
    target_pelvis = target_roles['pelvis']
    corrections = {t: tr[t].to_quaternion() for t in mapping}
    # Calibrate every mapped bone from a direct mapped child. This is
    # important for rigs with extra spine/neck joints: calibrating Spine1
    # against Head would include several independent joints and skew the
    # torso. Bone roll is never used as the calibration reference.
    preferred_children = {
        'pelvis': ('spine_01',),
        'spine_01': ('spine_02', 'neck_01', 'head'),
        'spine_02': ('spine_03', 'neck_01', 'head'),
        'spine_03': ('neck_01', 'head'),
        'neck_01': ('head',),
        'clavicle_l': ('upperarm_l',), 'clavicle_r': ('upperarm_r',),
        'upperarm_l': ('lowerarm_l',), 'upperarm_r': ('lowerarm_r',),
        'lowerarm_l': ('hand_l',), 'lowerarm_r': ('hand_r',),
        'thigh_l': ('calf_l',), 'thigh_r': ('calf_r',),
        'calf_l': ('foot_l',), 'calf_r': ('foot_r',),
    }
    for t in mapping:
        s = mapping[t]
        role = bone_role(t)
        target_children = list(target.data.bones[t].children)
        child = next((target.data.bones[target_roles[r]] for r in preferred_children.get(role, ())
                      if r in target_roles and target_roles[r] in mapping and
                      target.data.bones[target_roles[r]].parent == target.data.bones[t]), None)
        if child is None:
            child = next((candidate for candidate in target_children
                          if candidate.name in mapping and
                          source.data.bones[mapping[candidate.name]].parent and
                          source.data.bones[mapping[candidate.name]].parent.name == s), None)
        if child is None:
            continue
        sc = mapping[child.name]
        tv = tr[child.name].translation - tr[t].translation
        sv = alignment @ (sr[sc].translation - sr[s].translation)
        if tv.length > 1e-8 and sv.length > 1e-8:
            corrections[t] = tv.rotation_difference(sv) @ corrections[t]
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
    first = (source.matrix_world @ source.pose.bones[source_pelvis].matrix).translation.copy()
    up = _body_frame(tr, target_roles) @ Vector((0, 0, 1))
    action = bpy.data.actions.new(f'RETARGET|{label}')
    _set_action(target, action)
    ordered = sorted(target.pose.bones, key=lambda b: len(b.parent_recursive))
    previous = {}
    for frame in range(start, end + 1):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        source_world = {s: source.matrix_world @ source.pose.bones[s].matrix for s in mapping.values()}
        displacement = alignment @ (source_world[source_pelvis].translation - sr[source_pelvis].translation) * scale
        if in_place:
            travel = alignment @ (source_world[source_pelvis].translation - first) * scale
            displacement -= travel - up * travel.dot(up)
        solved = {}
        for bone in ordered:
            parent = bone.parent
            kwargs = dict(parent_matrix=solved[parent.name], parent_matrix_local=parent.bone.matrix_local) if parent else {}
            # Preserve target joint positions and solve extra parents with FK.
            pose = bone.bone.convert_local_to_pose(Matrix.Identity(4), bone.bone.matrix_local, **kwargs)
            if bone.name in mapping:
                s = mapping[bone.name]
                delta = source_world[s].to_quaternion() @ sr[s].to_quaternion().inverted()
                rotation = target_object_rotation_inverse @ alignment @ delta @ alignment.inverted() @ corrections[bone.name]
                position = pose.translation.copy()
                if bone.name == target_pelvis:
                    position = tw.inverted() @ (tr[target_pelvis].translation + displacement)
                pose = Matrix.LocRotScale(position, rotation, Vector((1, 1, 1)))
            basis = bone.bone.convert_local_to_pose(pose, bone.bone.matrix_local, invert=True, **kwargs)
            bone.matrix_basis = basis
            if bone.name in mapping:
                q = bone.rotation_quaternion.copy()
                if bone.name in previous and q.dot(previous[bone.name]) < 0:
                    q.negate()
                bone.rotation_quaternion = q
                previous[bone.name] = q.copy()
                for channel in ('location', 'rotation_quaternion', 'scale'):
                    bone.keyframe_insert(data_path=channel, frame=frame)
            solved[bone.name] = pose
    action['retarget_schema'] = RETARGET_SCHEMA
    action['retarget_version'] = RETARGET_VERSION
    action['source_action'] = source_action.name
    action['source_rig_family'] = report['source_rig_family']
    action['target_rig_family'] = report['target_rig_family']
    action['bone_map'] = json.dumps(mapping, sort_keys=True)
    action['height_scale'] = scale
    action['in_place'] = in_place
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
