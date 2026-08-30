"""Create deterministic placeholder equipment for Mixamo catalog renders.

The props are intentionally procedural and low-poly. They are visual helpers
for 2D reference sheets, not final game assets. Each prop is grouped under an
Empty parented to a Mixamo hand bone, so the complete group follows animation.
"""
from __future__ import annotations

import math
from typing import Any

import bpy
from mathutils import Matrix

PROP_VERSION = "0.5.0"


def _material(name: str, color: tuple[float, float, float, float],
              metallic: float = 0.0, roughness: float = 0.55) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.diffuse_color = color
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    if shader is not None:
        shader.inputs["Base Color"].default_value = color
        shader.inputs["Metallic"].default_value = metallic
        shader.inputs["Roughness"].default_value = roughness
    return material


def _finish(obj: bpy.types.Object, name: str, material: bpy.types.Material,
            parent: bpy.types.Object) -> bpy.types.Object:
    obj.name = name
    obj.parent = parent
    obj.matrix_parent_inverse = Matrix.Identity(4)
    if obj.data is not None and hasattr(obj.data, "materials"):
        obj.data.materials.append(material)
    return obj


def _cube(name: str, location: tuple[float, float, float],
          dimensions: tuple[float, float, float], material: bpy.types.Material,
          parent: bpy.types.Object,
          rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=location, rotation=rotation)
    obj = bpy.context.object
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return _finish(obj, name, material, parent)


def _cylinder(name: str, location: tuple[float, float, float], radius: float,
              depth: float, material: bpy.types.Material, parent: bpy.types.Object,
              rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
              vertices: int = 8) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices, radius=radius, depth=depth,
        location=location, rotation=rotation,
    )
    return _finish(bpy.context.object, name, material, parent)


