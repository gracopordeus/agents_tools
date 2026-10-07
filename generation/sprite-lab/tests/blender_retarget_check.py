"""Real-asset visual/numerical check. Run with Blender --python ... -- --out DIR."""
import sys
import json
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bpy
from mathutils import Vector
from blender_semantic_preview import import_asset
from blender_retarget import retarget_action, _set_action, _body_frame
from rig_compatibility import bone_role


def run():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True)
    p.add_argument('--target', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--action')
    args = p.parse_args(sys.argv[sys.argv.index('--') + 1:])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    target = next(o for o in import_asset(Path(args.target)) if o.type == 'ARMATURE')
    source = next(o for o in import_asset(Path(args.source)) if o.type == 'ARMATURE')
    actions = list(bpy.data.actions)
    leaf = str(args.action or '').split('|')[-1]
    sa = next((item for item in actions if item.name == args.action), None) if args.action else source.animation_data.action
    sa = sa or next((item for item in actions if item.name.removesuffix('.001').split('|')[-1] == leaf), None)
    if sa is None:
        raise RuntimeError(f"action not found: {args.action}")
    action, report = retarget_action(target, source, sa, 'check')
    sr = {b.name: source.matrix_world @ b.matrix_local for b in source.data.bones}
    tr = {b.name: target.matrix_world @ b.matrix_local for b in target.data.bones}
    roles = {bone_role(s): s for s in report['mapping'].values() if bone_role(s)}
    targets = {bone_role(t): t for t in report['mapping'] if bone_role(t)}
    alignment = _body_frame(tr, targets) @ _body_frame(sr, roles).inverted()
    samples = []
    max_angle = 0
    max_stretch = 0
    max_angle_info = None
    max_stretch_info = None
    start, end = map(int, action.frame_range)
    for f in range(start, end+1):
        bpy.context.scene.frame_set(f)
        bpy.context.view_layer.update()
        s = {r: source.matrix_world @ source.pose.bones[n].matrix.translation for r,n in roles.items()}
        t = {r: target.matrix_world @ target.pose.bones[n].matrix.translation for r,n in targets.items()}
        for side in ('l','r'):
            for a,b in [('clavicle','upperarm'),('upperarm','lowerarm'),('lowerarm','hand'),('thigh','calf'),('calf','foot')]:
                a,b = a+'_'+side,b+'_'+side
                av = alignment @ (s[b]-s[a])
                bv = t[b]-t[a]
                angle = math.degrees(av.angle(bv))
                if angle > max_angle:
                    max_angle = angle
                    max_angle_info = (f, a, b, angle)
                bind_length = (tr[targets[b]].translation-tr[targets[a]].translation).length
                stretch = abs(bv.length/bind_length-1)
                if stretch > max_stretch:
                    max_stretch = stretch
                    max_stretch_info = (f, a, b, stretch, bv.length, bind_length)
        for a, b in [('pelvis', 'spine_01'), ('spine_01', 'spine_02'),
                     ('spine_02', 'spine_03'), ('spine_03', 'neck_01'),
                     ('neck_01', 'head')]:
            if a not in s or b not in s or a not in t or b not in t:
                continue
            av = alignment @ (s[b] - s[a])
            bv = t[b] - t[a]
            angle = math.degrees(av.angle(bv))
            if angle > max_angle:
                max_angle = angle
                max_angle_info = (f, a, b, angle)
            bind_length = (tr[targets[b]].translation-tr[targets[a]].translation).length
            stretch = abs(bv.length/bind_length-1)
            if stretch > max_stretch:
                max_stretch = stretch
                max_stretch_info = (f, a, b, stretch, bv.length, bind_length)
        samples.append(dict(frame=f, source={r:list(alignment@(v-s['pelvis'])) for r,v in s.items()},
                            target={r:list(v-t['pelvis']) for r,v in t.items()}))
    result = dict(max_segment_angle_degrees=max_angle, max_relative_stretch=max_stretch,
                  frames=len(samples), samples=samples)
    (out/'poses.json').write_text(json.dumps(result))
    print('RETARGET_CHECK', {k:v for k,v in result.items() if k != 'samples'}, 'angle_info=', max_angle_info, 'stretch_info=', max_stretch_info)
    assert max_angle < 3, f'limb direction error {max_angle}'
    assert max_stretch < .001, f'bone stretch {max_stretch}'
    # Export the actual target and baked animation for further inspection.
    for obj in list(bpy.data.objects):
        if obj == source or (obj.type=='MESH' and any(m.type=='ARMATURE' and m.object==source for m in obj.modifiers)):
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.export_scene.gltf(filepath=str(out/'retarget.glb'), export_format='GLB',
                              export_animations=True, export_animation_mode='ACTIVE_ACTIONS')


if __name__ == '__main__':
    run()
