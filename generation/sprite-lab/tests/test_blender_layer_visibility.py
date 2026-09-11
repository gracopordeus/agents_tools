import sys
import unittest
from pathlib import Path


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import blender_layer_visibility as visibility  # noqa: E402


class FakeObject(dict):
    def __init__(self, name: str, *, parent=None, hide_render: bool = False, **properties):
        super().__init__(properties)
        self.name = name
        self.parent = parent
        self.hide_render = hide_render


class BlenderLayerVisibilityTests(unittest.TestCase):
    def test_weapon_pass_shows_only_selected_weapon_component(self) -> None:
        selected_root = FakeObject(
            "sprite_component_weapon_1",
            conditioning_component_id="weapon_1",
            conditioning_component_role="weapon",
        )
        other_root = FakeObject(
            "sprite_component_weapon_2",
            conditioning_component_id="weapon_2",
            conditioning_component_role="weapon",
        )
        selected = FakeObject("SwordMesh", parent=selected_root)
        other_weapon = FakeObject("AxeMesh", parent=other_root)
        character = FakeObject("BodyMesh")
        accessory = FakeObject(
            "CapeMesh", conditioning_component_role="accessory"
        )

        with visibility.weapon_only_visibility(
            [character, selected, other_weapon, accessory], "weapon_1"
        ):
            self.assertTrue(character.hide_render)
            self.assertFalse(selected.hide_render)
            self.assertTrue(other_weapon.hide_render)
            self.assertTrue(accessory.hide_render)

        self.assertTrue(
            all(
                not obj.hide_render
                for obj in (character, selected, other_weapon, accessory)
            )
        )

    def test_weapon_pass_metadata_preserves_component_and_64_cell_alignment(self) -> None:
        primary = {
            "directions": [f"r{index}" for index in range(1, 9)],
            "sampled_frames": list(range(20, 28)),
            "camera": {"type": "ORTHO", "ortho_scale": 4.0},
            "cell": [512, 512],
            "cells": [
                {"row": row, "column": column, "frame": 20 + column}
                for row in range(8)
                for column in range(8)
            ],
        }
        component = {
            "id": "weapon_1",
            "role": "weapon",
            "attach_to": "hand_r",
            "transform": {
                "position": [1, 2, 3],
                "rotation": [4, 5, 6],
                "scale": [1, 1, 1],
            },
            "fit": {"mode": "character_height", "ratio": 0.8},
        }

        metadata = visibility.weapon_pass_metadata(primary, component)

        self.assertEqual(len(metadata["cells"]), 64)
        self.assertEqual(metadata["camera"], primary["camera"])
        self.assertEqual(metadata["directions"], primary["directions"])
        self.assertEqual(metadata["sampled_frames"], primary["sampled_frames"])
        for field in ("role", "attach_to", "transform", "fit"):
            self.assertEqual(metadata["component"][field], component[field])
        component["transform"]["position"][0] = 99
        self.assertEqual(metadata["component"]["transform"]["position"][0], 1)

    def test_character_pass_metadata_copies_primary_alignment_contract(self) -> None:
        primary = {
            "directions": [f"r{index}" for index in range(1, 9)],
            "sampled_frames": list(range(10, 18)),
            "camera": {"type": "ORTHO", "ortho_scale": 3.8},
            "cell": [512, 512],
        }
        metadata = visibility.character_pass_metadata(
            primary, {"foot_anchor": [256, 440]}
        )

        self.assertEqual(metadata["directions"], primary["directions"])
        self.assertEqual(metadata["sampled_frames"], primary["sampled_frames"])
        self.assertEqual(metadata["camera"], primary["camera"])
        self.assertEqual(metadata["cell"], primary["cell"])
        self.assertEqual(metadata["foot_anchor"], [256, 440])
        self.assertTrue(metadata["transparent_background"])
        self.assertEqual(len(metadata["directions"]) * len(metadata["sampled_frames"]), 64)
        primary["camera"]["ortho_scale"] = 99
        self.assertEqual(metadata["camera"]["ortho_scale"], 3.8)

    def test_character_pass_hides_only_weapon_component_meshes(self) -> None:
        weapon_root = FakeObject(
            "sprite_component_weapon_1",
            conditioning_component_role="weapon",
        )
        weapon_mesh = FakeObject("SwordMesh", parent=weapon_root)
        character = FakeObject("BodyMesh")
        hidden_accessory = FakeObject(
            "CapeMesh",
            hide_render=True,
            conditioning_component_role="accessory",
        )

        with visibility.character_only_visibility(
            [character, weapon_mesh, hidden_accessory]
        ):
            self.assertFalse(character.hide_render)
            self.assertTrue(weapon_mesh.hide_render)
            self.assertTrue(hidden_accessory.hide_render)

        self.assertFalse(character.hide_render)
        self.assertFalse(weapon_mesh.hide_render)
        self.assertTrue(hidden_accessory.hide_render)

    def test_character_pass_restores_visibility_after_exception(self) -> None:
        weapon = FakeObject(
            "WeaponMesh",
            conditioning_component_role="weapon",
        )

        with self.assertRaisesRegex(RuntimeError, "render failed"):
            with visibility.character_only_visibility([weapon]):
                self.assertTrue(weapon.hide_render)
                raise RuntimeError("render failed")

        self.assertFalse(weapon.hide_render)

    def test_weapon_pass_rejects_missing_selected_component(self) -> None:
        character = FakeObject("BodyMesh")

        with self.assertRaisesRegex(ValueError, "weapon_component_id 'missing'"):
            with visibility.weapon_only_visibility([character], "missing"):
                pass


if __name__ == "__main__":
    unittest.main()
