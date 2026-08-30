"""Export deterministic RGB, mask, segmentation, depth and skeleton channels.

Run from Blender 4.x with:

    blender -b scene.blend --python blender_conditioning_export.py -- \
        --request request.json

The request JSON is intentionally small and scene-local. Meshes can declare a
``conditioning_role`` custom property; otherwise the exporter uses conservative
name matching and assigns ``other``.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import bpy  # type: ignore
import numpy as np
from bpy_extras.object_utils import world_to_camera_view  # type: ignore
from mathutils import Vector  # type: ignore


ROLES = (
    "head",
    "torso",
    "arm",
    "hand",
    "leg",
    "weapon",
    "shield",
    "accessory",
    "other",
)
ROLE_COLORS = {
    "head": (231, 76, 60, 255),
    "torso": (241, 196, 15, 255),
    "arm": (46, 204, 113, 255),
    "hand": (52, 152, 219, 255),
    "leg": (155, 89, 182, 255),
    "weapon": (230, 126, 34, 255),
    "shield": (26, 188, 156, 255),
    "accessory": (233, 30, 99, 255),
    "other": (189, 195, 199, 255),
}
NAME_ROLE_HINTS = (
    ("weapon", "weapon"),
    ("sword", "weapon"),
    ("axe", "weapon"),
    ("bow", "weapon"),
    ("shield", "shield"),
    ("head", "head"),
    ("helmet", "head"),
    ("hair", "head"),
    ("torso", "torso"),
    ("body", "torso"),
    ("chest", "torso"),
    ("arm", "arm"),
    ("hand", "hand"),
    ("leg", "leg"),
    ("foot", "leg"),
    ("boot", "leg"),
    ("cape", "accessory"),
    ("belt", "accessory"),
    ("prop", "accessory"),
)


def _args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    return parser.parse_args(argv)


def _load_request(path: Path) -> dict[str, Any]:
    request = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("request deve ser um objeto JSON")
    return request


def _scene_objects() -> list[bpy.types.Object]:
    return [
        obj
        for obj in bpy.context.scene.objects
        if obj.type == "MESH" and obj.visible_get() and obj.get("conditioning_include", True)
    ]


def _role(obj: bpy.types.Object) -> str:
    declared = str(obj.get("conditioning_role", "")).strip().casefold()
    if declared in ROLES:
        return declared
    name = obj.name.casefold()
    for hint, role in NAME_ROLE_HINTS:
        if hint in name:
            return role
    return "other"


def _material(name: str, color: tuple[int, int, int, int]) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    shader = nodes.get("Principled BSDF")
    if shader is None:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Base Color"].default_value = tuple(channel / 255.0 for channel in color)
    shader.inputs["Roughness"].default_value = 1.0
    shader.inputs["Metallic"].default_value = 0.0
    return material


def _configure_scene(scene: bpy.types.Scene, request: dict[str, Any]) -> tuple[int, int]:
    resolution = request.get("resolution", [512, 512])
    width, height = int(resolution[0]), int(resolution[1])
    if width <= 0 or height <= 0:
        raise ValueError("resolution deve ser positiva")
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    camera_name = str(request.get("camera") or "").strip()
    camera = bpy.data.objects.get(camera_name) if camera_name else scene.camera
    if camera is None or camera.type != "CAMERA":
        raise ValueError("a cena precisa de uma câmera válida")
    scene.camera = camera
    return width, height


def _set_render_engine(scene: bpy.types.Scene, requested: str | None) -> str:
    """Set a version-compatible Blender engine and return its actual ID."""
    current = str(scene.render.engine)
    if not requested:
        return current
    engine = requested.strip()
    available = {
        item.identifier
        for item in scene.render.bl_rna.properties["engine"].enum_items
    }
    if engine == "BLENDER_EEVEE_NEXT" and "BLENDER_EEVEE" in available:
        engine = "BLENDER_EEVEE"
    if engine not in available:
        raise ValueError(
            f"engine inválido: {requested}; disponíveis: {', '.join(sorted(available))}"
        )
    scene.render.engine = engine
    return engine


def _render(scene: bpy.types.Scene, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def _render_with_overrides(
    scene: bpy.types.Scene,
    objects: list[bpy.types.Object],
    material_by_role: dict[str, bpy.types.Material],
    path: Path,
) -> None:
    previous: dict[str, list[bpy.types.Material | None]] = {}
    try:
        for obj in objects:
            override = material_by_role[_role(obj)]
            previous[obj.name] = [slot.material for slot in obj.material_slots]
            if obj.material_slots:
                for slot in obj.material_slots:
                    slot.material = override
            else:
                obj.data.materials.append(override)
        _render(scene, path)
    finally:
        for obj in objects:
            original = previous.get(obj.name, [])
            if original:
                for slot, material in zip(obj.material_slots, original):
                    slot.material = material
            else:
                obj.data.materials.clear()


def _render_depth_material(
    scene: bpy.types.Scene,
    objects: list[bpy.types.Object],
    path: Path,
    near: float,
    far: float,
) -> None:
    """Render camera-space depth as a normal aligned image pass."""
    material = bpy.data.materials.get("__generation_depth_material")
    if material is None:
        material = bpy.data.materials.new("__generation_depth_material")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    camera_data = nodes.new("ShaderNodeCameraData")
    mapper = nodes.new("ShaderNodeMapRange")
    mapper.inputs["From Min"].default_value = near
    mapper.inputs["From Max"].default_value = far
    mapper.inputs["To Min"].default_value = 1.0
    mapper.inputs["To Max"].default_value = 0.0
    mapper.clamp = True
    emission = nodes.new("ShaderNodeEmission")
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(camera_data.outputs["View Z Depth"], mapper.inputs["Value"])
    links.new(mapper.outputs["Result"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    previous: dict[str, list[bpy.types.Material | None]] = {}
    try:
        for obj in objects:
            previous[obj.name] = [slot.material for slot in obj.material_slots]
            if obj.material_slots:
                for slot in obj.material_slots:
                    slot.material = material
            else:
                obj.data.materials.append(material)
        _render(scene, path)
    finally:
        for obj in objects:
            original = previous.get(obj.name, [])
            if original:
                for slot, value in zip(obj.material_slots, original):
                    slot.material = value
            else:
                obj.data.materials.clear()


def _bone_role(name: str) -> str:
    normalized = str(name).casefold().replace("_", "")
    if "head" in normalized or "neck" in normalized:
        return "head"
    if any(token in normalized for token in (
        "clavicle", "upperarm", "lowerarm", "hand", "finger",
        "thumb", "index", "middle", "pinky", "ring",
    )):
        return "arm"
    if any(token in normalized for token in ("thigh", "calf", "foot", "ball")):
        return "leg"
    if any(token in normalized for token in ("pelvis", "spine", "torso", "chest")):
        return "torso"
    return "other"


def _object_role(obj: bpy.types.Object) -> str:
    role = _role(obj)
    parent = obj.parent
    while role == "other" and parent is not None:
        parent_name = parent.name.casefold()
        for hint, hinted_role in NAME_ROLE_HINTS:
            if hint in parent_name:
                return hinted_role
        parent = parent.parent
    return role


def _render_vertex_segmentation(
    scene: bpy.types.Scene,
    objects: list[bpy.types.Object],
    material_by_role: dict[str, bpy.types.Material],
    path: Path,
) -> None:
    """Render skinned faces using the semantic role of their dominant bone."""
    original_materials: dict[str, list[bpy.types.Material | None]] = {}
    original_indices: dict[str, list[int]] = {}
    try:
        for obj in objects:
            original_materials[obj.name] = [slot.material for slot in obj.material_slots]
            original_indices[obj.name] = [polygon.material_index for polygon in obj.data.polygons]
            obj.data.materials.clear()
            for role in ROLES:
                obj.data.materials.append(material_by_role[role])
            fallback = _object_role(obj)
            for polygon in obj.data.polygons:
                role = fallback
                if obj.vertex_groups:
                    scores: dict[int, float] = {}
                    for vertex_index in polygon.vertices:
                        for assignment in obj.data.vertices[vertex_index].groups:
                            scores[assignment.group] = scores.get(assignment.group, 0.0) + assignment.weight
                    if scores:
                        group_index = max(scores, key=scores.get)
                        role = _bone_role(obj.vertex_groups[group_index].name)
                polygon.material_index = ROLES.index(role)
        _render(scene, path)
    finally:
        for obj in objects:
            obj.data.materials.clear()
            for material in original_materials.get(obj.name, []):
                obj.data.materials.append(material)
            for polygon, index in zip(obj.data.polygons, original_indices.get(obj.name, [])):
                polygon.material_index = index


def _configure_depth_compositor(
    scene: bpy.types.Scene,
    near: float,
    far: float,
    raw_directory: Path,
) -> dict[str, Any]:
    scene.use_nodes = True
    for view_layer in scene.view_layers:
        view_layer.use_pass_z = True
    tree = getattr(scene, "node_tree", None) or getattr(scene, "compositing_node_group", None)
    if tree is None and hasattr(scene, "compositing_node_group"):
        tree = bpy.data.node_groups.new(
            "__generation_depth_compositor",
            "CompositorNodeTree",
        )
        scene.compositing_node_group = tree
    if tree is None:
        raise RuntimeError("a versão do Blender não expõe uma árvore de composição")
    tree.nodes.clear()
    layers = tree.nodes.new("CompositorNodeRLayers")
    try:
        mapper = tree.nodes.new("CompositorNodeMapRange")
        mapper.inputs["From Min"].default_value = near
        mapper.inputs["From Max"].default_value = far
        mapper.inputs["To Min"].default_value = 1.0
        mapper.inputs["To Max"].default_value = 0.0
        mapper.clamp = True
        composite = tree.nodes.new("CompositorNodeComposite")
        tree.links.new(layers.outputs["Depth"], mapper.inputs["Value"])
        tree.links.new(mapper.outputs["Value"], composite.inputs["Image"])
        return {"mode": "composite", "tree": tree}
    except RuntimeError:
        # Blender 5.2's new compositor has no Map Range or Composite nodes.
        # Export the raw float pass to EXR and map it to a PNG below using the
        # explicit near/far range, keeping depth comparable between frames.
        tree.nodes.clear()
        layers = tree.nodes.new("CompositorNodeRLayers")
        output = tree.nodes.new("CompositorNodeOutputFile")
        raw_directory.mkdir(parents=True, exist_ok=True)
        output.directory = str(raw_directory.resolve())
        output.file_name = "depth"
        item = output.file_output_items.new("FLOAT", "Depth")
        tree.links.new(layers.outputs["Depth"], output.inputs[item.name])
        return {
            "mode": "file",
            "tree": tree,
            "node": output,
            "raw_directory": raw_directory,
        }


def _convert_depth_exr(raw_path: Path, destination: Path, near: float, far: float) -> None:
    """Map a float EXR depth pass to a grayscale PNG using the request range."""
    import OpenImageIO as oiio  # type: ignore

    input_image = oiio.ImageInput.open(str(raw_path))
    if input_image is None:
        raise RuntimeError(f"não foi possível abrir o depth EXR: {raw_path}")
    try:
        spec = input_image.spec()
        pixels = np.asarray(input_image.read_image(oiio.FLOAT), dtype=np.float32)
    finally:
        input_image.close()
    if pixels.ndim == 3:
        depth = pixels[:, :, 0]
    elif pixels.ndim == 2:
        depth = pixels
    else:
        raise RuntimeError(f"formato inesperado no depth EXR: {pixels.shape}")
    mapped = np.where(
        np.isfinite(depth),
        np.clip((far - depth) / (far - near), 0.0, 1.0),
        0.0,
    )
    mapped = np.where(depth >= far, 0.0, mapped)
    # The compositor writes the float pass bottom-up; projected channels use
    # top-down image coordinates.
    mapped = np.flipud(mapped)
    output_spec = oiio.ImageSpec(spec.width, spec.height, 1, oiio.UINT8)
    output = oiio.ImageOutput.create(str(destination))
    if output is None:
        raise RuntimeError(f"não foi possível criar o depth PNG: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.open(str(destination), output_spec)
        output.write_image(np.round(mapped * 255.0).astype(np.uint8))
    finally:
        output.close()


def _convert_depth_render_result(
    destination: Path,
    near: float,
    far: float,
    width: int,
    height: int,
) -> bool:
    """Convert Blender's in-memory Z pass without compositor resampling."""
    render_result = bpy.data.images.get("Render Result")
    if render_result is None or not render_result.layers:
        return False
    depth_pass = next(
        (item for item in render_result.layers[0].passes if item.name.casefold() in {"depth", "z"}),
        None,
    )
    if depth_pass is None:
        return False
    values = np.asarray(depth_pass.rect[:], dtype=np.float32)
    if values.size != width * height * 4:
        return False
    depth = values.reshape((height, width, 4))[:, :, 0]
    mapped = np.where(
        np.isfinite(depth),
        np.clip((far - depth) / (far - near), 0.0, 1.0),
        0.0,
    )
    mapped = np.where(depth >= far, 0.0, mapped)
    mapped = np.flipud(mapped)
    import OpenImageIO as oiio  # type: ignore

    destination.parent.mkdir(parents=True, exist_ok=True)
    spec = oiio.ImageSpec(width, height, 1, oiio.UINT8)
    output = oiio.ImageOutput.create(str(destination))
    if output is None:
        raise RuntimeError(f"não foi possível criar o depth PNG: {destination}")
    try:
        output.open(str(destination), spec)
        output.write_image(np.round(mapped * 255.0).astype(np.uint8))
    finally:
        output.close()
    return True


