"""Blender worker for tile atlas renders (asset_type=tile, representation=tile_atlas).

Renders a tile mesh from a fixed top-down orthographic camera, producing
individual cells that stitch together pixel-perfectly when placed in a grid.

Usage:
    blender --background --python blender_tile_atlas.py -- --request request.json

Request JSON:
    {
        "mesh_path": "path/to/tile.glb",
        "output": "path/to/output_dir",
        "render_profile": { ... tile_reference_v1 ... },
        "variant_id": "floor_snow_clean",
        "tile_key": "floor",
        "directions": 1,
        "phases": 1,
        "orientation_angle": 0.0
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


def import_tile_mesh(mesh_path: str) -> bpy.types.Object:
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
    elif suffix in {".obj", ".blend"}:
        bpy.ops.wm.append(filepath=str(filepath))
    else:
        raise ValueError(f"formato não suportado: {suffix}")
    imported = [obj for obj in bpy.context.selected_objects if obj.type == "MESH"]
    if not imported:
        raise RuntimeError("nenhum mesh importado")
    return imported[0]


def setup_camera(
    profile: dict,
    directions: int = 1,
    orientation_angle: float = 0.0,
) -> bpy.types.Object:
    cell_size = profile.get("cell_size", [256, 256])
    ortho_scale = profile.get("ortho_scale", 1.0)
    elevation = profile.get("camera_elevation", 80.0)
    azimuth = profile.get("camera_azimuth", 45.0)

    data = bpy.data.cameras.new("tile_camera")
    data.type = "ORTHO"
    data.ortho_scale = ortho_scale
    camera = bpy.data.objects.new("tile_camera", data)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera

    elevation_rad = math.radians(elevation)
    azimuth_rad = math.radians(azimuth + orientation_angle)
    distance = max(ortho_scale * 3.0, 5.0)
    camera.location = (
        distance * math.cos(azimuth_rad) * math.cos(elevation_rad),
        distance * math.sin(azimuth_rad) * math.cos(elevation_rad),
        distance * math.sin(elevation_rad),
    )
    camera.rotation_mode = "QUATERNION"
    target = Vector((0.0, 0.0, 0.0))
    camera.rotation_quaternion = (target - camera.location).to_track_quat("-Z", "Y")
    return camera


def setup_lighting() -> None:
    light_data = bpy.data.lights.new("tile_light", "SUN")
    light_data.energy = 3.0
    light_data.color = (1.0, 0.98, 0.95)
    light = bpy.data.objects.new("tile_light", light_data)
    bpy.context.scene.collection.objects.link(light)
    light.location = (2.0, -2.0, 5.0)
    light.rotation_mode = "QUATERNION"
    target = Vector((0.0, 0.0, 0.0))
    light.rotation_quaternion = (target - light.location).to_track_quat("-Z", "Y")


def render_cells(
    scene: bpy.types.Scene,
    camera: bpy.types.Object,
    output_dir: Path,
    cell_size: list[int],
    phases: int = 1,
) -> list[dict]:
    scene.render.resolution_x = cell_size[0]
    scene.render.resolution_y = cell_size[1]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.engine = "BLENDER_EEVEE"

    cells = []
    for phase in range(phases):
        output_path = output_dir / f"phase_{phase:03d}.png"
        scene.render.filepath = str(output_path)
        bpy.ops.render.render(write_still=True)
        cells.append({
            "phase": phase,
            "path": str(output_path),
            "width": cell_size[0],
            "height": cell_size[1],
        })
    return cells


def assemble_spritesheet(
    cells: list[dict],
    output_dir: Path,
    cell_size: list[int],
    phases: int,
) -> Path:
    try:
        from PIL import Image
    except ImportError:
        sheet_path = output_dir / "spritesheet_meta.json"
        sheet_path.write_text(json.dumps({"cells": cells, "note": "PIL não disponível, cells individuais preservados"}, indent=2))
        return sheet_path

    sheet_width = cell_size[0] * phases
    sheet_height = cell_size[1]
    sheet = Image.new("RGBA", (sheet_width, sheet_height), (0, 0, 0, 0))
    for i, cell in enumerate(cells):
        cell_img = Image.open(cell["path"])
        x_offset = i * cell_size[0]
        sheet.paste(cell_img, (x_offset, 0))
    sheet_path = output_dir / "spritesheet.png"
    sheet.save(sheet_path)
    return sheet_path


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
    directions = request.get("directions", 1)
    phases = request.get("phases", 1)
    orientation_angle = request.get("orientation_angle", 0.0)
    variant_id = request.get("variant_id", "unknown")
    tile_key = request.get("tile_key", "unknown")

    clear_scene()

    obj = import_tile_mesh(mesh_path)

    bbox_min = Vector((float("inf"),) * 3)
    bbox_max = Vector((float("-inf"),) * 3)
    for corner in obj.bound_box:
        world = obj.matrix_world @ Vector(corner)
        for i in range(3):
            bbox_min[i] = min(bbox_min[i], world[i])
            bbox_max[i] = max(bbox_max[i], world[i])
    tile_world_size = max(bbox_max.x - bbox_min.x, bbox_max.y - bbox_min.y, bbox_max.z - bbox_min.z)

    center = (bbox_min + bbox_max) / 2.0
    obj.location -= center
    obj.location.z -= bbox_min.z

    camera = setup_camera(profile, directions, orientation_angle)
    setup_lighting()

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"

    cells = render_cells(scene, camera, output, cell_size, phases)
    sheet_path = assemble_spritesheet(cells, output, cell_size, phases)

    metadata = {
        "variant_id": variant_id,
        "tile_key": tile_key,
        "cell_size": cell_size,
        "directions": directions,
        "phases": phases,
        "orientation_angle": orientation_angle,
        "tile_world_size": tile_world_size,
        "cells": cells,
        "spritesheet": str(sheet_path),
    }
    (output / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
