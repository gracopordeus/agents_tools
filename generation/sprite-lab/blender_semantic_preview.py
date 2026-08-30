"""Blender worker for adaptive semantic previews.

It produces independent front/top PNGs and optional animation frames.  It does
not build a spritesheet; the Python orchestrator turns the frames into a GIF.
The camera envelope is calculated from evaluated mesh bounds so props and
characters of different sizes are framed without a fixed cell assumption.
"""
from __future__ import annotations

import json
import math
import re
import struct
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector


BONE_PATH_RE = re.compile(r"pose\.bones\[(?:\"([^\"]+)\"|'([^']+)')\]")
ROOT_BONE_TOKENS = ("root", "hips", "pelvis", "master")
PREVIEW_HELPER_MESHES = {"cube", "icosphere"}

# Blender's glTF importer maps glTF Y-up coordinates to its Z-up scene as
# (x, y, z) -> (x, -z, y).  Component transforms are authored in the web
# viewer's glTF/Three.js space, so they need a basis change before being
# applied to imported Blender objects.
GLTF_TO_BLENDER_BASIS = Matrix(
    (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
)
SOCKET_CORRECTION_CACHE: dict[tuple[str, str], Matrix] = {}


def _three_euler_xyz_matrix(angles: tuple[float, float, float]) -> Matrix:
    """Build the XYZ matrix used by Three.js ``Euler``/``Quaternion``.

    Blender and Three.js both call this order ``XYZ``, but their Euler-to-
    matrix conventions are not interchangeable.  Component rotations are
    authored by the web viewer with Three.js, so preserve that exact matrix
    before converting it to Blender's glTF basis.
    """
    x, y, z = angles
    a, b = math.cos(x), math.sin(x)
    c, d = math.cos(y), math.sin(y)
    e, f = math.cos(z), math.sin(z)
    ae, af = a * e, a * f
    be, bf = b * e, b * f
    return Matrix(
        (
            (c * e, -c * f, d, 0.0),
            (af + be * d, ae - bf * d, -b * c, 0.0),
            (bf - ae * d, be + af * d, a * c, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )
    )


def args() -> tuple[Path, Path]:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    request = None
    for index, value in enumerate(argv):
        if value == "--request" and index + 1 < len(argv):
            request = Path(argv[index + 1])
    if request is None:
        raise SystemExit("uso: --request request.json")
    return request, Path(str(request) + ".result.json")


def import_asset(path: Path) -> list[bpy.types.Object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    before = set(bpy.data.objects)
    extension = path.suffix.casefold()
    if extension == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif extension in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        raise ValueError(f"formato de mesh não suportado pelo preview: {extension}")
    # FBX files from authoring tools often include the default Blender/Unity
    # helpers alongside the real asset.  They are not part of the web model
    # and, when parented to a hand socket, can make a prop appear misaligned.
    for obj in list(bpy.data.objects):
        normalized = _normalized_name(obj.name)
        if obj not in before and (
            obj.type in {"CAMERA", "LIGHT"}
            or (obj.type == "MESH" and normalized in PREVIEW_HELPER_MESHES)
        ):
            bpy.data.objects.remove(obj, do_unlink=True)
    imported = [obj for obj in bpy.data.objects if obj not in before]
    for obj in imported:
        if obj.type != "MESH":
            continue
        for material in obj.data.materials:
            if not material or not material.use_nodes:
                continue
            alpha = material.node_tree.nodes.get("Principled BSDF")
            alpha_input = alpha.inputs.get("Alpha") if alpha else None
            if alpha_input and not alpha_input.links:
                alpha_input.default_value = 1.0
            material.diffuse_color[3] = 1.0
    return imported


def find_armature() -> bpy.types.Object | None:
    return next((obj for obj in bpy.data.objects if obj.type == "ARMATURE"), None)


def _normalized_name(value: str) -> str:
    return str(value or "").replace("_", "").replace(" ", "").casefold()


def _gltf_space(path: Path | str | None) -> bool:
    return Path(str(path or "")).suffix.casefold() in {".glb", ".gltf"}


def _gltf_matrix_to_blender(matrix: Matrix) -> Matrix:
    """Convert a local glTF matrix into the imported Blender basis."""
    inverse = GLTF_TO_BLENDER_BASIS.inverted()
    return GLTF_TO_BLENDER_BASIS @ matrix @ inverse


def _gltf_vector_to_blender(vector: Vector) -> Vector:
    return (GLTF_TO_BLENDER_BASIS @ Vector((*vector, 1.0))).to_3d()


def _gltf_node_matrix(node: dict[str, Any]) -> Matrix:
    raw_matrix = node.get("matrix")
    if isinstance(raw_matrix, list) and len(raw_matrix) == 16:
        # glTF matrices are column-major; mathutils matrices are row-indexed.
        return Matrix(
            (
                tuple(float(raw_matrix[index]) for index in (0, 4, 8, 12)),
                tuple(float(raw_matrix[index]) for index in (1, 5, 9, 13)),
                tuple(float(raw_matrix[index]) for index in (2, 6, 10, 14)),
                tuple(float(raw_matrix[index]) for index in (3, 7, 11, 15)),
            )
        )
    translation = Vector(tuple(float(value) for value in node.get("translation", [0, 0, 0])))
    rotation = tuple(float(value) for value in node.get("rotation", [0, 0, 0, 1]))
    quaternion = Quaternion((rotation[3], rotation[0], rotation[1], rotation[2]))
    scale = tuple(float(value) for value in node.get("scale", [1, 1, 1]))
    return (
        Matrix.Translation(translation)
        @ quaternion.to_matrix().to_4x4()
        @ Matrix.Diagonal((*scale, 1.0))
    )


def _read_gltf_nodes(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".gltf":
        document = json.loads(path.read_text(encoding="utf-8"))
        return [node for node in document.get("nodes", []) if isinstance(node, dict)]
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) != 12 or header[:4] != b"glTF":
            raise ValueError(f"GLB inválido: {path}")
        chunks: list[bytes] = []
        while True:
            chunk_header = handle.read(8)
            if not chunk_header:
                break
            if len(chunk_header) != 8:
                raise ValueError(f"GLB truncado: {path}")
            length, chunk_type = struct.unpack("<I4s", chunk_header)
            payload = handle.read(length)
            if len(payload) != length:
                raise ValueError(f"GLB truncado: {path}")
            if chunk_type == b"JSON":
                chunks.append(payload)
    if not chunks:
        raise ValueError(f"GLB sem JSON: {path}")
    document = json.loads(chunks[0].decode("utf-8"))
    return [node for node in document.get("nodes", []) if isinstance(node, dict)]


def _gltf_node_world_matrix(nodes: list[dict[str, Any]], target_name: str) -> Matrix | None:
    target_normalized = _normalized_name(target_name)
    node_index = next(
        (
            index
            for index, node in enumerate(nodes)
            if _normalized_name(node.get("name", "")) == target_normalized
        ),
        None,
    )
    if node_index is None:
        return None
    parents = {
        int(child): index
        for index, node in enumerate(nodes)
        for child in node.get("children", [])
        if isinstance(child, int)
    }
    path: list[int] = []
    current = node_index
    visited: set[int] = set()
    while isinstance(current, int) and current not in visited and 0 <= current < len(nodes):
        visited.add(current)
        path.append(current)
        current = parents.get(current)
    result = Matrix.Identity(4)
    for index in reversed(path):
        result = result @ _gltf_node_matrix(nodes[index])
    return result


def _socket_correction(
    armature: bpy.types.Object,
    bone_name: str,
    character_path: Path | None,
) -> Matrix:
    """Map Blender's bone frame to the matching glTF node frame.

    Blender creates a bone-oriented frame (including its bone-length axis)
    when importing glTF.  The web viewer attaches props to the original glTF
    node frame.  The rest-pose delta is constant for a bone, so applying it
    to every animated pose preserves the web socket without baking away the
    animation.
    """
    if character_path is None or not _gltf_space(character_path):
        return Matrix.Identity(4)
    key = (str(character_path.resolve()), str(bone_name))
    cached = SOCKET_CORRECTION_CACHE.get(key)
    if cached is not None:
        return cached.copy()
    try:
        gltf_world = _gltf_node_world_matrix(_read_gltf_nodes(character_path), bone_name)
        if gltf_world is None:
            return Matrix.Identity(4)
        desired_world = _gltf_matrix_to_blender(gltf_world)
        rest_world = armature.matrix_world @ armature.data.bones[bone_name].matrix_local
        correction = rest_world.inverted_safe() @ desired_world
    except (OSError, ValueError, KeyError, TypeError, struct.error):
        return Matrix.Identity(4)
    SOCKET_CORRECTION_CACHE[key] = correction.copy()
    return correction


def find_bone(armature: bpy.types.Object, name: str) -> str:
    normalized_name = _normalized_name(name)
    aliases = {
        "handr": {"handr", "righthand", "handright"},
        "handl": {"handl", "lefthand", "handleft"},
    }
    normalized_aliases = aliases.get(normalized_name, {normalized_name})
    for bone in armature.pose.bones:
        normalized = _normalized_name(bone.name)
        if normalized in normalized_aliases:
            return bone.name
    raise RuntimeError(f"rig sem o osso/socket {name}")


def find_hand(armature: bpy.types.Object, side: str) -> str:
    socket = "hand_r" if side.casefold().startswith("r") else "hand_l"
    return find_bone(armature, socket)


def action_range(action: bpy.types.Action | None) -> tuple[int, int]:
    if action is None:
        return 1, 1
    return max(1, math.floor(action.frame_range[0])), max(
        1, math.ceil(action.frame_range[1])
    )


def find_action(name: str | None) -> bpy.types.Action | None:
    if not name:
        return None
    actions = list(bpy.data.actions)
    exact = next((item for item in actions if item.name == name), None)
    if exact:
        return exact
    leaf = name.split("|")[-1]
    return next(
        (item for item in actions if item.name == leaf or item.name.endswith("|" + leaf)),
        None,
    )


def _action_fcurves(action: bpy.types.Action) -> list[Any]:
    """Return action curves across Blender's legacy and layered APIs."""
    curves = getattr(action, "fcurves", None)
    if curves is not None:
        try:
            return list(curves)
        except (TypeError, RuntimeError):
            pass
    result: list[Any] = []
    for layer in getattr(action, "layers", []) or []:
        for strip in getattr(layer, "strips", []) or []:
            channelbag_method = getattr(strip, "channelbag", None)
            if channelbag_method is None:
                continue
            for slot in getattr(action, "slots", []) or []:
                try:
                    channelbag = channelbag_method(slot)
                except (TypeError, RuntimeError):
                    continue
                if channelbag is not None:
                    result.extend(list(getattr(channelbag, "fcurves", []) or []))
    return result


def _lock_curve_at_start(curve: Any, baseline: float) -> None:
    """Flatten a location channel while keeping its original start pose."""
    for keyframe in curve.keyframe_points:
        keyframe.co.y = baseline
        keyframe.handle_left.y = baseline
        keyframe.handle_right.y = baseline
    curve.update()


def lock_root_motion(
    armature: bpy.types.Object,
    action: bpy.types.Action | None,
) -> dict[str, Any]:
    """Pin planar root translation so imported movement stays in place.

    Root rotation and pose channels remain untouched. Only X/Y location curves
    of the root-motion bone (or the armature object fallback) are flattened to
    their first-frame values; vertical motion remains available for jumps and
    crouches.
    """
    metadata: dict[str, Any] = {
        "enabled": False,
        "bone": None,
        "axes": [],
        "delta": [0.0, 0.0, 0.0],
    }
    if action is None:
        return metadata

    bone_curves: dict[str, list[Any]] = {}
    object_curves: list[Any] = []
    for curve in _action_fcurves(action):
        if str(getattr(curve, "data_path", "")).endswith("location"):
            match = BONE_PATH_RE.search(str(getattr(curve, "data_path", "")))
            if match:
                bone_name = match.group(1) or match.group(2)
                bone_curves.setdefault(bone_name, []).append(curve)
            elif str(getattr(curve, "data_path", "")) == "location":
                object_curves.append(curve)

    candidates = [
        (name, curves)
        for name, curves in bone_curves.items()
        if any(token in name.casefold() for token in ROOT_BONE_TOKENS)
    ]
    if candidates:
        bone_name, curves = sorted(
            candidates,
            key=lambda item: (
                0 if item[0].casefold().replace("_", "") in ROOT_BONE_TOKENS else 1,
                len(item[0]),
                item[0].casefold(),
            ),
        )[0]
    elif object_curves:
        bone_name, curves = "armature_object", object_curves
    else:
        return metadata

    start, end = action_range(action)
    for curve in curves:
        axis = int(getattr(curve, "array_index", -1))
        if axis not in (0, 1, 2):
            continue
        try:
            metadata["delta"][axis] = float(curve.evaluate(end) - curve.evaluate(start))
        except (AttributeError, RuntimeError, TypeError):
            continue
        if axis not in (0, 1):
            continue
        try:
            baseline = float(curve.evaluate(start))
            _lock_curve_at_start(curve, baseline)
        except (AttributeError, RuntimeError, TypeError):
            continue
        metadata["axes"].append("xyz"[axis])

    metadata["axes"] = sorted(set(metadata["axes"]))
    metadata["bone"] = bone_name
    metadata["enabled"] = bool(metadata["axes"])
    metadata["planar_distance"] = math.hypot(metadata["delta"][0], metadata["delta"][1])
    action["_sprite_root_motion_lock"] = json.dumps(metadata, ensure_ascii=False)
    bpy.context.view_layer.update()
    return metadata


def root_motion_lock_metadata(action: bpy.types.Action | None) -> dict[str, Any]:
    """Read the lock report stored on an Action by :func:`lock_root_motion`."""
    if action is None:
        return {"enabled": False, "bone": None, "axes": [], "delta": [0.0, 0.0, 0.0]}
    try:
        value = action.get("_sprite_root_motion_lock")
        if value:
            return json.loads(str(value))
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return {"enabled": False, "bone": None, "axes": [], "delta": [0.0, 0.0, 0.0]}


def apply_animation(
    armature: bpy.types.Object,
    animation_path: Path | None,
    action_name: str | None,
) -> bpy.types.Action | None:
    action = find_action(action_name)
    if action is None and animation_path is not None:
        imported = import_asset(animation_path)
        action = find_action(action_name)
        if action is None:
            imported_actions = list(bpy.data.actions)
            if imported_actions:
                action = max(
                    imported_actions,
                    key=lambda item: item.frame_range[1] - item.frame_range[0],
                )
        for obj in imported:
            bpy.data.objects.remove(obj, do_unlink=True)
    if action is not None:
        if armature.animation_data is None:
            armature.animation_data_create()
        armature.animation_data.action = action
        action_slots = getattr(action, "slots", ())
        if action_slots and hasattr(armature.animation_data, "action_slot"):
            armature.animation_data.action_slot = action_slots[0]
        lock_root_motion(armature, action)
    return action


def _mesh_objects() -> list[bpy.types.Object]:
    return [
        obj for obj in bpy.data.objects
        if obj.type == "MESH" and not obj.hide_render
    ]


def _find_named_node(root: bpy.types.Object, name: str) -> bpy.types.Object | None:
    normalized_name = _normalized_name(name)
    stack = [root]
    while stack:
        current = stack.pop()
        if _normalized_name(current.name) == normalized_name:
            return current
        stack.extend(reversed(list(current.children)))
    return None


def _two_hand_axis(name: str) -> Vector:
    axes = {
        "x": Vector((1.0, 0.0, 0.0)),
        "y": Vector((0.0, 1.0, 0.0)),
        "z": Vector((0.0, 0.0, 1.0)),
        "-x": Vector((-1.0, 0.0, 0.0)),
        "-y": Vector((0.0, -1.0, 0.0)),
        "-z": Vector((0.0, 0.0, -1.0)),
    }
    return axes.get(str(name).casefold(), axes["z"]).copy()


def _pose_bone_position(armature: bpy.types.Object, bone_name: str) -> Vector:
    bone = armature.pose.bones.get(bone_name)
    if bone is None:
        raise RuntimeError(f"rig sem o osso/socket {bone_name}")
    return bone.matrix.translation.copy()


def _palm_center_offset(armature: bpy.types.Object, bone_name: str) -> Vector:
    """Return the grip point in hand-bone local coordinates."""
    hand = armature.data.bones.get(bone_name)
    if hand is None or _normalized_name(hand.name) not in {
        "handr", "handl", "righthand", "lefthand", "handright", "handleft",
    }:
        return Vector((0.0, 0.0, 0.0))
    inverse = hand.matrix_local.inverted()
    thumbs: list[Vector] = []
    fingers: list[Vector] = []
    for child in hand.children:
        local_head = inverse @ child.head_local
        normalized = _normalized_name(child.name)
        if "thumb" in normalized:
            thumbs.append(local_head)
        elif any(name in normalized for name in ("index", "middle", "ring", "pinky", "little")):
            fingers.append(local_head)
    if not fingers:
        return Vector((0.0, 0.0, 0.0))
    finger_line = sum(fingers, Vector((0.0, 0.0, 0.0))) / len(fingers)
    if not thumbs:
        return finger_line * 0.6
    thumb_joint = sum(thumbs, Vector((0.0, 0.0, 0.0))) / len(thumbs)
    return (thumb_joint + finger_line) * 0.5


def _pose_palm_position(armature: bpy.types.Object, bone_name: str) -> Vector:
    bone = armature.pose.bones.get(bone_name)
    if bone is None:
        raise RuntimeError(f"rig sem o osso/socket {bone_name}")
    return bone.matrix @ _palm_center_offset(armature, bone_name)


def update_two_hand_components(armature: bpy.types.Object) -> None:
    """Update two-handed component roots for the current evaluated frame."""
    roots = [
        obj for obj in bpy.data.objects
        if obj.type == "EMPTY" and obj.get("_sprite_two_handed")
    ]
    if not roots:
        return
    bpy.context.view_layer.update()
    for root in roots:
        primary = str(root.get("_sprite_primary_bone") or "")
        secondary = str(root.get("_sprite_secondary_bone") or "")
        if not primary or not secondary:
            continue
        first = _pose_palm_position(armature, primary)
        second = _pose_palm_position(armature, secondary)
        direction = second - first
        if direction.length < 1e-6:
            continue
        direction.normalize()
        base_rotation = Euler(
            tuple(
                math.radians(float(value))
                for value in root.get("_sprite_base_rotation", [0, 0, 0])
            ),
            "XYZ",
        ).to_quaternion()
        axis = base_rotation @ _two_hand_axis(str(root.get("_sprite_two_hand_axis", "z")))
        alignment = axis.rotation_difference(direction)
        root.rotation_mode = "QUATERNION"
        root.rotation_quaternion = alignment @ base_rotation
        midpoint = (first + second) * 0.5
        offset = Vector(tuple(float(value) for value in root.get("_sprite_base_position", [0, 0, 0])))
        root.location = midpoint + root.rotation_quaternion @ offset
    bpy.context.view_layer.update()


def bake_two_hand_components(
    armature: bpy.types.Object,
    action: bpy.types.Action,
) -> None:
    """Bake two-hand roots so exported GLBs follow both sockets without Blender."""
    roots = [
        obj for obj in bpy.data.objects
        if obj.type == "EMPTY" and obj.get("_sprite_two_handed")
    ]
    if not roots:
        return
    scene = bpy.context.scene
    start, end = action_range(action)
    current = scene.frame_current
    for frame in range(start, end + 1):
        scene.frame_set(frame)
        update_two_hand_components(armature)
        for root in roots:
            root.keyframe_insert(data_path="location", frame=frame)
            root.keyframe_insert(data_path="rotation_quaternion", frame=frame)
    scene.frame_set(current)
    update_two_hand_components(armature)


def _component_transform(
    component: dict[str, Any],
    request: dict[str, Any],
    character_height: float,
) -> tuple[list[float], list[float], list[float], float]:
    transform = component.get("transform") or {}
    position = [float(value) for value in transform.get("position", [0.0, 0.0, 0.0])]
    rotation = [float(value) for value in transform.get("rotation", [0.0, 0.0, 0.0])]
    base_scale = [float(value) for value in transform.get("scale", [1.0, 1.0, 1.0])]
    fit = component.get("fit") or {}
    fit_mode = str(fit.get("mode", "none")).casefold()
    fit_ratio = float(fit.get("ratio", 1.0))
    fit_scale = 1.0
    if fit_mode == "character_height":
        fit_scale = character_height * fit_ratio

    # Existing API payloads remain authoritative for legacy weapon rows.
    if component.get("legacy") and component.get("role") == "weapon":
        requested_rotation = request.get("weapon_rotation")
        if isinstance(requested_rotation, (list, tuple)) and len(requested_rotation) >= 3:
            rotation = [float(value) for value in requested_rotation[:3]]
        requested_offset = request.get("weapon_offset")
        if isinstance(requested_offset, (list, tuple)) and len(requested_offset) >= 3:
            position = [float(value) for value in requested_offset[:3]]
        if request.get("weapon_height_ratio") is not None:
            fit_scale = character_height * float(request["weapon_height_ratio"])
    return position, rotation, base_scale, fit_scale


def _attach_component(
    path: Path,
    armature: bpy.types.Object,
    component: dict[str, Any],
    request: dict[str, Any] | None = None,
    parent_roots: dict[str, bpy.types.Object] | None = None,
    character_height: float | None = None,
) -> tuple[dict[str, Any], bpy.types.Object]:
    request = request or {}
    parent_roots = parent_roots or {"character": armature}
    if character_height is None:
        character_bounds = bounds_at_frames([1])
        character_height = max(0.001, character_bounds[1].z - character_bounds[0].z)
    imported = import_asset(path)
    meshes = [obj for obj in imported if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError(f"componente sem mesh: {path}")

    component_id = str(component.get("id") or "component")
    root = bpy.data.objects.new(f"sprite_component_{component_id}", None)
    bpy.context.scene.collection.objects.link(root)
    parent_name = str(component.get("parent") or "character")
    attach_to = component.get("attach_to")
    attach_to_secondary = component.get("attach_to_secondary")
    parent_object = parent_roots.get(parent_name)
    resolved_attach_to_secondary = None

    if attach_to_secondary:
        if parent_name != "character" or not attach_to:
            raise RuntimeError("componente de duas mãos exige parent character e socket primário")
        primary_bone = find_bone(armature, str(attach_to))
        secondary_bone = find_bone(armature, str(attach_to_secondary))
        # A two-handed root is parented to the armature object, not one bone;
        # its transform is recomputed from both pose sockets every frame.
        root.parent = armature
        root.parent_type = "OBJECT"
        resolved_attach_to = primary_bone
        resolved_attach_to_secondary = secondary_bone
    elif attach_to:
        if parent_name == "character":
            bone_name = find_bone(armature, str(attach_to))
            root.parent = armature
            root.parent_type = "BONE"
            root.parent_bone = bone_name
            resolved_attach_to = bone_name
        else:
            if parent_object is None:
                raise RuntimeError(f"parent do componente não encontrado: {parent_name}")
            target = _find_named_node(parent_object, str(attach_to))
            if target is None:
                raise RuntimeError(
                    f"socket {attach_to} não encontrado no componente {parent_name}"
                )
            root.parent = target
            resolved_attach_to = target.name
    elif parent_name == "scene":
        resolved_attach_to = None
    else:
        if parent_object is None:
            raise RuntimeError(f"parent do componente não encontrado: {parent_name}")
        root.parent = parent_object
        resolved_attach_to = None
    dimensions = []
    for obj in meshes:
        dimensions.extend(float(value) for value in obj.dimensions)
    length = max(dimensions or [1.0])
    position, rotation, base_scale, fit_scale = _component_transform(
        component, request, character_height
    )
    fit_mode = str((component.get("fit") or {}).get("mode", "none")).casefold()
    scale = fit_scale / max(length, 1e-6) if fit_mode == "character_height" else 1.0
    palm_offset = Vector((0.0, 0.0, 0.0))
    if attach_to and not attach_to_secondary and parent_name == "character":
        palm_offset = _palm_center_offset(armature, resolved_attach_to)

    character_path_value = request.get("character_path")
    character_path = Path(str(character_path_value)) if character_path_value else None
    imported_gltf_space = _gltf_space(path) and _gltf_space(character_path)
    authored_rotation = _three_euler_xyz_matrix(
        tuple(math.radians(value) for value in rotation[:3])
    )
    if imported_gltf_space:
        local_position = (
            _gltf_vector_to_blender(Vector(tuple(position[:3])))
            + _gltf_vector_to_blender(palm_offset)
        )
        local_rotation = _gltf_matrix_to_blender(authored_rotation)
        local_scale_values = (base_scale[0], base_scale[2], base_scale[1])
    else:
        local_position = Vector(tuple(position[:3])) + palm_offset
        local_rotation = authored_rotation
        local_scale_values = (base_scale[0], base_scale[1], base_scale[2])
    local_scale = Matrix.Diagonal(
        (
            scale * local_scale_values[0],
            scale * local_scale_values[1],
            scale * local_scale_values[2],
            1.0,
        )
    )
    local_transform = Matrix.Translation(local_position) @ local_rotation @ local_scale
    if attach_to and not attach_to_secondary and parent_name == "character":
        # The web viewer applies the saved transform in socket-local space.
        # Blender's bone-parent basis includes the bone rest orientation, so
        # calculate a constant correction from an identity bone child. This
        # keeps the world matrix equal to socket_world × local_transform at
        # every animation frame while retaining a real bone parent for GLB export.
        pose_bone = armature.pose.bones.get(resolved_attach_to)
        if pose_bone is None:
            raise RuntimeError(f"rig sem o osso/socket {resolved_attach_to}")
        socket_world = armature.matrix_world @ pose_bone.matrix
        if imported_gltf_space:
            socket_world = socket_world @ _socket_correction(
                armature,
                resolved_attach_to,
                character_path,
            )
        root.parent = armature
        root.parent_type = "BONE"
        root.parent_bone = resolved_attach_to
        root.matrix_parent_inverse = Matrix.Identity(4)
        root.matrix_basis = Matrix.Identity(4)
        bpy.context.view_layer.update()
        bone_parent_world = root.matrix_world.copy()
        root.rotation_mode = "QUATERNION"
        root.matrix_basis = bone_parent_world.inverted() @ socket_world @ local_transform
    else:
        root.matrix_basis = local_transform
    visible = bool(component.get("visible", True))
    root.hide_render = not visible
    if attach_to_secondary:
        root["_sprite_two_handed"] = True
        root["_sprite_primary_bone"] = resolved_attach_to
        root["_sprite_secondary_bone"] = resolved_attach_to_secondary
        root["_sprite_two_hand_axis"] = str(component.get("two_hand_axis", "z"))
        root["_sprite_base_position"] = list(local_position)
        root["_sprite_base_rotation"] = [
            math.degrees(value) for value in local_rotation.to_euler("XYZ")
        ]
    # Blender may leave a newly linked scene-root empty's matrix_world stale
    # until the view layer evaluates it.  Children must be parented only after
    # this update, otherwise their local matrices can silently cancel scale or
    # rotation on scene-level components.
    bpy.context.view_layer.update()
    imported_objects = set(imported)
    for obj in imported:
        if obj.parent in imported_objects:
            continue
        world = obj.matrix_world.copy()
        obj.parent = root
        obj.matrix_parent_inverse = Matrix.Identity(4)
        # Imported FBX/GLTF objects carry their asset-local transform in
        # matrix_world because they enter a fresh Blender scene.  Reusing that
        # matrix as matrix_basis lets the new bone/component parent transform
        # the whole imported asset instead of cancelling it out.
        obj.matrix_basis = world
    for obj in meshes:
        obj.hide_render = not visible
    bpy.context.view_layer.update()
    if attach_to_secondary:
        update_two_hand_components(armature)

    metadata = {
        "id": component_id,
        "role": component.get("role", "prop"),
        "path": str(path),
        "parent": parent_name,
        "attach_to": resolved_attach_to,
        "attach_to_secondary": resolved_attach_to_secondary,
        "two_hand_axis": str(component.get("two_hand_axis", "z")),
        "scale": list(root.scale),
        "rotation_degrees": rotation,
        "offset": position,
        "palm_offset": list(palm_offset),
        "fit": component.get("fit", {"mode": "none", "ratio": 1.0}),
        "visible": visible,
    }
    return metadata, root


def attach_components(
    component_requests: list[dict[str, Any]],
    armature: bpy.types.Object,
    request: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach ordered components, resolving component-to-component parents."""
    request = request or {}
    character_bounds = bounds_at_frames([1])
    character_height = max(0.001, character_bounds[1].z - character_bounds[0].z)
    pending = list(component_requests)
    parent_roots: dict[str, bpy.types.Object] = {"character": armature}
    metadata: list[dict[str, Any]] = []
    while pending:
        progress = False
        remaining: list[dict[str, Any]] = []
        for component in pending:
            parent_name = str(component.get("parent") or "character")
            if parent_name != "scene" and parent_name not in parent_roots:
                remaining.append(component)
                continue
            item_metadata, root = _attach_component(
                Path(str(component["path"])),
                armature,
                component,
                request,
                parent_roots,
                character_height,
            )
            parent_roots[str(component["id"])] = root
            metadata.append(item_metadata)
            progress = True
        if not progress:
            unresolved = ", ".join(str(item.get("id")) for item in remaining)
            raise RuntimeError(f"não foi possível resolver parents dos componentes: {unresolved}")
        pending = remaining
    return metadata


def attach_weapon(path: Path, armature: bpy.types.Object, request: dict[str, Any]) -> dict[str, Any]:
    """Compatibility wrapper for the original single-weapon API."""
    side = str(request.get("weapon_hand", "right"))
    component = {
        "id": "weapon",
        "asset_id": "legacy-weapon",
        "role": "weapon",
        "parent": "character",
        "attach_to": "hand_r" if side.casefold().startswith("r") else "hand_l",
        "fit": {
            "mode": "character_height",
            "ratio": float(request.get("weapon_height_ratio", 0.8)),
        },
        "transform": {
            "position": [0.0, 0.0, 0.0],
            "rotation": request.get("weapon_rotation", [0.0, 0.0, 0.0]),
            "scale": [1.0, 1.0, 1.0],
        },
        "legacy": True,
    }
    metadata, _ = _attach_component(path, armature, component, request)
    return {
        "path": metadata["path"],
        "hand": side,
        "bone": metadata["attach_to"],
        "scale": metadata["scale"][0],
        "rotation_degrees": metadata["rotation_degrees"],
        "offset": metadata["offset"],
        "height_ratio": request.get("weapon_height_ratio", 0.8),
    }


def bounds_at_frames(
    frames: list[int],
    excluded: set[bpy.types.Object] | None = None,
    on_frame=None,
) -> tuple[Vector, Vector]:
    minimum = Vector((float("inf"), float("inf"), float("inf")))
    maximum = Vector((float("-inf"), float("-inf"), float("-inf")))
    scene = bpy.context.scene
    for frame in frames:
        scene.frame_set(frame)
        if on_frame is not None:
            on_frame()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        for obj in _mesh_objects():
            if excluded and obj in excluded:
                continue
            evaluated = obj.evaluated_get(depsgraph)
            for corner in evaluated.bound_box:
                point = evaluated.matrix_world @ Vector(corner)
                minimum.x = min(minimum.x, point.x)
                minimum.y = min(minimum.y, point.y)
                minimum.z = min(minimum.z, point.z)
                maximum.x = max(maximum.x, point.x)
                maximum.y = max(maximum.y, point.y)
                maximum.z = max(maximum.z, point.z)
    if not math.isfinite(minimum.x):
        raise RuntimeError("nenhum mesh renderizável encontrado")
    return minimum, maximum


def setup_scene(
    scene: bpy.types.Scene, resolution: int, minimum: Vector, maximum: Vector
) -> None:
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except TypeError:
        scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    world = bpy.data.worlds.new("semantic_preview_world")
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    if background:
        background.inputs["Color"].default_value = (0.015, 0.018, 0.025, 1.0)
        background.inputs["Strength"].default_value = 0.3
    scene.world = world
    center = (minimum + maximum) * 0.5
    radius = max(1.0, max(maximum.x - minimum.x, maximum.y - minimum.y, maximum.z - minimum.z))
    for name, energy, direction in (
        ("key", 2.4, (-2.5, -3.5, 4.0)),
        ("fill", 1.1, (3.5, -2.0, 2.0)),
        ("rim", 1.5, (1.5, 3.0, 3.5)),
    ):
        data = bpy.data.lights.new(name, "AREA")
        data.energy = energy * 500 * radius * radius
        data.shape = "DISK"
        data.size = radius * 1.5
        lamp = bpy.data.objects.new(name, data)
        scene.collection.objects.link(lamp)
        lamp.location = center + Vector(direction) * radius
        lamp.rotation_euler = (center - lamp.location).to_track_quat("-Z", "Y").to_euler()


def look_at(camera: bpy.types.Object, target: Vector) -> None:
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def make_camera(scene: bpy.types.Scene, view: str, minimum: Vector, maximum: Vector) -> bpy.types.Object:
    center = (minimum + maximum) * 0.5
    span_x = max(0.001, maximum.x - minimum.x)
    span_y = max(0.001, maximum.y - minimum.y)
    span_z = max(0.001, maximum.z - minimum.z)
    if view == "top":
        scale = max(span_x, span_y) * 1.22
        distance = max(span_x, span_y, span_z) * 3.0 + 1.0
        location = Vector((center.x, center.y, maximum.z + distance))
    else:
        scale = max(span_x, span_z) * 1.22
        distance = max(span_x, span_y, span_z) * 3.0 + 1.0
        location = Vector((center.x, minimum.y - distance, center.z))
    data = bpy.data.cameras.new(f"semantic_camera_{view}")
    data.type = "ORTHO"
    data.ortho_scale = scale
    camera = bpy.data.objects.new(f"semantic_camera_{view}", data)
    scene.collection.objects.link(camera)
    camera.location = location
    look_at(camera, center)
    scene.camera = camera
    return camera


def sample_frames(start: int, end: int, count: int) -> list[int]:
    if end <= start or count <= 1:
        return [start]
    return sorted({start + round((end - start) * index / (count - 1)) for index in range(count)})


def render_view(scene: bpy.types.Scene, view: str, minimum: Vector, maximum: Vector, output: Path, resolution: int) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    make_camera(scene, view, minimum, maximum)
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.filepath = str(output)
    bpy.ops.render.render(write_still=True)
    return {
        "path": str(output),
        "view": view,
        "bounds": {"min": list(minimum), "max": list(maximum)},
        "ortho_scale": scene.camera.data.ortho_scale,
    }


def main() -> int:
    request_path, result_path = args()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    output = Path(request["output"]).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    character_path = Path(request["character_path"]).expanduser().resolve()
    import_asset(character_path)
    armature = find_armature()
    action = None
    if request.get("animation_path"):
        if armature is None:
            raise RuntimeError("personagem sem armature para receber animação")
        action = apply_animation(
            armature,
            Path(request["animation_path"]).expanduser().resolve(),
            request.get("action_name"),
        )
    root_motion_lock = root_motion_lock_metadata(action)
    weapon_meta = None
    if request.get("weapon_path"):
        if armature is None:
            raise RuntimeError("personagem sem armature para anexar arma")
        weapon_meta = attach_weapon(
            Path(request["weapon_path"]).expanduser().resolve(), armature, request
        )
    resolution = max(128, int(request.get("resolution", 512)))
    start, end = action_range(action)
    frame_count = max(1, int(request.get("gif_frames", 24)))
    frames = sample_frames(start, end, frame_count)
    envelope_frames = frames if action is not None else [start]
    minimum, maximum = bounds_at_frames(
        envelope_frames,
        on_frame=(lambda: update_two_hand_components(armature)) if armature else None,
    )
    setup_scene(scene, resolution, minimum, maximum)
    views = {
        "front": render_view(scene, "front", minimum, maximum, output / "front.png", resolution),
        "top": render_view(scene, "top", minimum, maximum, output / "top.png", resolution),
    }
    gif_frames: list[str] = []
    if action is not None:
        make_camera(scene, "front", minimum, maximum)
        for index, frame in enumerate(frames):
            scene.frame_set(frame)
            if armature:
                update_two_hand_components(armature)
            path = output / "frames" / f"frame_{index:03d}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            gif_frames.append(str(path))
    metadata = {
        "schema": "sprite_lab.semantic_preview/v1",
        "character_path": str(character_path),
        "animation_path": request.get("animation_path"),
        "action_name": request.get("action_name"),
        "root_motion_lock": root_motion_lock,
        "frame_range": [start, end],
        "sampled_frames": frames,
        "fps": float(request.get("fps", 10)),
        "views": views,
        "gif_frames": gif_frames,
        "weapon": weapon_meta,
        "adaptive_bounds": {"min": list(minimum), "max": list(maximum)},
        "spritesheet_generated": False,
    }
    result_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"SEMANTIC_PREVIEW_OK views=2 gif_frames={len(gif_frames)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
