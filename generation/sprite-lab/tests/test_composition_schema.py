import sys
import unittest


SPRITE_LAB = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import composition_schema  # noqa: E402


class CompositionSchemaTests(unittest.TestCase):
    def test_legacy_weapon_becomes_a_component(self) -> None:
        components = composition_schema.normalize_components(
            {
                "weapon_asset_id": "claymore",
            }
        )

        self.assertEqual(len(components), 1)
        self.assertEqual(components[0]["id"], "weapon")
        self.assertEqual(components[0]["asset_id"], "claymore")
        self.assertEqual(components[0]["parent"], "character")
        self.assertEqual(components[0]["attach_to"], "hand_r")
        self.assertEqual(components[0]["fit"]["mode"], "character_height")

    def test_explicit_components_normalize_transforms_and_defaults(self) -> None:
        components = composition_schema.normalize_components(
            {
                "components": [
                    {
                        "id": "shield",
                        "asset_id": "shield_asset",
                        "role": "shield",
                        "attach_to": "hand_l",
                        "transform": {
                            "position": [0.1, -0.2, 0.0],
                            "rotation": [0, 90, 15],
                            "scale": [0.5, 0.5, 0.5],
                        },
                    }
                ]
            }
        )

        self.assertEqual(
            components[0]["transform"],
            {
                "position": [0.1, -0.2, 0.0],
                "rotation": [0.0, 90.0, 15.0],
                "scale": [0.5, 0.5, 0.5],
            },
        )
        self.assertEqual(components[0]["visible"], True)
        self.assertEqual(components[0]["fit"], {"mode": "none", "ratio": 1.0})

    def test_legacy_wrist_offset_is_removed_for_palm_grip(self) -> None:
        components = composition_schema.normalize_components(
            {
                "components": [
                    {
                        "id": "weapon",
                        "asset_id": "axe",
                        "role": "weapon",
                        "parent": "character",
                        "attach_to": "hand_r",
                        "legacy": True,
                        "transform": {
                            "position": [0.015, -0.05, 0.0],
                            "rotation": [0, 90, 90],
                            "scale": [1, 1, 1],
                        },
                    }
                ]
            }
        )

        self.assertEqual(components[0]["transform"]["position"], [0.0, 0.0, 0.0])
        self.assertEqual(components[0]["transform"]["rotation"], [0.0, 90.0, 90.0])

    def test_duplicate_ids_and_unknown_parent_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            composition_schema.normalize_components(
                {
                    "components": [
                        {"id": "a", "asset_id": "one"},
                        {"id": "a", "asset_id": "two"},
                    ]
                }
            )

        with self.assertRaises(ValueError):
            composition_schema.normalize_components(
                {
                    "components": [
                        {"id": "a", "asset_id": "one", "parent": "b"},
                        {"id": "b", "asset_id": "two", "parent": "a"},
                    ]
                }
            )

        with self.assertRaises(ValueError):
            composition_schema.normalize_components(
                {
                    "components": [
                        {
                            "id": "a",
                            "asset_id": "one",
                            "parent": "missing",
                        }
                    ]
                }
            )

    def test_invalid_scale_and_fit_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            composition_schema.normalize_components(
                {
                    "components": [
                        {
                            "id": "prop",
                            "asset_id": "one",
                            "transform": {"scale": [1, 0, 1]},
                        }
                    ]
                }
            )

    def test_two_handed_attachment_requires_two_character_sockets(self) -> None:
        component = composition_schema.normalize_components(
            {
                "components": [
                    {
                        "id": "greatsword",
                        "asset_id": "sword",
                        "role": "weapon",
                        "parent": "character",
                        "attach_to": "hand_r",
                        "attach_to_secondary": "hand_l",
                        "two_hand_axis": "-z",
                    }
                ]
            }
        )[0]
        self.assertEqual(component["attach_to_secondary"], "hand_l")
        self.assertEqual(component["two_hand_axis"], "-z")

        with self.assertRaises(ValueError):
            composition_schema.normalize_components(
                {
                    "components": [
                        {
                            "id": "invalid",
                            "asset_id": "sword",
                            "attach_to_secondary": "hand_l",
                        }
                    ]
                }
            )

        with self.assertRaises(ValueError):
            composition_schema.normalize_components(
                {
                    "components": [
                        {
                            "id": "invalid",
                            "asset_id": "sword",
                            "parent": "scene",
                            "attach_to": "hand_r",
                            "attach_to_secondary": "hand_l",
                        }
                    ]
                }
            )

        with self.assertRaises(ValueError):
            composition_schema.normalize_components(
                {
                    "components": [
                        {"id": f"prop_{index}", "asset_id": "one"}
                        for index in range(composition_schema.MAX_COMPONENTS + 1)
                    ]
                }
            )

        with self.assertRaises(ValueError):
            composition_schema.normalize_components(
                {
                    "components": [
                        {
                            "id": "prop",
                            "asset_id": "one",
                            "fit": {"mode": "unknown", "ratio": 1},
                        }
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
