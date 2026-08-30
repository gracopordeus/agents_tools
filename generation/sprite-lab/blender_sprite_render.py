"""Blender worker for deterministic 8xN Sprite Lab renders."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

SPRITE_LAB = Path(__file__).resolve().parent
GENERATION = SPRITE_LAB.parent
sys.path.insert(0, str(SPRITE_LAB))
sys.path.insert(0, str(GENERATION))

import blender_render_catalog as brc  # noqa: E402
import render_profile  # noqa: E402
from direction_contract import (  # noqa: E402
    DIRECTION_CONTRACT,
    DIRECTION_ROWS,
    DIRECTION_TARGETS,
    ordered_subset,
)
from blender_semantic_preview import (  # noqa: E402
    action_range,
    apply_animation,
    attach_components,
    attach_weapon,
    find_armature,
    import_asset,
    root_motion_lock_metadata,
    update_two_hand_components,
)
from blender_conditioning_export import (  # noqa: E402
    _configure_depth_compositor,
    _convert_depth_exr,
    _convert_depth_render_result,
    _render_depth_material,
    _material,
    _render_with_overrides,
    _render_vertex_segmentation,
    _role,
    ROLE_COLORS,
    _write_skeleton,
    _write_pose_heatmap,
)


# Keep the public row contract independent from the camera target ordering.
# Direction semantics are represented by the row number in the sprite sheet.
ROWS = list(DIRECTION_ROWS)


def request_paths() -> tuple[Path, Path]:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    request = None
    for index, value in enumerate(argv):
        if value == "--request" and index + 1 < len(argv):
            request = Path(argv[index + 1])
    if request is None:
        raise SystemExit("uso: --request request.json")
    return request, Path(str(request) + ".result.json")


def create_render_root(scene: bpy.types.Scene) -> bpy.types.Object:
    """Create a neutral pivot so direction changes do not fight FBX Actions."""
    root = bpy.data.objects.new("sprite_render_root", None)
    scene.collection.objects.link(root)
    top_level = [
        obj for obj in bpy.data.objects
        if obj.type in {"ARMATURE", "MESH"} and obj.parent is None
    ]
    for obj in top_level:
        world = obj.matrix_world.copy()
        obj.parent = root
        obj.matrix_world = world
    component_roots = [
        obj for obj in bpy.data.objects
        if obj.type == "EMPTY"
        and obj.name.startswith("sprite_component_")
        and obj.parent is None
    ]
    for obj in component_roots:
        world = obj.matrix_world.copy()
        obj.parent = root
        obj.matrix_world = world
    return root


def render_mesh_wireframe(scene: bpy.types.Scene, path: Path) -> None:
    """Render the evaluated character meshes as a clean wireframe pass."""
    material = bpy.data.materials.get("__generation_mesh_white")
    if material is None:
        material = bpy.data.materials.new("__generation_mesh_white")
        material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
        material.use_nodes = True
        shader = material.node_tree.nodes.get("Principled BSDF")
        if shader:
            shader.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
            shader.inputs["Roughness"].default_value = 1.0
    changed = []
    modifiers = []
    try:
        for obj in scene.objects:
            if obj.type != "MESH" or not obj.visible_get():
                continue
            modifier = obj.modifiers.new("__generation_mesh_wireframe", "WIREFRAME")
            modifier.thickness = 0.012
            modifier.use_replace = True
            modifiers.append((obj, modifier))
            original = [slot.material for slot in obj.material_slots]
            changed.append((obj, original))
            for slot in obj.material_slots:
                slot.material = material
            if not obj.material_slots:
                obj.data.materials.append(material)
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
    finally:
        for obj, original in changed:
            if original:
                for slot, value in zip(obj.material_slots, original):
                    slot.material = value
            else:
                obj.data.materials.clear()
        for obj, modifier in modifiers:
            obj.modifiers.remove(modifier)


def render_mesh_lineart(
    scene: bpy.types.Scene,
    objects: list[bpy.types.Object],
    path: Path,
) -> None:
    """Render visible mesh contours with Blender Freestyle, without fills."""
    previous_freestyle = scene.render.use_freestyle
    scene.render.use_freestyle = True
    view_layer = scene.view_layers[0]
    freestyle = view_layer.freestyle_settings
    lineset = freestyle.linesets[0]
    linestyle = lineset.linestyle
    if linestyle is None:
        linestyle = bpy.data.linestyles.new("__generation_lineart")
        lineset.linestyle = linestyle
    fill = bpy.data.materials.get("__generation_lineart_fill")
    if fill is None:
        fill = bpy.data.materials.new("__generation_lineart_fill")
    fill.use_nodes = True
    fill_nodes = fill.node_tree.nodes
    fill_links = fill.node_tree.links
    fill_nodes.clear()
    fill_shader = fill_nodes.new("ShaderNodeEmission")
    fill_shader.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    fill_shader.inputs["Strength"].default_value = 0.0
    fill_output = fill_nodes.new("ShaderNodeOutputMaterial")
    fill_links.new(fill_shader.outputs["Emission"], fill_output.inputs["Surface"])
    original_materials = {}
    previous = {
        "silhouette": lineset.select_silhouette,
        "crease": lineset.select_crease,
        "border": lineset.select_border,
        "material": lineset.select_material_boundary,
        "external": lineset.select_external_contour,
        "color": tuple(linestyle.color),
        "thickness": linestyle.thickness,
    }
    try:
        for obj in objects:
            original_materials[obj.name] = [slot.material for slot in obj.material_slots]
            if obj.material_slots:
                for slot in obj.material_slots:
                    slot.material = fill
            else:
                obj.data.materials.append(fill)
        lineset.select_silhouette = True
        lineset.select_crease = True
        lineset.select_border = True
        lineset.select_material_boundary = True
        lineset.select_external_contour = True
        linestyle.color = (1.0, 1.0, 1.0)
        linestyle.thickness = 1.0
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        import OpenImageIO as oiio  # type: ignore

        input_image = oiio.ImageInput.open(str(path))
        if input_image is None:
            raise RuntimeError(f"não foi possível ler o lineart: {path}")
        try:
            spec = input_image.spec()
            pixels = np.asarray(input_image.read_image(oiio.FLOAT), dtype=np.float32)
        finally:
            input_image.close()
        rgb = pixels[:, :, :3]
        intensity = np.max(rgb, axis=2)
        line = np.clip(np.round(intensity * 255.0), 0.0, 255.0).astype(np.uint8)
        rgba = np.zeros((spec.height, spec.width, 4), dtype=np.uint8)
        rgba[:, :, :3] = 255
        rgba[:, :, 3] = line
        output_spec = oiio.ImageSpec(spec.width, spec.height, 4, oiio.UINT8)
        output = oiio.ImageOutput.create(str(path))
        if output is None:
            raise RuntimeError(f"não foi possível criar o lineart: {path}")
        try:
            output.open(str(path), output_spec)
            output.write_image(rgba)
        finally:
            output.close()
    finally:
        for obj in objects:
            original = original_materials.get(obj.name, [])
            if original:
                for slot, value in zip(obj.material_slots, original):
                    slot.material = value
            else:
                obj.data.materials.clear()
        lineset.select_silhouette = previous["silhouette"]
        lineset.select_crease = previous["crease"]
        lineset.select_border = previous["border"]
        lineset.select_material_boundary = previous["material"]
        lineset.select_external_contour = previous["external"]
        linestyle.color = previous["color"]
        linestyle.thickness = previous["thickness"]
        scene.render.use_freestyle = previous_freestyle


def make_locked_camera(
    scene: bpy.types.Scene,
    elevation: float,
    azimuth: float,
    ortho_scale: float,
    foot_anchor: list[float] | list[int],
    cell_size: list[float] | list[int],
) -> bpy.types.Object:
    """Project world ground origin onto an exact, reusable pixel anchor."""
    data = bpy.data.cameras.new("sprite_locked_camera")
    data.type = "ORTHO"
    camera = bpy.data.objects.new("sprite_locked_camera", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    configure_locked_camera(camera, elevation, azimuth, ortho_scale, foot_anchor, cell_size)
    return camera


def configure_locked_camera(
    camera: bpy.types.Object,
    elevation: float,
    azimuth: float,
    ortho_scale: float,
    foot_anchor: list[float] | list[int],
    cell_size: list[float] | list[int],
) -> None:
    """Update a locked camera after a dynamic cell-size decision."""
    elevation_rad = math.radians(elevation)
    azimuth_rad = math.radians(azimuth)
    up_z = max(0.001, math.cos(elevation_rad))
    anchor_from_center = (foot_anchor[1] / cell_size[1]) - 0.5
    target_z = anchor_from_center * ortho_scale / up_z
    target = Vector((0.0, 0.0, target_z))
    distance = max(ortho_scale * 2.0, 1.0)
    camera.data.ortho_scale = ortho_scale
    camera.location = target + Vector((
        distance * math.cos(azimuth_rad) * math.cos(elevation_rad),
        distance * math.sin(azimuth_rad) * math.cos(elevation_rad),
        distance * math.sin(elevation_rad),
    ))
    camera.rotation_mode = "QUATERNION"
    camera.rotation_quaternion = (target - camera.location).to_track_quat("-Z", "Y")


def projected_camera_bounds(
    camera: bpy.types.Object,
) -> tuple[float, float, float, float]:
    """Return evaluated mesh bounds on the camera's local X and Y axes."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    inverse_camera = camera.matrix_world.inverted()
    minimum_x = float("inf")
    maximum_x = float("-inf")
    minimum_y = float("inf")
    maximum_y = float("-inf")
    for mesh in brc.body_meshes():
        evaluated = mesh.evaluated_get(depsgraph)
        if evaluated.type != "MESH" or not evaluated.bound_box:
            continue
        for corner in evaluated.bound_box:
            world = evaluated.matrix_world @ Vector(corner)
            camera_space = inverse_camera @ world
            minimum_x = min(minimum_x, camera_space.x)
            maximum_x = max(maximum_x, camera_space.x)
            minimum_y = min(minimum_y, camera_space.y)
            maximum_y = max(maximum_y, camera_space.y)
    if minimum_x == float("inf"):
        raise RuntimeError("não foi possível projetar os meshes para ajuste dinâmico")
    return minimum_x, maximum_x, minimum_y, maximum_y


