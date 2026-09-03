import sys
import unittest
from pathlib import Path


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import ai_render_spec  # noqa: E402


class AiRenderSpecTests(unittest.TestCase):
    def test_default_spec_is_eight_by_eight_and_uses_canonical_directions(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")

        self.assertEqual(spec["version"], "2.0")
        self.assertEqual(spec["output"]["grid"], {"rows": 8, "columns": 8})
        self.assertEqual(spec["output"]["background"], "transparent")
        self.assertEqual([row["id"] for row in spec["rows"]], [
            "north", "north_east", "east", "south_east",
            "south", "south_west", "west", "north_west",
        ])

    def test_reference_manifest_preserves_provider_input_order(self) -> None:
        manifest = ai_render_spec.build_reference_manifest(
            ["bones", "lineart", "frame_control"],
            identity_name="concept.jpeg",
        )

        self.assertEqual(
            [(item["index"], item["type"]) for item in manifest],
            [
                (1, "identity"),
                (2, "bones"),
                (3, "lineart"),
                (4, "frame_control"),
            ],
        )
        self.assertEqual(manifest[1]["role"], "pose_skeleton_motion")
        self.assertEqual(manifest[3]["role"], "cell_boundary_control")
        self.assertIn("black guide lines", manifest[3]["does_not_control"])
        self.assertEqual(manifest[0]["name"], "concept.jpeg")
        self.assertEqual(manifest[0]["role"], "authoritative_visual_identity")

    def test_compiler_contains_fixed_contract_and_cell_overrides(self) -> None:
        spec = ai_render_spec.default_render_spec(mode="prop_catalog", name="props")
        spec["rows"][0]["name"] = "House"
        spec["rows"][0]["description"] = "Small Nordic timber house."
        spec["rows"][0]["columns"]["cells"] = [
            {"column": 1, "description": "House with snow on the roof."}
        ]

        prompt = ai_render_spec.compile_prompt(
            spec,
            ai_render_spec.build_reference_manifest(["beauty", "lineart"]),
            "Keep the palette restrained.",
        )

        self.assertIn("Create exactly one 8-column by 8-row", prompt)
        self.assertIn("IMAGE 2:", prompt)
        self.assertIn("BEAUTY_REFERENCE", prompt)
        self.assertIn("Asset mode: prop_catalog", prompt)
        self.assertIn("ROW 1 — House", prompt)
        self.assertIn("Column 1: House with snow on the roof.", prompt)
        self.assertIn("Keep the palette restrained.", prompt)
        self.assertIn("IDENTITY TRANSFER CONTRACT — HIGHEST VISUAL AUTHORITY", prompt)
        self.assertIn("identity reference for every visible appearance", prompt)

    def test_compiler_rejects_conflicting_direction_overrides(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")

        with self.assertRaisesRegex(ValueError, "R1.*SOUTH.*NORTH"):
            ai_render_spec.compile_prompt(
                spec,
                ai_render_spec.build_reference_manifest(["bones", "lineart"]),
                "R1 = SOUTH; R5 faces NORTH.",
            )

    def test_provider_prompt_names_identity_and_repeats_physical_input_order(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        manifest = ai_render_spec.build_reference_manifest(
            ["beauty", "bones", "lineart", "frame_control"],
            identity_name="concept.jpeg",
        )

        prompt = ai_render_spec.compile_provider_prompt(
            spec,
            manifest,
            provider="openai",
        )

        self.assertIn("reference image concept.jpeg", prompt)
        self.assertIn("first image is the authoritative character reference (concept.jpeg)", prompt)
        self.assertIn("second is the aligned beauty spritesheet", prompt)
        self.assertIn("Use the 5 uploaded images in this order", prompt)
        self.assertIn("fully transparent RGBA background", prompt)
        self.assertNotIn("PROVIDER INPUT AND DELIVERY CONTRACT", prompt)

    def test_character_rows_are_normalized_by_position_and_prompt_is_explicit(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec["rows"][0].update({"id": "north", "vector": [0, 1]})
        spec["rows"][4].update({"id": "south", "vector": [0, -1]})

        normalized = ai_render_spec.normalize_render_spec(spec)
        self.assertEqual(normalized["rows"][0]["id"], "north")
        self.assertEqual(normalized["rows"][0]["vector"], [0, 1])
        self.assertEqual(normalized["rows"][4]["id"], "south")
        self.assertEqual(normalized["rows"][4]["vector"], [0, -1])

        prompt = ai_render_spec.compile_prompt(
            spec,
            ai_render_spec.build_reference_manifest(["bones", "lineart"]),
        )
        self.assertNotIn("R1=NORTH", prompt)
        self.assertNotIn("R5=SOUTH", prompt)
        self.assertNotIn("sampled_frames", prompt)

    def test_character_prompt_does_not_turn_metadata_into_appearance(self) -> None:
        spec = ai_render_spec.default_render_spec(name="viking_warrior")
        spec["asset"]["global_description"] = "Classic Nordic fantasy comic."
        spec["asset"]["style"] = {
            "preset": "nordic_comic",
            "description": "Heroic anatomy and fur clothing.",
        }

        prompt = ai_render_spec.compile_prompt(
            spec,
            ai_render_spec.build_reference_manifest(["beauty", "bones", "lineart"]),
        )

        self.assertNotIn("viking_warrior", prompt)
        self.assertNotIn("Classic Nordic fantasy comic", prompt)
        self.assertNotIn("Heroic anatomy and fur clothing", prompt)
        self.assertIn("do not replace it with a generic archetype", prompt.casefold())

    def test_empty_background_uses_fixed_transparency_in_prompt(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec["output"]["background"] = ""
        prompt = ai_render_spec.compile_prompt(
            spec,
            ai_render_spec.build_reference_manifest(["bones", "lineart"]),
        )
        self.assertIn("fully transparent RGBA background", prompt)

    def test_lemon_green_background_is_explicit_for_image_provider_prompt(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec["output"]["background"] = "#00FF00"
        prompt = ai_render_spec.compile_provider_prompt(
            spec,
            ai_render_spec.build_reference_manifest(["beauty", "bones", "lineart"]),
            provider="google",
        )

        self.assertIn("pure lemon-green background (#00FF00)", prompt)
        self.assertIn("Do not use transparency, gradients, shadows", prompt)
        self.assertNotIn("fully transparent RGBA background in every empty pixel", prompt)

    def test_structural_component_is_attached_without_reposing_character(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec["source_contract"] = {
            "components": [
                {
                    "name": "Axe",
                    "role": "weapon",
                    "attach_to": "hand_r",
                    "hand": "right",
                }
            ],
            "directions": [
                {"row": index, "id": direction, "vector": vector}
                for index, (direction, vector) in enumerate(
                    [
                        ("south", [0, -1]),
                        ("south_east", [1, -1]),
                        ("east", [1, 0]),
                        ("north_east", [1, 1]),
                        ("north", [0, 1]),
                        ("north_west", [-1, 1]),
                        ("west", [-1, 0]),
                        ("south_west", [-1, -1]),
                    ],
                    start=1,
                )
            ],
        }
        prompt = ai_render_spec.compile_prompt(
            spec,
            ai_render_spec.build_reference_manifest(
                ["beauty", "bones", "lineart", "frame_control"]
            ),
        )
        self.assertIn('"name": "Axe"', prompt)
        self.assertIn('"attach_to": "hand_r"', prompt)
        self.assertIn('"hand": "right"', prompt)
        self.assertIn("Preserve every component listed in spritesheetContract", prompt)
        self.assertIn("non-authoritative; do not copy internal linework", prompt)
        self.assertNotIn("The declared weapon is mandatory", prompt)
        self.assertNotIn("head-and-handle silhouette", prompt)
        self.assertNotIn("R1=SOUTH", prompt)
        self.assertNotIn("R5=NORTH", prompt)
        self.assertIn('{"row": 1, "id": "south", "vector": [0, -1]}', prompt)
        self.assertIn('{"row": 5, "id": "north", "vector": [0, 1]}', prompt)
        self.assertIn("spritesheetContract:", prompt)
        self.assertLess(len(prompt), 4500)

    def test_component_contract_is_agnostic_for_non_weapon_props(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec["source_contract"] = {
            "components": [
                {
                    "name": "Lantern",
                    "role": "accessory",
                    "attach_to": "spine",
                }
            ]
        }

        prompt = ai_render_spec.compile_prompt(
            spec,
            ai_render_spec.build_reference_manifest(["beauty", "bones", "lineart"]),
        )

        self.assertIn('"name": "Lantern"', prompt)
        self.assertIn('"attach_to": "spine"', prompt)
        self.assertIn("Preserve every component listed in spritesheetContract", prompt)
        self.assertNotIn("weapon", prompt.casefold())
        self.assertNotIn("axe", prompt.casefold())

    def test_source_contract_order_is_persisted_in_render_spec_rows(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec["source_contract"] = {
            "directions": [
                {"row": 1, "id": "south", "vector": [0, -1]},
                {"row": 2, "id": "south_east", "vector": [1, -1]},
                {"row": 3, "id": "east", "vector": [1, 0]},
                {"row": 4, "id": "north_east", "vector": [1, 1]},
                {"row": 5, "id": "north", "vector": [0, 1]},
                {"row": 6, "id": "north_west", "vector": [-1, 1]},
                {"row": 7, "id": "west", "vector": [-1, 0]},
                {"row": 8, "id": "south_west", "vector": [-1, -1]},
            ]
        }

        normalized = ai_render_spec.normalize_render_spec(spec)

        self.assertEqual(
            [row["id"] for row in normalized["rows"]],
            [
                "south", "south_east", "east", "north_east",
                "north", "north_west", "west", "south_west",
            ],
        )
        self.assertEqual(normalized["rows"][0]["vector"], [0, -1])
        self.assertEqual(normalized["rows"][4]["vector"], [0, 1])
        self.assertEqual(normalized["rows"][0]["description"], "Character facing south.")
        self.assertEqual(normalized["rows"][4]["description"], "Character facing north.")

    def test_rows_and_cells_can_be_excluded_from_prompt_without_removing_contract(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec["prompt_options"] = {"include_rows": True, "include_cells": True}
        spec["rows"][0]["include_in_prompt"] = False
        spec["rows"][1]["columns"]["cells"] = [
            {"column": 1, "description": "Do not include this note.", "include_in_prompt": False},
            {"column": 2, "description": "Include this note.", "include_in_prompt": True},
        ]

        prompt = ai_render_spec.compile_prompt(
            spec,
            ai_render_spec.build_reference_manifest(["bones", "lineart"]),
        )
        self.assertNotIn("R1=NORTH [0, 1]", prompt)
        self.assertNotIn("ROW 1 — South", prompt)
        self.assertNotIn("Do not include this note.", prompt)
        self.assertIn("C2: Include this note.", prompt)


if __name__ == "__main__":
    unittest.main()
