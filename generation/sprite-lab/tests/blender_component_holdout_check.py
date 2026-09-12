"""Two-cell headless smoke test for Blender's native component Holdout pass."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import bpy


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

from blender_layer_visibility import component_holdout_visibility  # noqa: E402


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


def alpha_stats(path: Path) -> dict[str, object]:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = list(image.pixels)
        visible = []
        for index in range(width * height):
            if pixels[index * 4 + 3] > 0.05:
                visible.append((index % width, index // width))
        if visible:
            xs = [point[0] for point in visible]
            ys = [point[1] for point in visible]
            bounding_box = [min(xs), min(ys), max(xs), max(ys)]
        else:
            bounding_box = None
        center = ((height // 2) * width + width // 2) * 4 + 3
        corner = 3
        return {
            "count": len(visible),
            "bbox": bounding_box,
            "center_alpha": pixels[center],
            "corner_alpha": pixels[corner],
            "size": [width, height],
        }
    finally:
        bpy.data.images.remove(image)


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
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
    scene.render.resolution_x = 128
    scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True

    bpy.ops.mesh.primitive_cube_add(
        location=(0.0, 0.0, 0.0), scale=(0.85, 0.8, 0.42)
    )
    character = bpy.context.object
    character.name = "CharacterOccluder"
    character.data.materials.append(emission("character", (0.2, 0.2, 0.2, 1.0)))

    component_root = bpy.data.objects.new("sprite_component_coat_1", None)
    scene.collection.objects.link(component_root)
    component_root["conditioning_component_id"] = "coat_1"
    component_root["conditioning_component_role"] = "clothing"
    bpy.ops.mesh.primitive_cube_add(
        location=(0.0, 0.0, -0.8), scale=(1.65, 0.2, 0.16)
    )
    component = bpy.context.object
    component.name = "CoatLayer"
    component.parent = component_root
    component.data.materials.append(emission("coat", (1.0, 0.2, 0.05, 1.0)))

    bpy.ops.object.camera_add(location=(0.0, 0.0, 10.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 4.5
    scene.camera = camera

    original = {
        "character_hide": character.hide_render,
        "character_holdout": character.is_holdout,
        "component_hide": component.hide_render,
        "component_holdout": component.is_holdout,
        "component_parent": component.parent,
        "character_material": character.material_slots[0].material,
        "component_material": component.material_slots[0].material,
        "camera_matrix": camera.matrix_world.copy(),
    }
    results = {}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        # These filenames intentionally model adjacent spritesheet cells.  The
        # camera/framing remains locked; only component depth changes.
        for column, (label, depth) in enumerate((("rear", -0.8), ("front", 0.8))):
            component_root.location.z = depth - component.location.z
            target = root / f"row0_col{column}.png"
            with component_holdout_visibility(
                [character, component], "coat_1"
            ) as selected:
                if selected != [component] or not character.is_holdout:
                    raise RuntimeError("seleção/holdout nativo incorreto")
                scene.render.filepath = str(target)
                bpy.ops.render.render(write_still=True)
            results[label] = alpha_stats(target)

    rear = results["rear"]
    front = results["front"]
    if rear["center_alpha"] > 0.05:
        raise RuntimeError(f"componente traseiro não foi recortado: {results}")
    if front["center_alpha"] < 0.95:
        raise RuntimeError(f"componente frontal foi perdido: {results}")
    if rear["count"] >= front["count"]:
        raise RuntimeError(f"holdout não reduziu a área traseira: {results}")
    if rear["bbox"] != front["bbox"]:
        raise RuntimeError(f"células perderam alinhamento/framing: {results}")
    if rear["corner_alpha"] > 0.01 or front["corner_alpha"] > 0.01:
        raise RuntimeError(f"fundo não é transparente: {results}")
    if rear["size"] != [128, 96] or front["size"] != [128, 96]:
        raise RuntimeError(f"tamanho de célula mudou: {results}")
    if (
        character.hide_render != original["character_hide"]
        or character.is_holdout != original["character_holdout"]
        or component.hide_render != original["component_hide"]
        or component.is_holdout != original["component_holdout"]
        or component.parent is not original["component_parent"]
        or character.material_slots[0].material is not original["character_material"]
        or component.material_slots[0].material is not original["component_material"]
        or camera.matrix_world != original["camera_matrix"]
    ):
        raise RuntimeError("estado da cena não foi restaurado")

    print(json.dumps({
        "method": "blender_object_holdout",
        "component": "coat_1",
        "cells": results,
        "rear_occluded": True,
        "front_visible": True,
        "transparent_background": True,
        "aligned_grid": True,
        "scene_state_restored": True,
    }))


if __name__ == "__main__":
    main()
