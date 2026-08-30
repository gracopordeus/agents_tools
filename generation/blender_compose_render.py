#!/usr/bin/env python3
"""Assembler Blender: monta um composite spec (outfit + arma + escudo +
animação UAL) e renderiza células 8x8 (8 direções x N fases).

Equivalente ao d2ws+COF do Diablo II, mas para o catálogo 3D: o composite spec
(``composite_spec.py``) declara as camadas; este script monta a cena e produz
``row{r}_col{c}.png`` (RGBA transparente) + ``render_metadata.json`` no mesmo
contrato do ``blender_render_catalog``/``build_run_sheet``.

Uso (headless):
    blender --background --python /home/ggnp/tools/generation/blender_compose_render.py -- \
        --spec <composite_spec.json> --out artifacts/composite_cells/<id> [--mode run]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_render_catalog as brc  # noqa: E402
import composite_spec as cspec  # noqa: E402

ROWS = ["w", "nw", "e", "ne", "n", "sw", "s", "se"]
TARGETS = [(-1, 0), (-1, 1), (1, 0), (1, 1), (0, 1), (-1, -1), (0, -1), (1, -1)]
ELEV = 35.264
AZIM = 45.0


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--mode", default="run")
    p.add_argument("--cell", type=int, default=512)
    p.add_argument("--rows", type=int, default=8)
    p.add_argument("--phases", type=int, default=8)
    p.add_argument("--fps", type=int, default=10)
    p.add_argument("--elev", type=float, default=ELEV)
    p.add_argument("--azim", type=float, default=AZIM)
    p.add_argument("--weapon-yaw", type=float, default=0.0,
                   help="giro da arma ao redor do próprio eixo (graus)")
    p.add_argument("--weapon-scale", type=float, default=0.25)
    p.add_argument("--shield-yaw", type=float, default=0.0,
                   help="giro extra do escudo (graus)")
    return p.parse_args(argv)


def find_hand(arm: bpy.types.Object, side: str) -> str:
    """Encontra o osso de mão no rig Quaternius (hand_r / hand_l)."""
    wanted = f"hand_{side[0]}"
    for bone in arm.pose.bones:
        norm = bone.name.casefold().replace("_", "").replace(" ", "")
        if norm == wanted or norm.endswith("hand" + side[0]):
            return bone.name
    raise RuntimeError(f"rig Quaternius sem osso {wanted}")


def attach_to_bone(root: bpy.types.Object, arm: bpy.types.Object, bone_name: str,
                   scene: bpy.types.Scene, align: Matrix, scale: float) -> None:
    """Parenta a raiz a um osso, com matriz de alinhamento e escala."""
    scene.frame_set(scene.frame_start)
    pose_bone = arm.pose.bones[bone_name]
    parent_world = arm.matrix_world @ pose_bone.matrix
    scale_m = Matrix.Diagonal((scale,) * 3 + (1.0,))
    desired = parent_world @ align @ scale_m
    root.parent = arm
    root.parent_type = "BONE"
    root.parent_bone = bone_name
    root.matrix_parent_inverse = parent_world.inverted()
    root.matrix_basis = desired


def import_weapon(scene: bpy.types.Scene, arm: bpy.types.Object, path: str,
                  scale: float, yaw: float, side: str) -> dict:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    imported = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in imported if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"arma sem meshes: {path}")
    root = bpy.data.objects.new("composite_weapon", None)
    scene.collection.objects.link(root)
    root.matrix_world = Matrix.Identity(4)
    yaw_rad = math.radians(yaw)
    for obj in meshes:
        world = obj.matrix_world.copy()
        if yaw_rad:
            world = Matrix.Rotation(yaw_rad, 4, "Z") @ world
        obj.parent = root
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_basis = world
        for material in obj.data.materials:
            if material is None or not material.use_nodes:
                continue
            shader = material.node_tree.nodes.get("Principled BSDF")
            alpha = shader.inputs.get("Alpha") if shader else None
            if alpha is not None and not alpha.is_linked and alpha.default_value < 1.0:
                alpha.default_value = 1.0
    bone = find_hand(arm, side)
    # Arma: lâmina local +Z alinhada ao +Y da mão (direção do antebraço).
    align = Matrix.Rotation(-math.pi / 2.0, 4, "X")
    attach_to_bone(root, arm, bone, scene, align, scale)
    return {"name": root.name, "bone": bone, "hand": side, "meshes": [o.name for o in meshes]}


def import_shield(scene: bpy.types.Scene, arm: bpy.types.Object, path: str,
                  scale: float, yaw: float, side: str) -> dict:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    imported = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in imported if o.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"escudo sem meshes: {path}")
    root = bpy.data.objects.new("composite_shield", None)
    scene.collection.objects.link(root)
    root.matrix_world = Matrix.Identity(4)
    yaw_rad = math.radians(yaw)
    for obj in meshes:
        world = obj.matrix_world.copy()
        if yaw_rad:
            world = Matrix.Rotation(yaw_rad, 4, "Z") @ world
        obj.parent = root
        obj.matrix_parent_inverse = Matrix.Identity(4)
        obj.matrix_basis = world
    bone = find_hand(arm, side)
    # Escudo: rosto no plano XZ do modelo (normal +Y). Alinha o +Z do escudo
    # (altura) ao +Y da mão e deixa a face legível no isométrico.
    align = Matrix.Rotation(-math.pi / 2.0, 4, "X")
    attach_to_bone(root, arm, bone, scene, align, scale)
    return {"name": root.name, "bone": bone, "hand": side, "meshes": [o.name for o in meshes]}


def apply_animation(arm: bpy.types.Object, ual_fbx: str, action_name: str) -> None:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=ual_fbx)
    imported = [o for o in bpy.data.objects if o not in before]
    action = next((a for a in bpy.data.actions if action_name in a.name), None)
    if action is None:
        raise RuntimeError(f"action '{action_name}' não encontrada na UAL")
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = action
    for obj in imported:
        if obj.type == "ARMATURE":
            if obj.animation_data and obj.animation_data.action is None:
                obj.animation_data.action = action
        bpy.data.objects.remove(obj, do_unlink=True)


def main() -> int:
    args = parse_args()
    spec = cspec.load_spec(Path(args.spec))
    anim = spec["animations"][args.mode]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    bpy.ops.import_scene.gltf(filepath=spec["armature"]["path"])
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not arms:
        raise RuntimeError("outfit sem armature")
    arm = arms[0]

    apply_animation(arm, anim["path"], anim["action"])
    brc.set_render_defaults(scene, args.cell, args.fps)

    weapon_meta = None
    if spec.get("weapon"):
        w = spec["weapon"]
        weapon_meta = import_weapon(scene, arm, w["path"], args.weapon_scale,
                                    args.weapon_yaw, w.get("hand", "right"))
    shield_meta = None
    if spec.get("shield"):
        s = spec["shield"]
        shield_meta = import_shield(scene, arm, s["path"], 1.0, args.shield_yaw,
                                    s.get("hand", "left"))

    start, end = brc.action_range(arm.animation_data.action if arm.animation_data else None,
                                  scene)
    cycle = brc.find_cycle(arm, scene, start, end) if hasattr(brc, "find_cycle") else None
    looping = cycle is not None
    if looping:
        phases = brc.phase_frames(start, start + cycle, args.phases, looping=True)
    else:
        phases = brc.phase_frames(start, end, args.phases)

    mins, maxs = brc.bounds_over_frames(arm, scene, phases)
    height = maxs.z - mins.z
    extent = max(maxs.x - mins.x, maxs.y - mins.y)
    camera = brc.make_camera(scene, math.radians(args.elev), math.radians(args.azim),
                             height, extent)

    scene.frame_set(start)
    arm.location = (0.0, 0.0, 0.0)
    h0, _ = brc.evaluated_hips(arm, scene)
    scene.frame_set(end)
    h1, _ = brc.evaluated_hips(arm, scene)
    forward = h1 - h0
    forward.z = 0.0
    if forward.length < 1e-4:
        forward = Vector((0.0, -1.0))
    else:
        forward.normalize()
    ground = mins.z

    camera_fit = brc.fit_camera_to_frames(arm, scene, camera, phases, ground,
                                          forward, None)

    n_rows = min(args.rows, len(ROWS))
    for row, target in enumerate(TARGETS[:n_rows]):
        arm.rotation_mode = "XYZ"
        arm.rotation_euler[2] = brc.yaw_for_target(forward, camera, target)
        for col, frame in enumerate(phases):
            arm.location = (0.0, 0.0, 0.0)
            scene.frame_set(frame)
            hips, _ = brc.evaluated_hips(arm, scene)
            arm.location = (-hips.x, -hips.y, -ground)
            scene.frame_set(frame)
            out_path = out / f"row{row}_col{col}.png"
            scene.render.filepath = str(out_path)
            bpy.ops.render.render(write_still=True)
            print(f"  row{row}({ROWS[row]}) col{col} frame{frame} -> {out_path.name}")

    metadata = {
        "schema": "quaternius_composite_render/v1",
        "spec": spec["id"],
        "composite_spec": str(Path(args.spec).resolve()),
        "mode": args.mode,
        "animation_action": anim["action"],
        "direction_rows": ROWS[:n_rows],
        "row_targets": TARGETS[:n_rows],
        "phases": phases,
        "loop_period": cycle,
        "camera": {"elev": args.elev, "azim": args.azim,
                   "fit": camera_fit},
        "weapon": weapon_meta,
        "shield": shield_meta,
        "cell": [args.cell, args.cell],
        "fps": args.fps,
        "transparent_background": True,
    }
    (out / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    print(f"RENDER_OK rows={n_rows} phases={args.phases} out={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
