"""Renderiza o run do Mixamo em 64 células (8 direções x 8 fases) com o
personagem completo (materiais/texturas originais).

Modo de uso (headless):
    blender --background --python /home/ggnp/tools/generation/blender_render_run.py [-- opções]

Opções (após o "--"):
    --elev <graus>   Elevação da câmera (padrão 35.264 = isométrico 2:1)
    --azim <graus>   Azimute da câmera (padrão 45)
    --cell <px>      Resolução da célula renderizada (padrão 256)
    --rows <N>       Número de linhas/direções a renderizar (padrão 8)
    --out <dir>      Diretório de saída (padrão artifacts/run_template_cells)

Gera row{r}_col{c}.png (RGBA, transparente). Rows 0..7 = W, NW, E, NE, N, SW, S, SE
(ordem do prompt WASD). Câmera ortográfica isométrica fixa; o personagem é
rotacionado em Y para cada direção e recentrado por frame no centro dos quadris
(removendo root motion). Iluminação fixa de 3 pontos.
"""
import argparse
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

from path_config import ASSET_ROOT, PROJECT_ROOT

FBX = str(ASSET_ROOT / "sources/mixamo/Fast Run_manequin.fbx")  # manequim (padrão)
OUT = PROJECT_ROOT / "artifacts/run_template_cells"
ROWS = ["w", "nw", "e", "ne", "n", "sw", "s", "se"]          # ordem WASD
TARGETS = [(-1, 0), (-1, 1), (1, 0), (1, 1), (0, 1), (-1, -1), (0, -1), (1, -1)]
ELEV = math.radians(35.264)                                   # isométrico 2:1
AZIM = math.radians(45.0)
CELL = 256
FRAME_A, FRAME_B = 1, 16                                      # loop útil (16 fases)
PHASES = [FRAME_A + 2 * k for k in range(8)]                  # 8 fases igualmente espaçadas


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(description="Render do run do Mixamo em células.")
    p.add_argument("--elev", type=float, default=math.degrees(ELEV))
    p.add_argument("--azim", type=float, default=math.degrees(AZIM))
    p.add_argument("--cell", type=int, default=CELL)
    p.add_argument("--rows", type=int, default=8)
    p.add_argument("--fbx", default=FBX)
    p.add_argument("--phases", type=int, default=8,
                   help="Número de fases por direção (ex.: 12)")
    p.add_argument("--loop", type=int, default=0,
                   help="Comprimento do loop em frames (0 = Fast Run 16); ex.: Running.fbx usa 42")
    p.add_argument("--out", default=str(OUT))
    return p.parse_args(argv)


def body_meshes():
    """Todos os meshes do personagem (exclui marcadores 'Beta_Joints')."""
    return [o for o in bpy.data.objects
            if o.type == "MESH" and o.name != "Beta_Joints"]


def setup_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=FBX)
    for ob in bpy.data.objects:
        if ob.type == "MESH":
            ob.hide_render = ob.name == "Beta_Joints"         # oculta marcadores
    arm = bpy.data.objects["Armature"]
    arm.rotation_mode = "XYZ"
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.resolution_x = CELL
    scene.render.resolution_y = CELL
    # iluminação fixa de 3 pontos (mundo preto, sem ambient)
    world = bpy.data.worlds.new("empty")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0, 0, 0, 1)
    scene.world = world
    suns = [
        ("key", math.radians(52), math.radians(35), 3.0),
        ("fill", math.radians(52), math.radians(220), 1.0),
        ("rim", math.radians(80), math.radians(140), 1.2),
    ]
    for name, elev, azim, energy in suns:
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        obj = bpy.data.objects.new(name, light)
        scene.collection.objects.link(obj)
        obj.rotation_euler = (elev, 0, azim)
    return arm


def evaluated(arm, scene, frame):
    scene.frame_set(frame)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    arm_eval = arm.evaluated_get(dg)
    hips = arm_eval.pose.bones.get("Hips") or arm_eval.pose.bones.get("mixamorig1:Hips")
    if hips is not None:
        hip_world = arm_eval.matrix_world @ hips.matrix.translation
        return arm_eval, hip_world.xy, dg
    c = Vector((0, 0))
    n = 0
    for mesh in body_meshes():
        me = mesh.evaluated_get(dg)
        for v in me.data.vertices:
            c += (mesh.matrix_world @ v.co).xy
            n += 1
    return arm_eval, c / max(n, 1), dg