def apply_dynamic_fit(
    scene: bpy.types.Scene,
    camera: bpy.types.Object,
    render_root: bpy.types.Object,
    profile: dict,
    resolution: int,
) -> dict[str, object]:
    """Shift the root only when projected content overflows X or Y."""
    minimum_x, maximum_x, minimum_y, maximum_y = projected_camera_bounds(camera)
    offset_x = 0.0
    offset_y = 0.0
    if profile.get("dynamic_x", False):
        try:
            offset_x = render_profile.horizontal_fit_offset(
                minimum_x,
                maximum_x,
                ortho_scale=float(camera.data.ortho_scale),
                cell_size=profile["cell_size"],
                margin_px=float(profile.get("horizontal_margin_px", 1.0)),
            )
        except ValueError as exc:
            raise RuntimeError(f"ajuste X dinâmico não comporta a célula: {exc}") from exc
    if profile.get("dynamic_y", False):
        try:
            offset_y = render_profile.vertical_fit_offset(
                minimum_y,
                maximum_y,
                ortho_scale=float(camera.data.ortho_scale),
                cell_size=profile["cell_size"],
                margin_px=float(profile.get("vertical_margin_px", 1.0)),
            )
        except ValueError as exc:
            raise RuntimeError(f"ajuste Y dinâmico não comporta a célula: {exc}") from exc
    if abs(offset_x) > 1e-9 or abs(offset_y) > 1e-9:
        axes = camera.matrix_world.to_3x3()
        right = axes @ Vector((1.0, 0.0, 0.0))
        up = axes @ Vector((0.0, 1.0, 0.0))
        right.normalize()
        up.normalize()
        render_root.location += right * offset_x + up * offset_y
    view_width = float(camera.data.ortho_scale) * (
        scene.render.resolution_x / max(scene.render.resolution_y, 1)
    )
    offset_x_pixels = offset_x * resolution / max(view_width, 1e-9)
    offset_y_pixels = offset_y * resolution / max(float(camera.data.ortho_scale), 1e-9)
    return {
        "offset_world": {"x": float(offset_x), "y": float(offset_y)},
        "offset_pixels": {"x": float(offset_x_pixels), "y": float(-offset_y_pixels)},
        "projected_bounds": {
            "minimum_x": float(minimum_x),
            "maximum_x": float(maximum_x),
            "minimum_y": float(minimum_y),
            "maximum_y": float(maximum_y),
        },
    }