def _project(camera: bpy.types.Object, scene: bpy.types.Scene, point: Vector, width: int, height: int) -> tuple[int, int] | None:
    projected = world_to_camera_view(scene, camera, point)
    if projected.z <= 0:
        return None
    return round(projected.x * width), round((1.0 - projected.y) * height)


def _draw_line(pixels: np.ndarray, first: tuple[int, int], second: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    height, width, _ = pixels.shape
    x0, y0 = first
    x1, y1 = second
    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    for index in range(steps + 1):
        ratio = index / steps
        x = round(x0 + (x1 - x0) * ratio)
        y = round(y0 + (y1 - y0) * ratio)
        if 0 <= x < width and 0 <= y < height:
            pixels[y, x] = color
            if x + 1 < width:
                pixels[y, x + 1] = color
            if y + 1 < height:
                pixels[y + 1, x] = color


def _write_skeleton(scene: bpy.types.Scene, camera: bpy.types.Object, path: Path, width: int, height: int) -> dict[str, Any]:
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    bones: list[dict[str, Any]] = []
    for obj in scene.objects:
        if obj.type != "ARMATURE" or not obj.visible_get():
            continue
        for bone in obj.pose.bones:
            head = obj.matrix_world @ bone.head
            tail = obj.matrix_world @ bone.tail
            head_px = _project(camera, scene, head, width, height)
            tail_px = _project(camera, scene, tail, width, height)
            item: dict[str, Any] = {"name": bone.name}
            if head_px and tail_px:
                _draw_line(pixels, head_px, tail_px, (255, 255, 255, 255))
                item["head"] = list(head_px)
                item["tail"] = list(tail_px)
            bones.append(item)
    image = bpy.data.images.new(f"conditioning_skeleton_{path.stem}", width=width, height=height, alpha=True)
    # Blender's image buffer is bottom-up while projected pixel coordinates are top-down.
    image.pixels.foreach_set((np.flipud(pixels).astype(np.float32) / 255.0).ravel())
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save()
    bpy.data.images.remove(image)
    return {"bones": bones}


def _write_pose_heatmap(
    scene: bpy.types.Scene,
    camera: bpy.types.Object,
    path: Path,
    width: int,
    height: int,
) -> None:
    """Write a grayscale heatmap around projected joints and bone segments."""
    heat = np.zeros((height, width), dtype=np.float32)

    def add_gaussian(x: float, y: float, radius: float, strength: float) -> None:
        left = max(0, int(x - radius * 3.0))
        right = min(width, int(x + radius * 3.0 + 1))
        top = max(0, int(y - radius * 3.0))
        bottom = min(height, int(y + radius * 3.0 + 1))
        if left >= right or top >= bottom:
            return
        yy, xx = np.ogrid[top:bottom, left:right]
        value = strength * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2.0 * radius ** 2))
        heat[top:bottom, left:right] = np.maximum(heat[top:bottom, left:right], value)

    for obj in scene.objects:
        if obj.type != "ARMATURE" or not obj.visible_get():
            continue
        for bone in obj.pose.bones:
            if bone.name.casefold() == "root":
                continue
            head = _project(camera, scene, obj.matrix_world @ bone.head, width, height)
            tail = _project(camera, scene, obj.matrix_world @ bone.tail, width, height)
            if not head or not tail:
                continue
            add_gaussian(*head, 9.0, 1.0)
            add_gaussian(*tail, 9.0, 0.9)
            for index in range(9):
                ratio = index / 8.0
                add_gaussian(
                    head[0] + (tail[0] - head[0]) * ratio,
                    head[1] + (tail[1] - head[1]) * ratio,
                    4.0,
                    0.75,
                )

    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    values = np.round(np.clip(heat, 0.0, 1.0) * 255.0).astype(np.uint8)
    pixels[:, :, 0] = values
    pixels[:, :, 1] = values
    pixels[:, :, 2] = values
    pixels[:, :, 3] = values
    image = bpy.data.images.new(f"conditioning_heatmap_{path.stem}", width=width, height=height, alpha=True)
    image.pixels.foreach_set((np.flipud(pixels).astype(np.float32) / 255.0).ravel())
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save()
    bpy.data.images.remove(image)