def char_bounds(arm, scene, frames):
    mins = Vector((1e9, 1e9, 1e9))
    maxs = Vector((-1e9, -1e9, -1e9))
    for f in frames:
        scene.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        for mesh in body_meshes():
            me = mesh.evaluated_get(dg)
            for v in me.data.vertices:
                p = mesh.matrix_world @ v.co
                for i in range(3):
                    mins[i] = min(mins[i], p[i]); maxs[i] = max(maxs[i], p[i])
    return mins, maxs


def make_camera(scene, target, dist):
    cam_data = bpy.data.cameras.new("iso_cam")
    cam_data.type = "ORTHO"
    cam = bpy.data.objects.new("iso_cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.location = target + Vector((dist * math.cos(AZIM) * math.cos(ELEV),
                                    dist * math.sin(AZIM) * math.cos(ELEV),
                                    dist * math.sin(ELEV)))
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (target - cam.location).to_track_quat("-Z", "Y")
    return cam


def screen_axes(cam):
    m = cam.matrix_world.to_3x3()
    return m @ Vector((1, 0, 0)), m @ Vector((0, 1, 0))


def yaw_for_target(forward, cam_right, cam_up, target):
    tx, ty = target
    tn = math.hypot(tx, ty)
    best = (None, 1e9)
    for deg in range(0, 3600):
        yaw = math.radians(deg / 10.0)
        f = Vector((forward.x * math.cos(yaw) - forward.y * math.sin(yaw),
                    forward.x * math.sin(yaw) + forward.y * math.cos(yaw), 0))
        sx = f.x * cam_right.x + f.y * cam_right.y
        sy = f.x * cam_up.x + f.y * cam_up.y
        err = abs(sx / tn - tx / tn) + abs(sy / tn - ty / tn)
        if err < best[1]:
            best = (yaw, err)
    return best[0]


def main():
    global ELEV, AZIM, CELL, OUT, FBX, FRAME_B, PHASES
    args = parse_args()
    ELEV = math.radians(args.elev)
    AZIM = math.radians(args.azim)
    CELL = args.cell
    OUT = Path(args.out)
    FBX = args.fbx
    if args.loop > 0:
        FRAME_B = args.loop
    if args.phases > 0:
        n = args.phases
        PHASES = [1 + round(k * (FRAME_B - 1) / max(n - 1, 1)) for k in range(n)]
    n_rows = min(args.rows, len(ROWS))

    OUT.mkdir(parents=True, exist_ok=True)
    arm = setup_scene()
    scene = bpy.context.scene

    mins, maxs = char_bounds(arm, scene, PHASES)
    char_h = maxs.z - mins.z
    dist = char_h * 2.0
    cam = make_camera(scene, Vector((0, 0, 0)), dist)
    ortho_scale = max(maxs.x - mins.x, maxs.y - mins.y, char_h) * 1.6
    cam.data.ortho_scale = ortho_scale
    cam_right, cam_up = screen_axes(cam)

    _, h0, _ = evaluated(arm, scene, FRAME_A)
    _, h1, _ = evaluated(arm, scene, FRAME_B)
    forward = (h1 - h0).normalized()
    if forward.length < 1e-4:
        forward = Vector((0, -1))

    # mínimo Z do ciclo = chão (bounce vertical preservado acima dele)
    ground = mins.z

    for r, (dname, target) in enumerate(zip(ROWS[:n_rows], TARGETS[:n_rows])):
        yaw = yaw_for_target(forward, cam_right, cam_up, target)
        arm.rotation_euler[2] = yaw
        for c, f in enumerate(PHASES):
            arm.location = (0, 0, 0)                      # mede hips sem offset
            _, hips, _ = evaluated(arm, scene, f)
            arm.location = (-hips.x, -hips.y, -ground)   # centra por frame (remove root motion)
            scene.frame_set(f)
            out_path = OUT / f"row{r}_col{c}.png"
            scene.render.filepath = str(out_path)
            bpy.ops.render.render(write_still=True)
            print(f"  row{r}({dname}) col{c} frame{f} -> {out_path.name}")
    print(f"RENDER_OK (elev={math.degrees(ELEV):.1f}° azim={math.degrees(AZIM):.1f}° cell={CELL})")


if __name__ == "__main__":
    main()
