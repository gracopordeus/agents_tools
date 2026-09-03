"""Blender worker for VFX frame sequence renders (asset_type=vfx, representation=frame_sequence).

Renders an animated mesh/material as a frame sequence for particle effects,
hazard overlays, portal glows, etc.

Usage:
    blender --background --python blender_vfx_sequence.py -- --request request.json

Request JSON:
    {
        "mesh_path": "path/to/vfx.glb",
        "output": "path/to/output_dir",
        "render_profile": { ... tile_reference_v1 ... },
        "variant_id": "hazard_ice_glow",
        "asset_key": "hazard_surface",
        "frame_start": 0,
        "frame_end": 7,
        "fps": 10
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


def import_mesh(mesh_path: str) -> bpy.types.Object:
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
    return imported[0]


def setup_camera(
    elevation: float = 80.0,
    azimuth: float = 45.0,
    ortho_scale: float = 1.0,
) -> bpy.types.Object:
    data = bpy.data.cameras.new("vfx_camera")
    data.type = "ORTHO"
    data.ortho_scale = ortho_scale
    camera = bpy.data.objects.new("vfx_camera", data)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera

    elevation_rad = math.radians(elevation)
    azimuth_rad = math.radians(azimuth)
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
    light_data = bpy.data.lights.new("vfx_light", "SUN")
    light_data.energy = 3.0
    light_data.color = (1.0, 0.98, 0.95)
    light = bpy.data.objects.new("vfx_light", light_data)
    bpy.context.scene.collection.objects.link(light)
    light.location = (2.0, -2.0, 5.0)
    light.rotation_mode = "QUATERNION"
    target = Vector((0.0, 0.0, 0.0))
    light.rotation_quaternion = (target - light.location).to_track_quat("-Z", "Y")


def render_frame_sequence(
    scene: bpy.types.Scene,
    output_dir: Path,
    cell_size: list[int],
    frame_start: int,
    frame_end: int,
) -> list[dict]:
    scene.render.resolution_x = cell_size[0]
    scene.render.resolution_y = cell_size[1]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.render.engine = "BLENDER_EEVEE"
    scene.frame_start = frame_start
    scene.frame_end = frame_end

    frames = []
    for frame_num in range(frame_start, frame_end + 1):
        scene.frame_set(frame_num)
        output_path = output_dir / f"frame_{frame_num:04d}.png"
        scene.render.filepath = str(output_path)
        bpy.ops.render.render(write_still=True)
        frames.append({
            "frame": frame_num,
            "path": str(output_path),
        })
    return frames


def assemble_gif(
    frames: list[dict],
    output_dir: Path,
    fps: int,
) -> Path:
    try:
        from PIL import Image
    except ImportError:
        meta_path = output_dir / "sequence_meta.json"
        meta_path.write_text(json.dumps({"frames": frames, "note": "PIL não disponível"}, indent=2))
        return meta_path

    images = []
    for frame in frames:
        img = Image.open(frame["path"])
        images.append(img)
    gif_path = output_dir / "sequence.gif"
    if images:
        images[0].save(
            gif_path,
            save_all=True,
            append_images=images[1:],
            duration=int(1000 / fps),
            loop=0,
        )
    return gif_path


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
    frame_start = request.get("frame_start", 0)
    frame_end = request.get("frame_end", 7)
    fps = request.get("fps", 10)
    variant_id = request.get("variant_id", "unknown")
    asset_key = request.get("asset_key", "unknown")

    clear_scene()
    obj = import_mesh(mesh_path)

    center = Vector((0.0, 0.0, 0.0))
    bbox_min = Vector((float("inf"),) * 3)
    bbox_max = Vector((float("-inf"),) * 3)
    for corner in obj.bound_box:
        world = obj.matrix_world @ Vector(corner)
        for i in range(3):
            bbox_min[i] = min(bbox_min[i], world[i])
            bbox_max[i] = max(bbox_max[i], world[i])
    center_xy = Vector(((bbox_min.x + bbox_max.x) / 2.0, (bbox_min.y + bbox_max.y) / 2.0, 0.0))
    obj.location -= center_xy
    obj.location.z -= bbox_min.z

    camera = setup_camera(
        elevation=profile.get("camera_elevation", 80.0),
        azimuth=profile.get("camera_azimuth", 45.0),
        ortho_scale=profile.get("ortho_scale", 1.0),
    )
    setup_lighting()

    scene = bpy.context.scene
    frames = render_frame_sequence(scene, output, cell_size, frame_start, frame_end)
    gif_path = assemble_gif(frames, output, fps)

    metadata = {
        "variant_id": variant_id,
        "asset_key": asset_key,
        "cell_size": cell_size,
        "frame_start": frame_start,
        "frame_end": frame_end,
        "fps": fps,
        "total_frames": len(frames),
        "frames": frames,
        "gif": str(gif_path),
    }
    (output / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
