import sys
import unittest
from pathlib import Path


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import orientation_contract  # noqa: E402
import sprite_render  # noqa: E402


class OrientationContractTests(unittest.TestCase):
    def test_default_orientation_uses_rest_pose_and_negative_y(self) -> None:
        manifest = orientation_contract.normalize_orientation()

        self.assertEqual(manifest["source"], "rest_pose")
        self.assertEqual(manifest["local_forward_axis"], "-Y")
        self.assertEqual(manifest["reference_bone"], "root")
        self.assertEqual(orientation_contract.axis_vector("-Y"), (0.0, -1.0, 0.0))

    def test_character_uses_matching_ual2_tpose(self) -> None:
        character_id = "character-ual2"
        manifest = sprite_render._character_orientation(
            character_id,
            [
                {
                    "id": "roll",
                    "asset_id": "animation-ual1",
                    "category": "dodge",
                },
                {
                    "id": "tpose-ual1",
                    "asset_id": "animation-ual1",
                    "asset_name": "UAL1_Standard",
                    "category": "tpose",
                    "action_name": "Armature|Armature|A_TPose",
                },
                {
                    "id": "tpose-ual2",
                    "asset_id": character_id,
                    "asset_name": "UAL2_Standard",
                    "category": "tpose",
                    "action_name": "Armature|Armature|A_TPose",
                },
            ],
        )

        self.assertEqual(manifest["source"], "rest_pose")
        self.assertEqual(manifest["rest_pose_id"], "tpose-ual2")
        self.assertEqual(manifest["character_asset_id"], character_id)
        self.assertEqual(manifest["local_forward_axis"], "-Y")

    def test_orientation_override_is_explicit_and_validated(self) -> None:
        manifest = sprite_render._character_orientation(
            "character",
            [],
            {"local_forward_axis": "+X", "yaw_offset_degrees": 90},
        )

        self.assertEqual(manifest["source"], "default")
        self.assertEqual(manifest["local_forward_axis"], "+X")
        self.assertEqual(manifest["yaw_offset_degrees"], 90.0)
        with self.assertRaises(ValueError):
            orientation_contract.normalize_orientation(
                {"local_forward_axis": "Z"}
            )


if __name__ == "__main__":
    unittest.main()
