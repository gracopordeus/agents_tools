"""Export a saved Sprite Lab relationship as a self-contained GLB."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from blender_semantic_preview import (  # noqa: E402
    apply_animation,
    attach_components,
    attach_weapon,
    bake_two_hand_components,
    find_armature,
    import_asset,
)


def parse_args() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--character", type=Path, required=True)
    parser.add_argument("--animation", type=Path)
    parser.add_argument("--action-name", default="")
    parser.add_argument("--components", default="[]")
    parser.add_argument("--weapon", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(values)


def run() -> int:
    args = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_asset(args.character)

    armature = find_armature()
    action = None
    if args.animation:
        if armature is None:
            raise RuntimeError("composição sem armature para receber a Action")
        action = apply_animation(armature, args.animation, args.action_name)
        if action is None:
            raise RuntimeError(f"Action não encontrada: {args.action_name}")

    components = json.loads(args.components)
    if not isinstance(components, list):
        raise RuntimeError("--components deve conter uma lista JSON")
    if components:
        attach_components(
            components,
            armature,
            {"character_path": str(args.character)},
        )
    elif args.weapon:
        attach_weapon(
            args.weapon,
            armature,
            {"character_path": str(args.character)},
        )

    if action is not None and armature is not None:
        bake_two_hand_components(armature, action)

    bpy.context.scene.frame_set(1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(args.output),
        export_format="GLB",
        # The character GLB carries its complete animation library. Exporting
        # all ACTIONS makes Blender reopen the composition on the character's
        # first action (usually A_TPose), instead of the Action selected by
        # the saved relationship. Keep the delivery artifact deterministic:
        # it contains the active composition Action only.
        export_animations=True,
        export_animation_mode="ACTIVE_ACTIONS",
        export_skins=True,
        export_morph=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
    )
    if not args.output.is_file():
        raise RuntimeError("Blender não gerou o GLB da composição")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
