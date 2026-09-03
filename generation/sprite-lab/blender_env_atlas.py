"""Blender worker for environment atlas renders.

Renders 8 environment assets from 8 directions each, producing a single
8×8 atlas (2048×2048) where:
  - columns = asset type (floor, wall, doorway, pillar, ruin, bridge, low_cover, rough)
  - rows = direction (0-7, rotating around Z axis)

Usage:
    blender --background --python blender_env_atlas.py -- --request request.json

Request JSON:
    {
        "assets": [
            {
                "col": 0,
                "name": "FloorTile_Basic",
                "fbx_path": "/path/to/FloorTile_Basic.fbx",
                "tile_key": "floor",
                "capabilities": ["walkable"]
            },
            ...
        ],
        "output": "path/to/output_dir",
        "render_profile": { ... tile_reference_v1 with directions=8 ... },
        "atlas_id": "env_atlas_nordic_01"
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
import render_profile  # noqa: E402


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


def import_fbx(fbx_path: str) -> list[bpy.types.Object]:
    filepath = Path(fbx_path).expanduser().resolve()
    if not filepath.exists():
        raise FileNotFoundError(f"FBX não encontrado: {filepath}")
    bpy.ops.import_scene.fbx(filepath=str(filepath))
    imported = list(bpy.context.selected_objects)
    if not imported:
        raise RuntimeError(f"nenhum objeto importado de {filepath.name}")
    return imported


def overwrite_materials(objects: list[bpy.types.Object]) -> None:
    """Replace all materials with simple diffuse BSDF for Eevee compatibility."""
    for obj in objects:
        if obj.type != "MESH" or not obj.data:
            continue
        while obj.data.materials:
            obj.data.materials.pop(index=0)
        mat = bpy.data.materials.new(name="EnvDiffuse")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links
        for n in list(nodes):
            nodes.remove(n)
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (0, 0)
        bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.6, 1.0)
        bsdf.inputs["Alpha"].default_value = 1.0
        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (300, 0)
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
        obj.data.materials.append(mat)


def center_and_ground(objects: list[bpy.types.Object], center_z: bool = False) -> float:
    """Center objects on XY, place bottom at Z=0 (or center vertically). Returns height."""
    mesh_only = [o for o in objects if o.type == "MESH"]
    if not mesh_only:
        return 0.0
    bbox_min = Vector((float("inf"),) * 3)
    bbox_max = Vector((float("-inf"),) * 3)
    for obj in mesh_only:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            for i in range(3):
                bbox_min[i] = min(bbox_min[i], world[i])
                bbox_max[i] = max(bbox_max[i], world[i])
    center_xy = Vector(((bbox_min.x + bbox_max.x) / 2.0, (bbox_min.y + bbox_max.y) / 2.0, 0.0))
    for obj in objects:
        obj.location -= center_xy
        if center_z:
            center_z_offset = (bbox_min.z + bbox_max.z) / 2.0
            obj.location.z -= center_z_offset
        else:
            obj.location.z -= bbox_min.z
    bpy.context.view_layer.update()
    return bbox_max.z - bbox_min.z


def get_object_world_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    """Get bounding box min/max in world space for all mesh objects."""
    mesh_only = [o for o in objects if o.type == "MESH"]
    if not mesh_only:
        return Vector((0, 0, 0)), Vector((0, 0, 0))
    bbox_min = Vector((float("inf"),) * 3)
    bbox_max = Vector((float("-inf"),) * 3)
    for obj in mesh_only:
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            for i in range(3):
                bbox_min[i] = min(bbox_min[i], world[i])
                bbox_max[i] = max(bbox_max[i], world[i])
    return bbox_min, bbox_max


def project_bounds_to_pixels(
    camera: bpy.types.Object,
    bbox_min: Vector,
    bbox_max: Vector,
    ortho_scale: float,
    cell_size: list[int],
) -> tuple[float, float]:
    """Project world bounds to camera space and convert to pixels."""
    inverse_camera = camera.matrix_world.inverted()
    corners = [
        Vector((bbox_min.x, bbox_min.y, bbox_min.z)),
        Vector((bbox_min.x, bbox_min.y, bbox_max.z)),
        Vector((bbox_min.x, bbox_max.y, bbox_min.z)),
        Vector((bbox_min.x, bbox_max.y, bbox_max.z)),
        Vector((bbox_max.x, bbox_min.y, bbox_min.z)),
        Vector((bbox_max.x, bbox_min.y, bbox_max.z)),
        Vector((bbox_max.x, bbox_max.y, bbox_min.z)),
        Vector((bbox_max.x, bbox_max.y, bbox_max.z)),
    ]
    min_x = float("inf")
    max_x = float("-inf")
    min_y = float("inf")
    max_y = float("-inf")
    for corner in corners:
        camera_space = inverse_camera @ corner
        min_x = min(min_x, camera_space.x)
        max_x = max(max_x, camera_space.x)
        min_y = min(min_y, camera_space.y)
        max_y = max(max_y, camera_space.y)
    width_world = max_x - min_x
    height_world = max_y - min_y
    pixels_per_world = cell_size[0] / ortho_scale
    width_px = width_world * pixels_per_world
    height_px = height_world * pixels_per_world
    return width_px, height_px


def scale_object_to_fit(
    objects: list[bpy.types.Object],
    max_dimension_px: float,
    ortho_scale: float,
    cell_size: list[int],
    render_root: bpy.types.Object,
    camera: bpy.types.Object,
    directions: int,
) -> dict:
    """Scale object if any projected dimension exceeds max_dimension_px."""
    bbox_min, bbox_max = get_object_world_bounds(objects)
    if bbox_min.x == float("inf"):
        return {"original_size": [0, 0, 0], "scale_factor": 1.0, "scaled": False}

    angle_step = 360.0 / directions
    max_width_px = 0.0
    max_height_px = 0.0
    for direction in range(directions):
        angle = direction * angle_step
        render_root.rotation_euler = (0.0, 0.0, math.radians(angle))
        bpy.context.view_layer.update()
        width_px, height_px = project_bounds_to_pixels(camera, bbox_min, bbox_max, ortho_scale, cell_size)
        max_width_px = max(max_width_px, width_px)
        max_height_px = max(max_height_px, height_px)

    render_root.rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()

    original_size = [float(bbox_max[i] - bbox_min[i]) for i in range(3)]
    scale_factor = 1.0
    if max_width_px > max_dimension_px or max_height_px > max_dimension_px:
        scale_factor = max_dimension_px / max(max_width_px, max_height_px)

    if scale_factor < 1.0:
        for obj in objects:
            obj.scale *= scale_factor
        bpy.context.view_layer.update()

    return {
        "original_size": original_size,
        "projected_px": [max_width_px, max_height_px],
        "scale_factor": scale_factor,
        "scaled": scale_factor < 1.0,
    }


def projected_camera_bounds_for_root(
    camera: bpy.types.Object,
    root: bpy.types.Object,
) -> tuple[float, float, float, float]:
    """Return evaluated bounds of all root children on camera local X and Y."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    inverse_camera = camera.matrix_world.inverted()
    min_x = float("inf")
    max_x = float("-inf")
    min_y = float("inf")
    max_y = float("-inf")
    for child in root.children:
        evaluated = child.evaluated_get(depsgraph)
        if evaluated.type != "MESH" or not evaluated.bound_box:
            continue
        for corner in evaluated.bound_box:
            world = evaluated.matrix_world @ Vector(corner)
            camera_space = inverse_camera @ world
            min_x = min(min_x, camera_space.x)
            max_x = max(max_x, camera_space.x)
            min_y = min(min_y, camera_space.y)
            max_y = max(max_y, camera_space.y)
    if min_x == float("inf"):
        return 0.0, 0.0, 0.0, 0.0
    return min_x, max_x, min_y, max_y


