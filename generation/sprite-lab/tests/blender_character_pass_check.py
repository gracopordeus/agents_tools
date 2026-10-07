"""One-cell Blender smoke check for the isolated-character visibility pass."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import bpy


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

from blender_layer_visibility import character_only_visibility  # noqa: E402


def opaque_pixels(path: Path) -> int:
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        return sum(1 for alpha in image.pixels[3::4] if alpha > 0.01)
    finally:
        bpy.data.images.remove(image)


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
    scene.render.resolution_x = 64
    scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True

    bpy.ops.mesh.primitive_cube_add(location=(-1.0, 0.0, 0.0), scale=(0.7, 0.7, 0.7))
    character = bpy.context.object
    character.name = "CharacterMesh"
    bpy.ops.mesh.primitive_cube_add(location=(1.0, 0.0, 0.0), scale=(0.7, 0.7, 0.7))
    weapon = bpy.context.object
    weapon.name = "WeaponMesh"
    weapon["conditioning_component_role"] = "weapon"

    bpy.ops.object.camera_add(location=(0.0, 0.0, 10.0))
    camera = bpy.context.object
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 5.0
    scene.camera = camera

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        combined = root / "combined.png"
        isolated = root / "isolated.png"
        scene.render.filepath = str(combined)
        bpy.ops.render.render(write_still=True)
        with character_only_visibility([character, weapon]):
            scene.render.filepath = str(isolated)
            bpy.ops.render.render(write_still=True)
        combined_pixels = opaque_pixels(combined)
        isolated_pixels = opaque_pixels(isolated)
        if not 0 < isolated_pixels < combined_pixels:
            raise RuntimeError(
                f"isolamento inválido: combined={combined_pixels}, isolated={isolated_pixels}"
            )
        if weapon.hide_render:
            raise RuntimeError("visibilidade da arma não foi restaurada")
        print(json.dumps({
            "combined_opaque_pixels": combined_pixels,
            "isolated_opaque_pixels": isolated_pixels,
            "weapon_visibility_restored": True,
            "resolution": [64, 64],
            "format": "PNG RGBA",
        }))


if __name__ == "__main__":
    main()