def _cone(name: str, location: tuple[float, float, float], radius: float,
          depth: float, material: bpy.types.Material, parent: bpy.types.Object,
          vertices: int = 4,
          rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cone_add(
        vertices=vertices, radius1=radius, radius2=0.0, depth=depth,
        location=location, rotation=rotation,
    )
    return _finish(bpy.context.object, name, material, parent)


def _sphere(name: str, location: tuple[float, float, float], radius: float,
            material: bpy.types.Material, parent: bpy.types.Object,
            scale: tuple[float, float, float] = (1.0, 1.0, 1.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=radius, location=location)
    obj = bpy.context.object
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return _finish(obj, name, material, parent)


def _torus(name: str, location: tuple[float, float, float], major_radius: float,
           minor_radius: float, material: bpy.types.Material, parent: bpy.types.Object,
           rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> bpy.types.Object:
    bpy.ops.mesh.primitive_torus_add(
        major_segments=8, minor_segments=4,
        major_radius=major_radius, minor_radius=minor_radius,
        location=location, rotation=rotation,
    )
    return _finish(bpy.context.object, name, material, parent)


def _empty(name: str, scene: bpy.types.Scene) -> bpy.types.Object:
    root = bpy.data.objects.new(name, None)
    scene.collection.objects.link(root)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 5.0
    return root


def _find_bone(armature: bpy.types.Object, side: str) -> str:
    wanted = f"{side}hand".casefold()
    normalized = lambda value: value.casefold().replace("_", "").replace(" ", "")
    exact = [bone.name for bone in armature.pose.bones if normalized(bone.name) == wanted]
    if exact:
        return exact[0]
    partial = [bone.name for bone in armature.pose.bones if wanted in normalized(bone.name)]
    if partial:
        return partial[0]
    raise RuntimeError(f"Mixamo armature has no {side} hand bone")


def _attach(root: bpy.types.Object, armature: bpy.types.Object, bone_name: str,
            scene: bpy.types.Scene, yaw_degrees: float = 0.0,
            uniform_scale: float | tuple[float, float, float] = 1.0) -> None:
    scene.frame_set(scene.frame_start)
    pose_bone = armature.pose.bones.get(bone_name)
    if pose_bone is None:
        raise RuntimeError(f"Mixamo armature has no bone named {bone_name}")
    parent_world = armature.matrix_world @ pose_bone.matrix
    # Prop local +Z follows the hand's local +Y, the direction of the Mixamo
    # hand bone. A small yaw can make a prop face the palm naturally.
    align = Matrix.Rotation(-math.pi / 2.0, 4, "X")
    if yaw_degrees:
        align = align @ Matrix.Rotation(math.radians(yaw_degrees), 4, "Z")
    if isinstance(uniform_scale, (int, float)):
        scale_values = (float(uniform_scale),) * 3
    else:
        scale_values = tuple(float(value) for value in uniform_scale)
    scale = Matrix.Diagonal((*scale_values, 1.0))
    desired_world = parent_world @ align @ scale
    root.parent = armature
    root.parent_type = "BONE"
    root.parent_bone = bone_name
    root.matrix_parent_inverse = parent_world.inverted()
    root.matrix_basis = desired_world


def _make_sword(scene: bpy.types.Scene, armature: bpy.types.Object) -> dict[str, Any]:
    steel = _material("mixamo_placeholder_steel", (0.32, 0.46, 0.62, 1.0), 0.75, 0.28)
    gold = _material("mixamo_placeholder_gold", (0.78, 0.42, 0.08, 1.0), 0.65, 0.3)
    leather = _material("mixamo_placeholder_leather", (0.12, 0.045, 0.025, 1.0), 0.0, 0.8)
    root = _empty("mixamo_placeholder_sword", scene)
    objects = [
        _cylinder("placeholder_sword_grip", (0.0, 0.0, 4.0), 2.0, 14.0, leather, root),
        _sphere("placeholder_sword_pommel", (0.0, 0.0, -5.0), 2.8, gold, root),
        _cube("placeholder_sword_guard", (0.0, 0.0, 12.0), (4.0, 17.0, 3.0), gold, root),
        _cone("placeholder_sword_blade", (0.0, 0.0, 35.0), 5.2, 46.0, steel, root,
              rotation=(0.0, 0.0, math.pi / 4.0)),
        _cube("placeholder_sword_blade_core", (0.0, -0.6, 35.0), (1.0, 1.0, 33.0), gold, root),
    ]
    bone = _find_bone(armature, "Right")
    _attach(root, armature, bone, scene, uniform_scale=1.35)
    return {"name": "sword", "bone": bone, "scale": 1.35,
            "objects": [obj.name for obj in objects]}


def _make_shield(scene: bpy.types.Scene, armature: bpy.types.Object) -> dict[str, Any]:
    steel = _material("mixamo_placeholder_shield_steel", (0.22, 0.34, 0.52, 1.0), 0.72, 0.32)
    gold = _material("mixamo_placeholder_shield_gold", (0.78, 0.42, 0.08, 1.0), 0.65, 0.3)
    leather = _material("mixamo_placeholder_shield_leather", (0.12, 0.045, 0.025, 1.0), 0.0, 0.8)
    root = _empty("mixamo_placeholder_shield", scene)
    # The Mixamo hand alignment maps local Y to the forearm's front/back axis;
    # using it as the shield normal keeps the broad face readable in isometric
    # captures instead of presenting an edge-on disk.
    disk_rotation = (-math.pi / 2.0, 0.0, 0.0)
    objects = [
        _cylinder("placeholder_shield_body", (0.0, 5.0, 0.0), 16.0, 3.0, steel, root, disk_rotation),
        _torus("placeholder_shield_rim", (0.0, 5.0, 0.0), 14.8, 1.35, gold, root, disk_rotation),
        _sphere("placeholder_shield_boss", (2.6, 5.0, 0.0), 3.4, gold, root),
        _cube("placeholder_shield_grip", (-2.2, 5.0, 0.0), (4.0, 8.0, 3.0), leather, root),
        _cube("placeholder_shield_crossbar", (-2.4, 5.0, 0.0), (3.0, 3.0, 20.0), leather, root),
    ]
    bone = _find_bone(armature, "Left")
    # Relative to the previous 2.7x shield: 75% height/depth and one-third
    # width. The shield disk occupies local X/Z, with Z as its visible width.
    shield_scale = (2.025, 2.025, 0.675)
    _attach(root, armature, bone, scene, uniform_scale=shield_scale)
    return {"name": "shield", "bone": bone, "scale": list(shield_scale),
            "objects": [obj.name for obj in objects]}


def add_placeholder_props(armature: bpy.types.Object, preset: str,
                          scene: bpy.types.Scene | None = None) -> dict[str, Any]:
    """Attach a named placeholder equipment preset to a Mixamo armature."""
    if preset in ("", "none", None):
        return {"preset": "none", "version": PROP_VERSION, "items": []}
    if preset != "sword_shield":
        raise ValueError(f"unknown Mixamo props preset: {preset}")
    scene = scene or bpy.context.scene
    items = [_make_sword(scene, armature), _make_shield(scene, armature)]
    return {
        "preset": preset,
        "version": PROP_VERSION,
        "items": items,
        "placeholder_only": True,
        "source": "procedural_blender_geometry",
    }