def position_render_root(
    scene: bpy.types.Scene,
    armature: bpy.types.Object,
    render_root: bpy.types.Object,
    frame: int,
    direction_yaw: float,
    ground: float,
) -> None:
    """Place one pose at the canonical ground/hip origin before projection."""
    armature.location = (0.0, 0.0, 0.0)
    render_root.location = (0.0, 0.0, 0.0)
    render_root.rotation_euler[2] = direction_yaw
    scene.frame_set(frame)
    update_two_hand_components(armature)
    hips, _ = brc.evaluated_hips(armature, scene)
    render_root.location = (-hips.x, -hips.y, -ground)
    scene.frame_set(frame)


def configure_sprite_lighting(
    scene: bpy.types.Scene,
    render_root: bpy.types.Object,
    request: dict,
    camera: bpy.types.Object,
) -> dict[str, object]:
    """Add the single sober studio key exactly at the active camera viewpoint."""
    try:
        intensity = float(request.get("light_intensity", 3.0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("configuração de luz inválida") from exc
    origin = [float(value) for value in camera.matrix_world.translation]
    if len(origin) != 3 or not all(math.isfinite(value) for value in origin) or not math.isfinite(intensity) or intensity <= 0.0:
        raise RuntimeError("configuração de luz inválida")
    data = bpy.data.lights.new("sprite_key_light", "AREA")
    # Keep the key restrained: the camera-facing area already fills the
    # visible surface, so a high wattage would flatten the material response.
    data.energy = intensity * 60.0
    data.color = (1.0, 0.93, 0.82)
    data.shape = "DISK"
    data.size = max(1.0, float(camera.data.ortho_scale) * 0.55)
    if hasattr(data, "use_shadow"):
        data.use_shadow = True
    light = bpy.data.objects.new("sprite_key_light", data)
    scene.collection.objects.link(light)
    light.location = Vector(origin)
    light.rotation_mode = "QUATERNION"
    light.rotation_quaternion = camera.rotation_quaternion.copy()

    return {
        "preset": str(request.get("light_preset", "default")),
        "type": "AREA",
        "origin": origin,
        "intensity": intensity,
        "energy": float(data.energy),
        "size": float(data.size),
        "origin_mode": "camera",
        "follows_render_root": False,
    }


def fit_uniform_cell(
    scene: bpy.types.Scene,
    armature: bpy.types.Object,
    render_root: bpy.types.Object,
    camera: bpy.types.Object,
    direction_yaws: list[float],
    phases: list[int],
    ground: float,
    profile: dict,
    base_resolution: int,
) -> tuple[dict, int, dict[str, object] | None]:
    """Fit one uniform cell while preserving the profile's pixel density."""
    if profile.get("cell_size_mode", "fixed") != "fit":
        return profile, base_resolution, None
    maximum_width = 0.0
    maximum_height = 0.0
    for direction_yaw in direction_yaws:
        for frame in phases:
            position_render_root(scene, armature, render_root, frame, direction_yaw, ground)
            current_min_x, current_max_x, current_min_y, current_max_y = projected_camera_bounds(camera)
            # Each frame gets its own dynamic translation at render time. Do
            # not combine minima/maxima from mutually exclusive poses, or the
            # camera would be enlarged for an envelope that no cell contains.
            maximum_width = max(maximum_width, current_max_x - current_min_x)
            maximum_height = max(maximum_height, current_max_y - current_min_y)
    padding_px = max(
        float(profile.get("horizontal_margin_px", 1.0)),
        float(profile.get("vertical_margin_px", 1.0)),
    )
    fitted_size, fitted_ortho = render_profile.fitted_cell_size(
        maximum_width,
        maximum_height,
        base_cell_size=base_resolution,
        ortho_scale=float(profile["ortho_scale"]),
        quantum=int(profile.get("cell_size_quantum", 16)),
        padding_px=padding_px,
    )
    if fitted_size > 2048:
        raise RuntimeError(
            f"célula dinâmica excede o limite de 2048 px: {fitted_size}"
        )
    scale = fitted_size / base_resolution
    effective_profile = {
        **profile,
        "cell_size": [fitted_size, fitted_size],
        "ortho_scale": fitted_ortho,
        "foot_anchor": [
            float(profile["foot_anchor"][0]) * scale,
            float(profile["foot_anchor"][1]) * scale,
        ],
    }
    configure_locked_camera(
        camera,
        float(effective_profile["camera_elevation"]),
        float(effective_profile["camera_azimuth"]),
        float(effective_profile["ortho_scale"]),
        effective_profile["foot_anchor"],
        effective_profile["cell_size"],
    )
    scene.render.resolution_x = fitted_size
    scene.render.resolution_y = fitted_size
    render_root.location = (0.0, 0.0, 0.0)
    return effective_profile, fitted_size, {
        "mode": "fit",
        "base_cell": [base_resolution, base_resolution],
        "effective_cell": [fitted_size, fitted_size],
        "base_ortho_scale": float(profile["ortho_scale"]),
        "effective_ortho_scale": float(fitted_ortho),
        "maximum_projected_span_world": {
            "x": float(maximum_width),
            "y": float(maximum_height),
        },
        "padding_px": padding_px,
        "quantum": int(profile.get("cell_size_quantum", 16)),
    }


def fit_ortho_scale(
    scene: bpy.types.Scene,
    armature: bpy.types.Object,
    render_root: bpy.types.Object,
    camera: bpy.types.Object,
    direction_yaws: list[float],
    phases: list[int],
    ground: float,
    profile: dict,
    resolution: int,
) -> tuple[dict, dict[str, object] | None]:
    """Optimize the camera scale while keeping the requested cell dimensions."""
    if profile.get("ortho_scale_mode", "fixed") != "fit":
        return profile, None
    maximum_width = 0.0
    maximum_height = 0.0
    for direction_yaw in direction_yaws:
        for frame in phases:
            position_render_root(scene, armature, render_root, frame, direction_yaw, ground)
            current_min_x, current_max_x, current_min_y, current_max_y = projected_camera_bounds(camera)
            maximum_width = max(maximum_width, current_max_x - current_min_x)
            maximum_height = max(maximum_height, current_max_y - current_min_y)
    safety_px = 0.5
    optimized = render_profile.optimized_ortho_scale(
        maximum_width,
        maximum_height,
        cell_size=profile["cell_size"],
        minimum_ortho_scale=float(profile["ortho_scale"]),
        horizontal_margin_px=float(profile.get("horizontal_margin_px", 1.0)),
        vertical_margin_px=float(profile.get("vertical_margin_px", 1.0)),
        safety_px=safety_px,
    )
    effective_profile = {**profile, "ortho_scale": float(optimized)}
    configure_locked_camera(
        camera,
        float(effective_profile["camera_elevation"]),
        float(effective_profile["camera_azimuth"]),
        float(effective_profile["ortho_scale"]),
        effective_profile["foot_anchor"],
        effective_profile["cell_size"],
    )
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    render_root.location = (0.0, 0.0, 0.0)
    return effective_profile, {
        "mode": "fit",
        "cell": list(profile["cell_size"]),
        "base_ortho_scale": float(profile["ortho_scale"]),
        "effective_ortho_scale": float(optimized),
        "maximum_projected_span_world": {
            "x": float(maximum_width),
            "y": float(maximum_height),
        },
        "horizontal_margin_px": float(profile.get("horizontal_margin_px", 1.0)),
        "vertical_margin_px": float(profile.get("vertical_margin_px", 1.0)),
        "safety_px": safety_px,
    }


def main() -> int:
    request_path, result_path = request_paths()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    output = Path(request["output"]).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = max(1, min(len(ROWS), int(request.get("rows", 8))))
    phases_count = max(1, min(32, int(request.get("phases", 8))))
    resolution = max(128, int(request.get("resolution", 256)))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    import_asset(Path(request["character_path"]).expanduser().resolve())
    armature = find_armature()
    if armature is None:
        raise RuntimeError("mesh principal sem armature; não é possível renderizar uma Action")
    action = apply_animation(
        armature,
        Path(request["animation_path"]).expanduser().resolve(),
        request.get("action_name"),
    )
    root_motion_lock = root_motion_lock_metadata(action)
    component_meta = []
    if request.get("components"):
        component_meta = attach_components(request["components"], armature, request)
    elif request.get("weapon_path"):
        component_meta = [
            attach_weapon(
                Path(request["weapon_path"]).expanduser().resolve(), armature, request
            )
        ]
    render_root = create_render_root(scene)

    brc.set_render_defaults(
        scene,
        resolution,
        int(request.get("fps", 10)),
        include_studio_lights=False,
    )
    start, end = brc.action_range(action, scene)
    cycle = brc.find_cycle(armature, scene, start, end) if action else None
    if cycle:
        phases = brc.phase_frames(start, start + cycle, phases_count, looping=True)
    else:
        phases = brc.phase_frames(start, end, phases_count)

    minimum, maximum = brc.bounds_over_frames(
        armature,
        scene,
        phases,
            on_frame=lambda: update_two_hand_components(armature),
    )
    height = max(0.001, maximum.z - minimum.z)
    extent = max(maximum.x - minimum.x, maximum.y - minimum.y)
    render_profile = request.get("render_profile")
    if render_profile:
        camera = make_locked_camera(
            scene,
            float(render_profile["camera_elevation"]),
            float(render_profile["camera_azimuth"]),
            float(render_profile["ortho_scale"]),
            render_profile["foot_anchor"],
            render_profile["cell_size"],
        )
    else:
        camera = brc.make_camera(
            scene,
            math.radians(float(request.get("elevation", 35.264))),
            math.radians(float(request.get("azimuth", 45.0))),
            height,
            extent,
        )

    scene.frame_set(start)
    armature.location = (0.0, 0.0, 0.0)
    first_hips, _ = brc.evaluated_hips(armature, scene)
    scene.frame_set(end)
    last_hips, _ = brc.evaluated_hips(armature, scene)
    root_delta = root_motion_lock.get("delta", [0.0, 0.0, 0.0])
    forward = Vector((float(root_delta[0]), float(root_delta[1]), 0.0))
    if forward.length < 1e-4:
        forward = Vector((last_hips.x - first_hips.x, last_hips.y - first_hips.y, 0.0))
    if forward.length < 1e-4:
        forward = Vector((0.0, -1.0, 0.0))
    else:
        forward.normalize()
    ground = float(render_profile["ground_z"]) if render_profile else minimum.z
    requested_rows = request.get("direction_rows")
    if requested_rows is None:
        row_names = ROWS[:rows]
    elif (
        not isinstance(requested_rows, list)
        or len(requested_rows) != rows
        or len(set(requested_rows)) != len(requested_rows)
        or any(str(row) not in DIRECTION_TARGETS for row in requested_rows)
        or not ordered_subset([str(row) for row in requested_rows])
    ):
        raise RuntimeError("direction_rows inválido para o render")
    else:
        row_names = [str(row) for row in requested_rows]
    direction_yaws = [
        brc.yaw_for_target(forward, camera, DIRECTION_TARGETS[row])
        for row in row_names
    ]
    effective_profile = render_profile
    cell_fit = None
    ortho_fit = None
    if render_profile:
        effective_profile, resolution, cell_fit = fit_uniform_cell(
            scene,
            armature,
            render_root,
            camera,
            direction_yaws,
            phases,
            ground,
            render_profile,
            resolution,
        )
        effective_profile, ortho_fit = fit_ortho_scale(
            scene,
            armature,
            render_root,
            camera,
            direction_yaws,
            phases,
            ground,
            effective_profile,
            resolution,
        )
    lighting = configure_sprite_lighting(scene, render_root, request, camera)
    semantic_objects = [obj for obj in scene.objects if obj.type == "MESH" and obj.visible_get()]
    semantic_materials = {
        role: _material(f"__generation_seg_{role}", color)
        for role, color in ROLE_COLORS.items()
    }
    depth_enabled = bool(request.get("depth", False))
    depth_near, depth_far = (float(value) for value in request.get("depth_range", [0.1, 20.0]))
    if depth_near >= depth_far:
        raise RuntimeError("depth_range inválido")
    depth_mode = None
    cells = []
    dynamic_x = bool(effective_profile and effective_profile.get("dynamic_x", False))
    dynamic_y = bool(effective_profile and effective_profile.get("dynamic_y", False))
    for row, direction_yaw in enumerate(direction_yaws):
        print(f"DIRECTION row={row_names[row]} yaw={math.degrees(direction_yaw):.3f}", flush=True)
        render_root.rotation_mode = "XYZ"
        render_root.rotation_euler[2] = direction_yaw
        for column, frame in enumerate(phases):
            position_render_root(
                scene, armature, render_root, frame, direction_yaw, ground
            )
            dynamic_fit = None
            if dynamic_x or dynamic_y:
                dynamic_fit = apply_dynamic_fit(
                    scene, camera, render_root, effective_profile, resolution
                )
            path = output / f"row{row}_col{column}.png"
            if depth_enabled:
                scene.use_nodes = False
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            segmentation_path = output / "segmentation" / f"row{row}_col{column}.png"
            _render_vertex_segmentation(
                scene,
                semantic_objects,
                {role: semantic_materials[role] for role in ROLE_COLORS},
                segmentation_path,
            )
            mesh_path = output / "mesh" / f"row{row}_col{column}.png"
            render_mesh_wireframe(scene, mesh_path)
            lineart_path = output / "lineart" / f"row{row}_col{column}.png"
            render_mesh_lineart(scene, semantic_objects, lineart_path)
            bones_path = output / "bones" / f"row{row}_col{column}.png"
            bones_meta = _write_skeleton(
                scene, camera, bones_path, resolution, resolution
            )
            heatmap_path = output / "heatmap" / f"row{row}_col{column}.png"
            _write_pose_heatmap(scene, camera, heatmap_path, resolution, resolution)
            cell = {
                "row": row,
                "direction": row_names[row],
                "column": column,
                "frame": frame,
                "path": str(path),
                "segmentation_path": str(segmentation_path),
                "mesh_path": str(mesh_path),
                "lineart_path": str(lineart_path),
                "bones_path": str(bones_path),
                "heatmap_path": str(heatmap_path),
                "bones": bones_meta["bones"],
            }
            if depth_enabled:
                depth_path = output / "depth" / f"row{row}_col{column}.png"
                depth_mode = "material"
                _render_depth_material(
                    scene,
                    semantic_objects,
                    depth_path,
                    depth_near,
                    depth_far,
                )
                cell["depth_path"] = str(depth_path)
                scene.use_nodes = False
            if dynamic_fit is not None:
                offsets_world = dynamic_fit["offset_world"]
                offsets_pixels = dynamic_fit["offset_pixels"]
                projected_bounds = dynamic_fit["projected_bounds"]
                if dynamic_x:
                    cell["horizontal_fit"] = {
                        "offset_world": offsets_world["x"],
                        "offset_pixels": offsets_pixels["x"],
                        "minimum_x": projected_bounds["minimum_x"],
                        "maximum_x": projected_bounds["maximum_x"],
                    }
                if dynamic_y:
                    cell["vertical_fit"] = {
                        "offset_world": offsets_world["y"],
                        "offset_pixels": offsets_pixels["y"],
                        "minimum_y": projected_bounds["minimum_y"],
                        "maximum_y": projected_bounds["maximum_y"],
                    }
                cell["foot_anchor"] = [
                    float(effective_profile["foot_anchor"][0]) + offsets_pixels["x"],
                    float(effective_profile["foot_anchor"][1]) + offsets_pixels["y"],
                ]
            elif effective_profile:
                cell["foot_anchor"] = [
                    float(effective_profile["foot_anchor"][0]),
                    float(effective_profile["foot_anchor"][1]),
                ]
            cells.append(cell)
            print(f"SPRITE row={row_names[row]} phase={column} frame={frame} file={path.name}", flush=True)

    metadata = {
        "schema": "sprite_lab.sprite_render/v1",
        "directions": row_names,
        "direction_contract": DIRECTION_CONTRACT,
        "direction_targets": [
            {
                "row": row,
                "target": list(DIRECTION_TARGETS[row]),
                "yaw_degrees": math.degrees(direction_yaw),
            }
            for row, direction_yaw in zip(row_names, direction_yaws)
        ],
        "render_mode": str(request.get("render_mode", "runtime")),
        "phases": len(phases),
        "sampled_frames": phases,
        "frame_range": [start, end],
        "looping": bool(cycle),
        "cycle_period": cycle,
        "cell": [resolution, resolution],
        "fps": float(request.get("fps", 10)),
        "camera": {
            "type": "ORTHO",
            "preset": (
                effective_profile.get("camera_preset")
                if effective_profile
                else request.get("camera_preset")
            ),
            "elevation": float(request.get("elevation", 35.264)),
            "azimuth": float(request.get("azimuth", 45.0)),
            "ortho_scale": float(camera.data.ortho_scale),
        },
        "depth": {
            "enabled": depth_enabled,
            "mode": depth_mode,
            "range": [depth_near, depth_far],
        },
        "lighting": lighting,
        "horizontal_fit": {
            "enabled": dynamic_x,
            "margin_px": (
                float(effective_profile.get("horizontal_margin_px", 1.0))
                if dynamic_x
                else None
            ),
            "mode": "overflow_only" if dynamic_x else "locked",
        },
        "vertical_fit": {
            "enabled": dynamic_y,
            "margin_px": (
                float(effective_profile.get("vertical_margin_px", 1.0))
                if dynamic_y
                else None
            ),
            "mode": "overflow_only" if dynamic_y else "locked",
        },
        "render_profile": render_profile,
        "effective_render_profile": effective_profile,
        "cell_fit": cell_fit,
        "ortho_fit": ortho_fit,
        "components": component_meta,
        "weapon": next(
            (item for item in component_meta if item.get("role") == "weapon"),
            None,
        ),
        "root_motion_removed": True,
        "root_motion_lock": root_motion_lock,
        "transparent_background": True,
        "bounds": {"min": list(minimum), "max": list(maximum)},
        "cells": cells,
    }
    metadata_path = output / "render_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"SPRITE_RENDER_OK rows={rows} phases={len(phases)} output={output}", flush=True)
    # Background Blender can keep its UI/render resources alive after the
    # Python entry point returns, especially for high-resolution batches.
    # Quit explicitly after the result is durable so the web job is released.
    bpy.ops.wm.quit_blender()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