def optimize_ortho_scale_per_direction(
    camera: bpy.types.Object,
    render_root: bpy.types.Object,
    direction_yaws: list[float],
    profile: dict,
) -> list[dict]:
    """Compute optimal ortho_scale for each direction independently."""
    if profile.get("ortho_scale_mode", "fixed") != "fit":
        return [{"mode": "fixed", "effective_ortho_scale": float(profile["ortho_scale"])}] * len(direction_yaws)

    safety_px = 0.5
    results = []
    for direction_yaw in direction_yaws:
        render_root.rotation_euler = (0.0, 0.0, math.radians(direction_yaw))
        bpy.context.view_layer.update()
        min_x, max_x, min_y, max_y = projected_camera_bounds_for_root(camera, render_root)
        width = max_x - min_x
        height = max_y - min_y

        if width <= 0.0 or height <= 0.0:
            results.append({"mode": "fallback", "effective_ortho_scale": float(profile["ortho_scale"])})
            continue

        optimized = render_profile.optimized_ortho_scale(
            width,
            height,
            cell_size=profile["cell_size"],
            minimum_ortho_scale=float(profile["ortho_scale"]),
            horizontal_margin_px=float(profile.get("horizontal_margin_px", 1.0)),
            vertical_margin_px=float(profile.get("vertical_margin_px", 1.0)),
            safety_px=8.0,
        )
        results.append({
            "mode": "fit",
            "direction_yaw": float(direction_yaw),
            "base_ortho_scale": float(profile["ortho_scale"]),
            "effective_ortho_scale": float(optimized),
            "projected_span_world": {"x": float(width), "y": float(height)},
        })

    render_root.rotation_euler = (0.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    return results


def render_asset_directions(
    scene: bpy.types.Scene,
    camera: bpy.types.Object,
    render_root: bpy.types.Object,
    output_dir: Path,
    cell_size: list[int],
    directions: int,
    asset_name: str,
    profile: dict,
    ortho_per_direction: list[dict] | None = None,
) -> list[dict]:
    """Render one asset from N directions, returning cell metadata."""
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
        bpy.context.view_layer.update()

        if ortho_per_direction and direction < len(ortho_per_direction):
            ortho_info = ortho_per_direction[direction]
            ortho_scale = ortho_info.get("effective_ortho_scale", profile.get("ortho_scale", 2.577))
            configure_locked_camera(
                camera,
                float(profile["camera_elevation"]),
                float(profile["camera_azimuth"]),
                float(ortho_scale),
                profile["foot_anchor"],
                cell_size,
            )
            bpy.context.view_layer.update()

        output_path = output_dir / f"{asset_name}_dir_{direction:02d}.png"
        scene.render.filepath = str(output_path)
        bpy.ops.render.render(write_still=True)
        cells.append({
            "direction": direction,
            "angle": angle,
            "path": str(output_path),
        })
    return cells


def main() -> int:
    request_path, result_path = request_paths()
    request = json.loads(request_path.read_text(encoding="utf-8"))

    assets = request.get("assets", [])
    if not assets:
        raise SystemExit("request deve conter 'assets' (lista de 8 assets)")

    output = Path(request["output"]).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    profile = request.get("render_profile", {})
    optimize_ortho = request.get("optimize_ortho_scale", False)
    if optimize_ortho:
        profile = {**profile, "ortho_scale_mode": "fit"}
    cell_size = profile.get("cell_size", [256, 256])
    directions = request.get("directions", 8)
    atlas_id = request.get("atlas_id", "env_atlas")
    max_dimension_px = request.get("max_dimension_px", 230)

    clear_scene()

    all_asset_results = []
    for asset in assets:
        col = asset["col"]
        name = asset["name"]
        fbx_path = asset["fbx_path"]
        tile_key = asset.get("tile_key", "unknown")
        capabilities = asset.get("capabilities", [])

        print(f"[{col}/7] Rendering {name} ({tile_key})...")

        imported = import_fbx(fbx_path)
        overwrite_materials(imported)
        center_z = profile.get("center_z", False)
        height = center_and_ground(imported, center_z=center_z)

        render_root = bpy.data.objects.new(f"root_{name}", None)
        bpy.context.scene.collection.objects.link(render_root)
        for obj in imported:
            obj.parent = render_root

        camera = make_locked_camera(
            bpy.context.scene,
            elevation=profile.get("camera_elevation", 35.264),
            azimuth=profile.get("camera_azimuth", 45.0),
            ortho_scale=profile.get("ortho_scale", 2.577),
            foot_anchor=profile.get("foot_anchor", [128, 128]),
            cell_size=cell_size,
        )

        fit_ortho = optimize_ortho_scale_per_direction(
            camera, render_root, [i * (360.0 / directions) for i in range(directions)], profile,
        )

        configure_sprite_lighting(bpy.context.scene, render_root, request, camera)

        asset_output = output / name
        asset_output.mkdir(parents=True, exist_ok=True)

        cells = render_asset_directions(
            bpy.context.scene, camera, render_root, asset_output, cell_size, directions, name,
            profile, fit_ortho,
        )

        all_asset_results.append({
            "col": col,
            "name": name,
            "tile_key": tile_key,
            "capabilities": capabilities,
            "height": height,
            "fbx_path": fbx_path,
            "ortho_per_direction": fit_ortho,
            "cells": cells,
        })

        for obj in list(bpy.context.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for cam in list(bpy.data.cameras):
            if cam.users == 0:
                bpy.data.cameras.remove(cam)
        for light in list(bpy.data.lights):
            if light.users == 0:
                bpy.data.lights.remove(light)

    metadata = {
        "atlas_id": atlas_id,
        "cell_size": cell_size,
        "directions": directions,
        "columns": len(assets),
        "total_cells": len(assets) * directions,
        "assets": all_asset_results,
    }
    (output / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
