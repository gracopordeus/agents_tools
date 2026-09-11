"""Two-cell Blender smoke check for the complete weapon-only pass."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import bpy


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

from blender_layer_visibility import weapon_only_visibility  # noqa: E402
from weapon_front_mask import (  # noqa: E402
    extract_weapon_front_mask,
    front_mask_palette,
    front_mask_palette_key,
)


def emission(name: str, color: tuple[float, float, float, float]):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    shader = nodes.new("ShaderNodeEmission")
    shader.inputs["Color"].default_value = color
    shader.inputs["Strength"].default_value = 1.0
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(shader.outputs["Emission"], output.inputs["Surface"])
    return material


def white_stats(path: Path) -> tuple[int, list[int]]:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        coordinates = []
        pixels = list(image.pixels)
        for index in range(width * height):
            red, green, blue, alpha = pixels[index * 4:index * 4 + 4]
            if alpha > 0.01 and red > 0.2 and green > 0.2 and blue > 0.2:
                coordinates.append((index % width, index // width))
        if not coordinates:
            return 0, [0, 0, 0, 0]
        xs = [point[0] for point in coordinates]
        ys = [point[1] for point in coordinates]
        return len(coordinates), [min(xs), min(ys), max(xs), max(ys)]
    finally:
        bpy.data.images.remove(image)


def render(scene, path: Path) -> None:
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def main() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    engines = {
        item.identifier
        for item in scene.render.bl_rna.properties["engine"].enum_items
    }
    scene.render.engine = (
        "BLENDER_EEVEE_NEXT"
        if "BLENDER_EEVEE_NEXT" in engines
        else "BLENDER_EEVEE"
    )
    scene.render.resolution_x = 96
    scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True

    bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 0.0), scale=(0.8, 0.8, 0.4))
    character = bpy.context.object
    character.name = "CharacterMesh"
    character.data.materials.append(emission("character_black", (0.0, 0.0, 0.0, 1.0)))

    weapon_root = bpy.data.objects.new("sprite_component_weapon_1", None)
    scene.collection.objects.link(weapon_root)
    weapon_root["conditioning_component_id"] = "weapon_1"
    weapon_root["conditioning_component_role"] = "weapon"
    bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 0.0), scale=(1.7, 0.18, 0.18))
    weapon = bpy.context.object
    weapon.name = "WeaponMesh"
    weapon.parent = weapon_root
    weapon.data.materials.append(emission("weapon_white", (1.0, 1.0, 1.0, 1.0)))

    bpy.ops.object.camera_add(location=(0.0, 0.0, 10.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 5.0
    scene.camera = camera
    original_parent = weapon.parent
    original_matrix = weapon.matrix_world.copy()

    results = {}
    front_masks = {}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for label, depth in (("front", 0.8), ("rear", -0.8)):
            weapon_root.location.z = depth
            combined = root / f"{label}_combined.png"
            isolated = root / f"{label}_isolated.png"
            render(scene, combined)
            with weapon_only_visibility([character, weapon], "weapon_1"):
                render(scene, isolated)
            combined_count, combined_bbox = white_stats(combined)
            isolated_count, isolated_bbox = white_stats(isolated)
            results[label] = {
                "combined_visible_weapon_pixels": combined_count,
                "combined_visible_weapon_bbox": combined_bbox,
                "isolated_weapon_pixels": isolated_count,
                "isolated_weapon_bbox": isolated_bbox,
            }

        production_palette = front_mask_palette()
        character_color = production_palette[
            front_mask_palette_key(None, "weapon_1")
        ]
        weapon_color = production_palette[
            front_mask_palette_key("weapon_1", "weapon_1")
        ]
        character.data.materials[0] = emission(
            "character_semantic",
            tuple(channel / 255 for channel in character_color),
        )
        weapon.data.materials[0] = emission(
            "weapon_semantic",
            tuple(channel / 255 for channel in weapon_color),
        )
        for label, depth in (("front", 0.8), ("rear", -0.8)):
            weapon_root.location.z = depth
            segmentation = root / f"{label}_segmentation.png"
            isolated_segmentation = root / f"{label}_weapon_only.png"
            mask = root / f"{label}_front_mask.png"
            isolated_mask = root / f"{label}_full_weapon_mask.png"
            render(scene, segmentation)
            report = extract_weapon_front_mask(segmentation, mask, dilation=1)
            with weapon_only_visibility([character, weapon], "weapon_1"):
                render(scene, isolated_segmentation)
            isolated_report = extract_weapon_front_mask(
                isolated_segmentation, isolated_mask, dilation=0
            )
            front_masks[label] = {
                "front_pixel_count": report["front_pixel_count"],
                "bounding_box": report["bounding_box"],
                "dilated_area": report["dilated_area"],
                "full_weapon_pixel_count": isolated_report["front_pixel_count"],
                "full_weapon_bbox": isolated_report["bounding_box"],
            }

    front = results["front"]
    rear = results["rear"]
    if front["isolated_weapon_bbox"] != rear["isolated_weapon_bbox"]:
        raise RuntimeError(f"bbox isolada desalinhada: {results}")
    if front["isolated_weapon_pixels"] != rear["isolated_weapon_pixels"]:
        raise RuntimeError(f"silhueta isolada mudou entre fases: {results}")
    if rear["combined_visible_weapon_pixels"] >= rear["isolated_weapon_pixels"]:
        raise RuntimeError(f"oclusão traseira não foi demonstrada: {results}")
    if front_masks["rear"]["front_pixel_count"] >= front_masks["rear"]["full_weapon_pixel_count"]:
        raise RuntimeError(f"máscara traseira incluiu pixels ocluídos: {front_masks}")
    if front_masks["front"]["front_pixel_count"] < front_masks["front"]["full_weapon_pixel_count"] * 0.9:
        raise RuntimeError(f"máscara frontal perdeu a arma visível: {front_masks}")
    if any(
        item["dilated_area"] < item["front_pixel_count"]
        for item in front_masks.values()
    ):
        raise RuntimeError(f"dilatação reduziu a máscara: {front_masks}")
    if weapon.parent is not original_parent or weapon.hide_render:
        raise RuntimeError("attachment ou visibilidade da arma foi alterado")
    weapon_root.location.z = 0.0
    if weapon.matrix_world.to_3x3() != original_matrix.to_3x3():
        raise RuntimeError("transform local da arma foi alterado pelo pass")
    print(json.dumps({
        **results,
        "front_masks": front_masks,
        "attachment_preserved": True,
        "visibility_restored": True,
        "camera": "ORTHO",
        "resolution": [96, 96],
        "format": "PNG RGBA",
        "front_mask_palette": {
            key: list(color) for key, color in production_palette.items()
        },
    }))


if __name__ == "__main__":
    main()
