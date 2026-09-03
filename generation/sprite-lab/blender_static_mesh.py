"""Blender worker for static prop renders (asset_type=prop_static, representation=sprite_atlas).

Renders a static mesh (no armature, no animation) from one or more directions,
producing a sprite atlas. Used for pillars, ruins, bridges, decorations, etc.

Usage:
    blender --background --python blender_static_mesh.py -- --request request.json

Request JSON:
    {
        "mesh_path": "path/to/prop.glb",
        "output": "path/to/output_dir",
        "render_profile": { ... hero_reference_v1 ... },
        "variant_id": "pillar_intact",
        "asset_key": "pillar",
        "directions": 8,
        "phases": 1
    }
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

SPRITE_LAB = Path(__file__).resolve().parent
GENERATION = SPRITE_LAB.parent
sys.path.insert(0, str(SPRITE_LAB))
sys.path.insert(0, str(GENERATION))

from blender_sprite_render import (  # noqa: E402
    make_locked_camera,
    configure_locked_camera,
    configure_sprite_lighting,
)


def request_paths() -> tuple[Path, Path]:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    request = None
    for index, value in enumerate(argv):
        if value == "--request" and index + 1 < len(argv):
            request = Path(argv[index + 1])
    if request is None:
        raise SystemExit("uso: --request request.json")
    return request, Path(str(request) + ".result.json")


def clear_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)


def import_mesh(mesh_path: str) -> list[bpy.types.Object]:
    filepath = Path(mesh_path).expanduser().resolve()
    if not filepath.exists():
        raise FileNotFoundError(f"mesh não encontrado: {filepath}")
    suffix = filepath.suffix.lower()
    if suffix == ".glb":
        bpy.ops.import_scene.gltf(filepath=str(filepath))
    elif suffix == ".gltf":
        bpy.ops.import_scene.gltf(filepath=str(filepath))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(filepath))
    else:
        raise ValueError(f"formato não suportado: {suffix}")
    imported = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    if not imported:
        raise RuntimeError("nenhum mesh importado")
    return imported


def center_and_ground(objects: list[bpy.types.Object]) -> float:
    bbox_min = Vector((float("inf"),) * 3)
    bbox_max = Vector((float("-inf"),) * 3)
    for obj in objects:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            for i in range(3):
                bbox_min[i] = min(bbox_min[i], world[i])
                bbox_max[i] = max(bbox_max[i], world[i])
    center_xy = Vector(((bbox_min.x + bbox_max.x) / 2.0, (bbox_min.y + bbox_max.y) / 2.0, 0.0))
    for obj in objects:
        obj.location -= center_xy
        obj.location.z -= bbox_min.z
    return bbox_max.z - bbox_min.z


def render_direction_cells(
    scene: bpy.types.Scene,
    camera: bpy.types.Object,
    render_root: bpy.types.Object,
    output_dir: Path,
    cell_size: list[int],
    directions: int,
    phases: int,
) -> list[dict]:
    scene.render.resolution_x = cell_size[0]
    scene.render.resolution_y = cell_size[1]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.engine = "BLENDER_EEVEE"

    angle_step = 360.0 / directions
    cells = []
    for direction in range(directions):
        angle = direction * angle_step
        render_root.rotation_euler = (0.0, 0.0, math.radians(angle))
        for phase in range(phases):
            output_path = output_dir / f"dir_{direction:02d}_phase_{phase:03d}.png"
            scene.render.filepath = str(output_path)
            bpy.ops.render.render(write_still=True)
            cells.append({
                "direction": direction,
                "phase": phase,
                "angle": angle,
                "path": str(output_path),
            })
    return cells


def assemble_atlas(
    cells: list[dict],
    output_dir: Path,
    cell_size: list[int],
    directions: int,
    phases: int,
) -> Path:
    try:
        from PIL import Image
    except ImportError:
        atlas_path = output_dir / "atlas_meta.json"
        atlas_path.write_text(json.dumps({"cells": cells, "note": "PIL não disponível"}, indent=2))
        return atlas_path

    sheet_width = cell_size[0] * phases
    sheet_height = cell_size[1] * directions
    sheet = Image.new("RGBA", (sheet_width, sheet_height), (0, 0, 0, 0))
    for cell in cells:
        cell_img = Image.open(cell["path"])
        x = cell["phase"] * cell_size[0]
        y = cell["direction"] * cell_size[1]
        sheet.paste(cell_img, (x, y))
    atlas_path = output_dir / "spritesheet.png"
    sheet.save(atlas_path)
    return atlas_path


def main() -> int:
    request_path, result_path = request_paths()
    request = json.loads(request_path.read_text(encoding="utf-8"))

    mesh_path = request.get("mesh_path")
    if not mesh_path:
        raise SystemExit("request deve conter 'mesh_path'")

    output = Path(request["output"]).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    profile = request.get("render_profile", {})
    cell_size = profile.get("cell_size", [256, 256])
    directions = request.get("directions", 8)
    phases = request.get("phases", 1)
    variant_id = request.get("variant_id", "unknown")
    asset_key = request.get("asset_key", "unknown")

    clear_scene()
    objects = import_mesh(mesh_path)
    height = center_and_ground(objects)

    scene = bpy.context.scene
    render_root = scene.collection.objects.get("sprite_render_root")
    if render_root is None:
        render_root = bpy.data.objects.new("sprite_render_root", None)
        scene.collection.objects.link(render_root)
    for obj in objects:
        obj.parent = render_root

    camera = make_locked_camera(
        scene,
        elevation=profile.get("camera_elevation", 35.264),
        azimuth=profile.get("camera_azimuth", 45.0),
        ortho_scale=profile.get("ortho_scale", 2.577),
        foot_anchor=profile.get("foot_anchor", [128, 220]),
        cell_size=cell_size,
    )
    configure_sprite_lighting(scene, render_root, request, camera)

    cells = render_direction_cells(scene, camera, render_root, output, cell_size, directions, phases)
    atlas_path = assemble_atlas(cells, output, cell_size, directions, phases)

    metadata = {
        "variant_id": variant_id,
        "asset_key": asset_key,
        "cell_size": cell_size,
        "directions": directions,
        "phases": phases,
        "height": height,
        "cells": cells,
        "atlas": str(atlas_path),
    }
    (output / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