def export(request: dict[str, Any]) -> dict[str, Any]:
    scene = bpy.context.scene
    output_value = str(request.get("output") or "").strip()
    if not output_value:
        raise ValueError("output é obrigatório")
    output = Path(output_value).resolve()
    width, height = _configure_scene(scene, request)
    frame_start = int(request.get("frame_start", scene.frame_start))
    frame_end = int(request.get("frame_end", frame_start))
    frame_step = int(request.get("frame_step", 1))
    if frame_step <= 0 or frame_end < frame_start:
        raise ValueError("intervalo de frames inválido")
    frame_numbers = list(range(frame_start, frame_end + 1, frame_step))
    render_engine = _set_render_engine(scene, request.get("engine"))
    objects = _scene_objects()
    white = _material("__generation_silhouette_white", (255, 255, 255, 255))
    materials = {role: _material(f"__generation_seg_{role}", color) for role, color in ROLE_COLORS.items()}
    camera = scene.camera
    depth_range = request.get("depth_range", [0.0, 100.0])
    depth_near, depth_far = float(depth_range[0]), float(depth_range[1])
    if depth_near >= depth_far:
        raise ValueError("depth_range inválido")
    frame_manifest: list[dict[str, Any]] = []
    for index, frame_number in enumerate(frame_numbers):
        scene.frame_set(frame_number)
        frame_id = f"f{index:02d}"
        beauty = output / "beauty" / f"{frame_id}.png"
        silhouette = output / "silhouette" / f"{frame_id}.png"
        segmentation = output / "segmentation" / f"{frame_id}.png"
        _render(scene, beauty)
        _render_with_overrides(scene, objects, {role: white for role in ROLES}, silhouette)
        _render_with_overrides(scene, objects, materials, segmentation)
        channels = {
            "beauty": f"beauty/{frame_id}.png",
            "silhouette": f"silhouette/{frame_id}.png",
            "segmentation": f"segmentation/{frame_id}.png",
        }
        landmarks = _write_skeleton(scene, camera, output / "skeleton" / f"{frame_id}.png", width, height)
        channels["skeleton"] = f"skeleton/{frame_id}.png"
        (output / "skeleton" / f"{frame_id}.json").write_text(
            json.dumps(landmarks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        frame_manifest.append({"id": frame_id, "index": index, "source_frame": frame_number, "channels": channels})

    # Depth is intentionally opt-in because compositor depth ranges are scene-specific.
    depth_config: dict[str, Any] | None = None
    if request.get("depth", False):
        depth_config = _configure_depth_compositor(
            scene,
            depth_near,
            depth_far,
            output / ".depth-raw",
        )
        for index, frame_number in enumerate(frame_numbers):
            scene.frame_set(frame_number)
            path = output / "depth" / f"f{index:02d}.png"
            if depth_config["mode"] == "file":
                depth_node = depth_config["node"]
                depth_node.directory = str(depth_config["raw_directory"].resolve())
                depth_node.file_name = f"f{index:02d}"
                raw_path = depth_config["raw_directory"] / f"f{index:02d}.exr"
                raw_path.unlink(missing_ok=True)
            _render(scene, path)
            if depth_config["mode"] == "file":
                if not raw_path.is_file():
                    raise RuntimeError(f"depth EXR ausente após renderização: {raw_path}")
                _convert_depth_exr(raw_path, path, depth_near, depth_far)
                raw_path.unlink(missing_ok=True)
            frame_manifest[index]["channels"]["depth"] = f"depth/f{index:02d}.png"

    manifest = {
        "schema": "generation.blender_conditioning_source/v1",
        "project": "generation",
        "source_scene": str(bpy.data.filepath),
        "camera": camera.name,
        "resolution": [width, height],
        "frame_start": frame_start,
        "frame_end": frame_end,
        "frame_step": frame_step,
        "action": str(request.get("action") or "run"),
        "direction": str(request.get("direction") or "r1"),
        "fps": float(request.get("fps", 10.0)),
        "foot_anchor": request.get("foot_anchor", [width // 2, round(height * 0.86)]),
        "profile_id": request.get("profile_id"),
        "engine": render_engine,
        "depth_mode": depth_config["mode"] if depth_config else None,
        "channels": sorted({channel for frame in frame_manifest for channel in frame["channels"]}),
        "frames": frame_manifest,
        "role_colors": ROLE_COLORS,
    }
    (output / "source_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    args = _args()
    result = export(_load_request(args.request))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
