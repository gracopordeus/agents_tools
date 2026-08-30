"""Probe: importa o FBX do Mixamo e reporta estrutura/animação (rodar via blender --background --python)."""
import bpy

FBX = "/home/ggnp/simple-arpg/mixamo/Fast Run.fbx"

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.collada_import(filepath=FBX) if False else None

# FBX import
try:
    bpy.ops.import_scene.fbx(filepath=FBX)
except Exception as e:
    print("ERRO IMPORT:", e)
    raise

print("=== OBJECTS ===")
for ob in bpy.data.objects:
    print(f"  {ob.name!r} type={ob.type}")

print("=== ARMATURE / ACTIONS ===")
for a in bpy.data.actions:
    f0 = int(a.frame_range[0]); f1 = int(a.frame_range[1])
    print(f"  action {a.name!r} frames {f0}..{f1} (len {f1-f0+1}) use_fake_user={a.use_fake_user}")

arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
if arm and arm.animation_data:
    print("  animation_data present, nla_tracks:", len(arm.animation_data.nla_tracks))
    for t in arm.animation_data.nla_tracks:
        print("    nla:", t.name, "strips:", [s.action.name if s.action else None for s in t.strips])
    if arm.animation_data.action:
        print("  active action:", arm.animation_data.action.name)

print("=== MESHES ===")
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
print("  meshes:", len(meshes))
for o in meshes[:5]:
    print("   ", o.name, "verts:", len(o.data.vertices))

print("=== RENDER ===")
print("  engine:", bpy.context.scene.render.engine)
print("  scene fps:", bpy.context.scene.render.fps, "frame_start/end:", bpy.context.scene.frame_start, bpy.context.scene.frame_end)
