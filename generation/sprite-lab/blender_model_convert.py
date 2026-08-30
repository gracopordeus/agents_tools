"""Convert one imported model to a browser-friendly GLB cache artifact."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bpy


AUXILIARY_OBJECT_NAMES = {"cube", "icosphere"}


def _normalized_name(value: str) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def remove_preview_helpers() -> None:
    """Remove default scene objects that are not part of the imported asset."""
    for obj in list(bpy.context.scene.objects):
        is_scene_helper = obj.type in {"CAMERA", "LIGHT"}
        is_default_mesh = obj.type == "MESH" and _normalized_name(obj.name) in AUXILIARY_OBJECT_NAMES
        if is_scene_helper or is_default_mesh:
            bpy.data.objects.remove(obj, do_unlink=True)


def relink_textures(input_path: Path) -> None:
    """Resolve texture paths exported from authoring machines with local paths."""
    search_roots = list(input_path.parents)[:4]
    for image in bpy.data.images:
        if image.packed_file:
            continue
        raw_path = str(image.filepath).replace("\\", "/")
        filename = raw_path.rsplit("/", 1)[-1]
        if not filename:
            continue
        candidates = [
            root / filename
            for root in search_roots
        ] + [
            root / directory / filename
            for root in search_roots
            for directory in ("Textures", "textures")
        ]
        texture_path = next((path for path in candidates if path.is_file()), None)
        if texture_path is None:
            continue
        image.filepath = str(texture_path)
        image.reload()


def parse_args() -> argparse.Namespace:
    argv = sys.argv
    values = argv[argv.index("--") + 1:] if "--" in argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(values)


def run() -> int:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    extension = args.input.suffix.casefold()
    if extension == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(args.input))
    elif extension in {".glb", ".gltf"}:
        bpy.ops.import_scene.gltf(filepath=str(args.input))
    else:
        raise ValueError(f"formato não suportado: {extension}")
    remove_preview_helpers()
    relink_textures(args.input)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(args.output),
        export_format="GLB",
        export_animations=True,
        export_skins=True,
        export_morph=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
    )
    if not args.output.is_file():
        raise RuntimeError("Blender não gerou o arquivo GLB")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
