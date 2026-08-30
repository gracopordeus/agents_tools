r"""Render one Mixamo FBX as an 8-direction, fixed-phase reference catalog item.

This script runs inside Blender and deliberately renders every imported mesh.
That means the selected character's skin, clothing, sword, shield and other
attached props remain visible in the reference.  It does not retarget or
replace the character with a mannequin.

Usage::

    blender --background --python /home/ggnp/tools/generation/blender_render_catalog.py -- \
      --fbx mixamo/catalog/characters/warrior/Fast\ Run.fbx \
      --out artifacts/mixamo_catalog/jobs/warrior__fast_run/cells
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_mixamo_props import _attach, _find_bone, add_placeholder_props

ROWS = ["w", "nw", "e", "ne", "n", "sw", "s", "se"]
TARGETS = [(-1, 0), (-1, 1), (1, 0), (1, 1), (0, 1), (-1, -1), (0, -1), (1, -1)]
DEFAULT_ELEV = 35.264
DEFAULT_AZIM = 45.0


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="Render one Mixamo FBX for the catalog")
    parser.add_argument("--fbx", required=True)
    parser.add_argument("--character-fbx", help="skin/mesh FBX used with an animation-only FBX")
    parser.add_argument("--out", required=True)
    parser.add_argument("--character", default="unknown")
    parser.add_argument("--animation", default="unknown")
    parser.add_argument("--action", help="Força uma ação específica do FBX (ex.: UAL multi-ação)")
    parser.add_argument("--elev", type=float, default=DEFAULT_ELEV)
    parser.add_argument("--azim", type=float, default=DEFAULT_AZIM)
    parser.add_argument("--cell", type=int, default=256)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--phases", type=int, default=8)
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument(
        "--props",
        choices=("none", "sword_shield"),
        default="none",
        help="procedural placeholder equipment attached to Mixamo hand bones",
    )
    parser.add_argument("--weapon-fbx", help="FBX externo de arma (ex.: greatsword Quaternius) anexado à mão direita")
    parser.add_argument("--weapon-scale", type=float, default=0.25,
                        help="escala da arma externa em relação ao mesh importado")
    parser.add_argument("--weapon-yaw", type=float, default=0.0,
                        help="cant (rotação ao redor do eixo da lâmina) da arma externa, em graus")
    parser.add_argument("--weapon-grip", type=float, default=0.13,
                        help="distância (mundo) do cabo em direção à palma da mão (0 = no pulso)")
    return parser.parse_args(argv)


def set_render_defaults(
    scene: bpy.types.Scene,
    cell: int,
    fps: int,
    include_studio_lights: bool = True,
    engine: str | None = None,
) -> None:
    if engine:
        scene.render.engine = engine
    else:
        try:
            scene.render.engine = "BLENDER_EEVEE_NEXT"
        except TypeError:
            scene.render.engine = "BLENDER_EEVEE"
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.resolution_x = cell
    scene.render.resolution_y = cell
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    world = bpy.data.worlds.new("mixamo_catalog_empty_world")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background is not None:
        background.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        background.inputs["Strength"].default_value = 0.0
    scene.world = world

    if not include_studio_lights:
        return
    lights = (
        ("key", math.radians(52), math.radians(35), 3.0),
        ("fill", math.radians(52), math.radians(220), 1.0),
        ("rim", math.radians(80), math.radians(140), 1.2),
    )
    for name, elev, azim, energy in lights:
        data = bpy.data.lights.new(name, "SUN")
        data.energy = energy
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        obj.rotation_euler = (elev, 0.0, azim)


def import_source(path: Path, character_path: Path | None = None) -> tuple[bpy.types.Object, bpy.types.Action | None]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if character_path is not None and not character_path.is_file():
        raise FileNotFoundError(character_path)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(character_path or path))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    if not armatures:
        raise RuntimeError("FBX has no armature")
    arm = next((obj for obj in armatures if obj.name == "Armature"), armatures[0])

    action = arm.animation_data.action if arm.animation_data else None
    if character_path is not None:
        # Pack downloads contain one large skinned character FBX and many
        # animation-only FBXs. Import the motion, transfer its Action to the
        # character armature, then remove the temporary animation objects.
        before = set(bpy.data.objects)
        actions_before = set(bpy.data.actions)
        bpy.ops.import_scene.fbx(filepath=str(path))
        imported = [obj for obj in bpy.data.objects if obj not in before]
        imported_arms = [obj for obj in imported if obj.type == "ARMATURE"]
        animation_arm = imported_arms[0] if imported_arms else None
        action = animation_arm.animation_data.action if animation_arm and animation_arm.animation_data else None
        if action is None:
            new_actions = [candidate for candidate in bpy.data.actions if candidate not in actions_before]
            if new_actions:
                action = max(new_actions, key=lambda candidate: candidate.frame_range[1] - candidate.frame_range[0])
        if action is not None:
            if arm.animation_data is None:
                arm.animation_data_create()
            arm.animation_data.action = action
        for obj in imported:
            bpy.data.objects.remove(obj, do_unlink=True)

    for obj in bpy.data.objects:
        if obj.type == "MESH":
            # Mixamo imports a Beta_Joints helper mesh for some uploads.  It is
            # calibration geometry, not part of the character appearance.
            obj.hide_render = "beta_joints" in obj.name.casefold()

    action = arm.animation_data.action if arm.animation_data else action
    if action is None and bpy.data.actions:
        action = max(bpy.data.actions, key=lambda candidate: candidate.frame_range[1] - candidate.frame_range[0])
        if arm.animation_data is None:
            arm.animation_data_create()
        arm.animation_data.action = action
    return arm, action


def action_range(action: bpy.types.Action | None, scene: bpy.types.Scene) -> tuple[int, int]:
    if action is None:
        # A T-pose/static FBX must not accidentally expand to Blender's default
        # 250-frame scene range; it is one visual pose repeated in the sheet.
        return int(scene.frame_start), int(scene.frame_start)
    start = max(1, math.floor(action.frame_range[0]))
    end = max(start, math.ceil(action.frame_range[1]))
    return start, end


def _mesh_signature(arm: bpy.types.Object, scene: bpy.types.Scene, frame: int) -> list:
    """Vértices do mesh deformado, normalizados pela posição do osso raiz (Hips).

    Remove o root motion para que poses iguais em frames diferentes tenham
    assinatura próxima — é a métrica confiável de similaridade de pose.
    """
    scene.frame_set(frame)
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ae = arm.evaluated_get(dg)
    hips = find_hips(ae)
    origin = ae.matrix_world @ hips.matrix.translation if hips else None
    points: list = []
    for mesh in body_meshes():
        evaluated = mesh.evaluated_get(dg)
        for vertex in evaluated.data.vertices:
            point = mesh.matrix_world @ vertex.co
            if origin is not None:
                point = point - origin
            points.append(point)
    return points


def pose_distance(arm: bpy.types.Object, scene: bpy.types.Scene,
                  frame_a: int, frame_b: int) -> float:
    """Distância média (mesh) entre duas poses — 0 = poses idênticas."""
    first = _mesh_signature(arm, scene, frame_a)
    last = _mesh_signature(arm, scene, frame_b)
    if not first or len(first) != len(last):
        return float("inf")
    total = sum((a - b).length for a, b in zip(first, last))
    return total / len(first)


def find_cycle(arm: bpy.types.Object, scene: bpy.types.Scene,
               start: int, end: int, min_period: int = 6,
               threshold: float = 0.08) -> int | None:
    """Retorna o período (frames) do ciclo de pose, ou None se não houver loop.

    Se o último frame repete o primeiro (seam), o período é end-1. Senão, busca
    o menor período em que a pose do frame inicial se repete.
    """
    if end <= start:
        return None
    seam = pose_distance(arm, scene, start, end)
    if seam <= threshold:
        return end - start if end - start >= min_period else None
    best_period, best_diff = None, float("inf")
    for period in range(min_period, end - start):
        diff = pose_distance(arm, scene, start, start + period)
        if diff < best_diff:
            best_diff, best_period = diff, period
    return best_period if best_period is not None and best_diff <= threshold else None


def phase_frames(start: int, end: int, phases: int, looping: bool = False) -> list[int]:
    """Frames amostrados. Para loop, exclui o frame "seam" (end) e distribui as
    fases sobre start..end-1, de modo que a fase 8 fecha no ciclo de verdade."""
    if phases <= 0:
        raise ValueError("phases must be positive")
    if start == end:
        return [start] * phases
    if looping:
        end_eff = max(start + 1, end - 1)
        step = (end_eff - start) / max(phases - 1, 1)
        return [start + round(i * step) for i in range(phases)]
    step = max(1, (end - start + phases - 1) // phases)
    return [start + i * step for i in range(phases)]


def body_meshes() -> list[bpy.types.Object]:
    return [
        obj for obj in bpy.data.objects
        if obj.type == "MESH" and not obj.hide_render and "beta_joints" not in obj.name.casefold()
    ]


def find_hips(arm: bpy.types.Object) -> bpy.types.PoseBone | None:
    candidates = ("hips", "mixamorig:hips", "mixamorig1:hips", "pelvis")
    for bone in arm.pose.bones:
        normalized = bone.name.casefold().replace("_", "").replace(" ", "")
        if normalized in {candidate.replace(":", "").casefold() for candidate in candidates}:
            return bone
    for bone in arm.pose.bones:
        if "hip" in bone.name.casefold() or "pelvis" in bone.name.casefold():
            return bone
    return None


def evaluated_hips(arm: bpy.types.Object, scene: bpy.types.Scene) -> tuple[Vector, object]:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    arm_eval = arm.evaluated_get(depsgraph)
    hips = find_hips(arm_eval)
    if hips is not None:
        world = arm_eval.matrix_world @ hips.matrix.translation
        return world, depsgraph
    meshes = body_meshes()
    total = Vector((0.0, 0.0, 0.0))
    count = 0
    for mesh in meshes:
        evaluated = mesh.evaluated_get(depsgraph)
        for vertex in evaluated.data.vertices:
            total += mesh.matrix_world @ vertex.co
            count += 1
    return total / max(count, 1), depsgraph


def bounds_over_frames(
    arm: bpy.types.Object,
    scene: bpy.types.Scene,
    frames: list[int],
    on_frame=None,
) -> tuple[Vector, Vector]:
    mins = Vector((1e12, 1e12, 1e12))
    maxs = Vector((-1e12, -1e12, -1e12))
    arm.location = (0.0, 0.0, 0.0)
    for frame in frames:
        scene.frame_set(frame)
        if on_frame is not None:
            on_frame()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        for mesh in body_meshes():
            evaluated = mesh.evaluated_get(depsgraph)
            for vertex in evaluated.data.vertices:
                point = mesh.matrix_world @ vertex.co
                mins.x = min(mins.x, point.x)
                mins.y = min(mins.y, point.y)
                mins.z = min(mins.z, point.z)
                maxs.x = max(maxs.x, point.x)
                maxs.y = max(maxs.y, point.y)
                maxs.z = max(maxs.z, point.z)
    if mins.x > maxs.x:
        raise RuntimeError("FBX has no renderable character mesh")
    return mins, maxs


def make_camera(scene: bpy.types.Scene, elev: float, azim: float, height: float, extent: float) -> bpy.types.Object:
    data = bpy.data.cameras.new("mixamo_catalog_camera")
    data.type = "ORTHO"
    camera = bpy.data.objects.new("mixamo_catalog_camera", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    # Aponta para o meio vertical do personagem (não para o chão): com os pés
    # ancorados em z=0, o corpo ocupa 0..height e o centro fica em height/2.
    # Sem isso, cabeça/arma acima da moldura eram cortadas.
    target = Vector((0.0, 0.0, height / 2.0))
    distance = max(height * 2.0, 1.0)
    camera.location = target + Vector((
        distance * math.cos(azim) * math.cos(elev),
        distance * math.sin(azim) * math.cos(elev),
        distance * math.sin(elev),
    ))
    camera.rotation_mode = "QUATERNION"
    camera.rotation_quaternion = (target - camera.location).to_track_quat("-Z", "Y")
    camera.data.ortho_scale = max(extent, height) * 1.9
    return camera


def yaw_for_target(forward: Vector, camera: bpy.types.Object, target: tuple[int, int]) -> float:
    right = camera.matrix_world.to_3x3() @ Vector((1.0, 0.0, 0.0))
    up = camera.matrix_world.to_3x3() @ Vector((0.0, 1.0, 0.0))
    tx, ty = target
    target_length = max(math.hypot(tx, ty), 1.0)
    best = (0.0, 1e12)
    for index in range(3600):
        yaw = math.radians(index / 10.0)
        rotated = Vector((
            forward.x * math.cos(yaw) - forward.y * math.sin(yaw),
            forward.x * math.sin(yaw) + forward.y * math.cos(yaw),
            0.0,
        ))
        screen_x = rotated.x * right.x + rotated.y * right.y
        screen_y = rotated.x * up.x + rotated.y * up.y
        error = abs(screen_x / target_length - tx / target_length) + abs(screen_y / target_length - ty / target_length)
        if error < best[1]:
            best = (yaw, error)
    return best[0]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def import_external_weapon(scene: bpy.types.Scene, armature: bpy.types.Object,
                           weapon_path: str, scale: float = 0.25,
                           yaw: float = 0.0, grip: float = -0.04) -> dict:
    """Importa um FBX de arma externo (ex.: greatsword Quaternius) na mão direita.

    A convenção de alinhamento é a mesma dos props procedurais: o eixo +Z local
    da arma (lâmina) segue o +Y da mão. Presume arma modelada com a lâmina ao
    longo do +Z e o pegador próximo à origem. ``yaw`` canta a lâmina ao redor do
    próprio eixo (um gume para frente); ``grip`` (positivo) desloca o modelo para
    baixo ao longo da lâmina para que a mão segure o cabo.
    """
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=weapon_path)
    imported = [obj for obj in bpy.data.objects if obj not in before]
    meshes = [obj for obj in imported if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"weapon FBX sem meshes: {weapon_path}")
    root = bpy.data.objects.new("weapon_external", None)
    scene.collection.objects.link(root)
    yaw_rad = math.radians(yaw)
    for obj in meshes:
        # A lâmina do modelo fica ao longo do +Z local; o cant (yaw) gira ao
        # redor desse eixo, aplicado direto na matriz do mesh (assado).
        if yaw_rad:
            world_matrix = Matrix.Rotation(yaw_rad, 4, "Z") @ obj.matrix_world
        else:
            world_matrix = obj.matrix_world.copy()
        obj.parent = root
        obj.matrix_parent_inverse = root.matrix_world.inverted()
        obj.matrix_world = world_matrix
    # Parent no armature (OBJECT), não num osso: o matrix_basis de osso-pai fica
    # degenerado. A orientação é dirigida por frame no loop de render.
    root.parent = armature
    root.parent_type = "OBJECT"
    root.matrix_parent_inverse = Matrix.Identity(4)
    bone = _find_bone(armature, "Right")
    return {"name": "weapon_external", "bone": bone, "scale": scale,
            "yaw": yaw, "grip": grip, "source": weapon_path,
            "meshes": [obj.name for obj in meshes]}


def drive_weapon(arm: bpy.types.Object, scene: bpy.types.Scene, metadata: dict | None) -> None:
    """Posiciona a arma na mão direita a cada frame (estado validado).

    A lâmina (+Z do modelo) fica PARA CIMA no mundo, com ``yaw`` cantando ao redor
    do próprio eixo. ``grip`` (positivo) desloca o modelo para baixo ao longo da
    lâmina, de modo que a mão segure o cabo (a origem do FBX fica ``grip*scale``
    abaixo da mão). Usa somente posições de ossos (válidas mesmo com a escala 0.01).
    """
    if not metadata:
        return
    root = bpy.data.objects.get(metadata.get("name"))
    if root is None:
        return
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    ae = arm.evaluated_get(dg)
    bone = ae.pose.bones.get(metadata.get("bone"))
    if bone is None:
        return
    hand = ae.matrix_world @ bone.matrix.translation

    scale = float(metadata.get("scale", 0.25))
    grip = float(metadata.get("grip", 0.13))
    up = Vector((0.0, 0.0, 1.0))
    root_pos = hand - up * (grip * scale)
    rot = up.to_track_quat("Z", "Y").to_matrix().to_4x4()
    cant = Matrix.Rotation(math.radians(metadata.get("yaw", 0.0)), 4, "Z")
    scale_m = Matrix.Diagonal((scale,) * 3 + (1.0,))
    root.matrix_world = Matrix.Translation(root_pos) @ rot @ cant @ scale_m


def main() -> int:
    args = parse_args()
    fbx = Path(args.fbx).expanduser().resolve()
    character_fbx = Path(args.character_fbx).expanduser().resolve() if args.character_fbx else None
    output = Path(args.out).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.rows < 1 or args.rows > len(ROWS):
        raise ValueError(f"rows must be between 1 and {len(ROWS)}")

    arm, action = import_source(fbx, character_fbx)
    if args.action:
        matching = [candidate for candidate in bpy.data.actions if candidate.name == args.action]
        if not matching:
            raise RuntimeError(f"Ação '{args.action}' não encontrada no FBX")
        action = matching[0]
        if arm.animation_data is None:
            arm.animation_data_create()
        arm.animation_data.action = action
    scene = bpy.context.scene
    props_metadata = add_placeholder_props(arm, args.props, scene)
    weapon_metadata = None
    if args.weapon_fbx:
        weapon_metadata = import_external_weapon(scene, arm, args.weapon_fbx,
                                                 args.weapon_scale, args.weapon_yaw,
                                                 args.weapon_grip)
    source_fps = scene.render.fps
    set_render_defaults(scene, args.cell, args.fps)
    start, end = action_range(action, scene)
    cycle_period = find_cycle(arm, scene, start, end)
    looping = cycle_period is not None
    if looping:
        phases = phase_frames(start, start + cycle_period, args.phases, looping=True)
    else:
        phases = phase_frames(start, end, args.phases)
    bounds = bounds_over_frames(arm, scene, phases)
    mins, maxs = bounds
    height = maxs.z - mins.z
    extent = max(maxs.x - mins.x, maxs.y - mins.y)
    camera = make_camera(scene, math.radians(args.elev), math.radians(args.azim), height, extent)

    scene.frame_set(start)
    arm.location = (0.0, 0.0, 0.0)
    first_hips, _ = evaluated_hips(arm, scene)
    scene.frame_set(end)
    last_hips, _ = evaluated_hips(arm, scene)
    forward = Vector((last_hips.x - first_hips.x, last_hips.y - first_hips.y, 0.0))
    if forward.length < 1e-4:
        forward = Vector((0.0, -1.0, 0.0))
    else:
        forward.normalize()
    ground = mins.z

    row_names = ROWS[:args.rows]
    for row, target in enumerate(TARGETS[:args.rows]):
        arm.rotation_mode = "XYZ"
        arm.rotation_euler[2] = yaw_for_target(forward, camera, target)
        for column, frame in enumerate(phases):
            arm.location = (0.0, 0.0, 0.0)
            scene.frame_set(frame)
            hips, _ = evaluated_hips(arm, scene)
            arm.location = (-hips.x, -hips.y, -ground)
            scene.frame_set(frame)
            drive_weapon(arm, scene, weapon_metadata)
            path = output / f"row{row}_col{column}.png"
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            print(f"RENDER row={row_names[row]} phase={column} frame={frame} file={path.name}")

    meshes = [obj.name for obj in body_meshes()]
    metadata = {
        "schema": "mixamo_render/v1",
        "created_at": utc_now(),
        "source_fbx": str(fbx),
        "character_fbx": str(character_fbx) if character_fbx else None,
        "character": args.character,
        "animation": args.animation,
        "action": action.name if action is not None else None,
        "armature": arm.name,
        "meshes_rendered": meshes,
        "skin_and_props_preserved": True,
        "props": props_metadata,
        "weapon": weapon_metadata,
        "frame_range": [start, end],
        "looping": looping,
        "cycle_period": cycle_period,
        "sampled_frames": phases,
        "source_fps": source_fps,
        "output_fps": args.fps,
        "directions": row_names,
        "phases": args.phases,
        "cell": [args.cell, args.cell],
        "camera": {"type": "ORTHO", "elevation": args.elev, "azimuth": args.azim},
        "root_motion_removed": True,
        "transparent_background": True,
        "bounds": {"min": list(mins), "max": list(maxs)},
    }
    (output / "render_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"RENDER_OK rows={args.rows} phases={args.phases} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
