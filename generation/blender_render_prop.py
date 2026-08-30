"""Renderiza um prop demo (árvore procedural) na câmera isométrica da pipeline.

Uso (headless):
    blender --background --python /home/ggnp/tools/generation/blender_render_prop.py -- [--elev 45] [--azim 45]

Gera artifacts/prop_demo/tree_<elev>.png (RGBA, transparente) e tree_<elev>_black.png
(fundo preto p/ visualização). Mesma convenção de câmera da pipeline de personagens:
ortográfica, elevação configurável, azimute 45°, luz de 3 pontos.
"""
import argparse
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

from path_config import PROJECT_ROOT

DEFAULT_OUT = PROJECT_ROOT / "artifacts/prop_demo"
AZIM = 45.0


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(description="Prop demo (árvore) isométrica.")
    p.add_argument("--elev", type=float, default=30.0)
    p.add_argument("--azim", type=float, default=AZIM)
    p.add_argument("--cell", type=int, default=256)
    p.add_argument("--out", default=str(DEFAULT_OUT))
    return p.parse_args(argv)


def material(name: str, color: tuple):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        if n.type == "BSDF_PRINCIPLED":
            n.inputs["Base Color"].default_value = color + (1.0,)
            return mat
    return mat


def add_mesh(ops_name: str, ops_kwargs: dict, mat):
    getattr(bpy.ops.mesh, ops_name)(**ops_kwargs)
    obj = bpy.context.active_object
    obj.data.materials.append(mat)
    return obj


def setup_tree(scene):
    trunk = material("trunk", (0.38, 0.24, 0.13))
    leaf = material("leaf", (0.18, 0.46, 0.20))
    add_mesh("primitive_cylinder_add", {"radius": 0.32, "depth": 2.6, "location": (0, 0, 1.3)}, trunk)
    add_mesh("primitive_cone_add", {"radius1": 1.7, "radius2": 0.0, "depth": 2.6,
                                    "location": (0, 0, 3.9)}, leaf)
    add_mesh("primitive_uv_sphere_add", {"radius": 1.0, "location": (1.0, 0.4, 3.4)}, leaf)
    add_mesh("primitive_uv_sphere_add", {"radius": 1.15, "location": (-1.0, -0.4, 3.2)}, leaf)

    scene.render.engine = "BLENDER_EEVEE"
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    world = bpy.data.worlds.new("empty")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0, 0, 0, 1)
    scene.world = world
    for name, elev, azim, energy in (("key", math.radians(52), math.radians(35), 3.0),
                                     ("fill", math.radians(52), math.radians(220), 1.0),
                                     ("rim", math.radians(80), math.radians(140), 1.2)):
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        obj = bpy.data.objects.new(name, light)
        scene.collection.objects.link(obj)
        obj.rotation_euler = (elev, 0, azim)


def bounds():
    mins = Vector((1e9, 1e9, 1e9)); maxs = Vector((-1e9, -1e9, -1e9))
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        for v in ob.data.vertices:
            p = ob.matrix_world @ v.co
            for i in range(3):
                mins[i] = min(mins[i], p[i]); maxs[i] = max(maxs[i], p[i])
    return mins, maxs


def main():
    args = parse_args()
    elev = math.radians(args.elev)
    azim = math.radians(args.azim)
    cell = args.cell
    out = Path(args.out)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    setup_tree(scene)
    scene.render.resolution_x = cell
    scene.render.resolution_y = cell

    mins, maxs = bounds()
    char_h = maxs.z - mins.z
    dist = char_h * 2.0
    cam_data = bpy.data.cameras.new("iso_cam")
    cam_data.type = "ORTHO"
    cam = bpy.data.objects.new("iso_cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    target = Vector((0, 0, 0))
    cam.location = target + Vector((dist * math.cos(azim) * math.cos(elev),
                                    dist * math.sin(azim) * math.cos(elev),
                                    dist * math.sin(elev)))
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (target - cam.location).to_track_quat("-Z", "Y")
    cam.data.ortho_scale = max(maxs.x - mins.x, maxs.y - mins.y, char_h) * 1.6

    out.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(out / f"tree_{args.elev:.0f}.png")
    bpy.ops.render.render(write_still=True)
    print(f"RENDER_OK (elev={args.elev}° azim={args.azim}° cell={cell})")


if __name__ == "__main__":
    main()
