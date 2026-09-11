import sys
import unittest
from pathlib import Path


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import ai_render_spec  # noqa: E402


class AiRenderSpecTests(unittest.TestCase):
    def _layered_character_spec(self) -> dict:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec.update({
            "generation_mode": "character_weapon_holdout",
            "layer_contract": {
                "weapon_component_id": "weapon_1",
                "generation_order": ["character", "weapon"],
                "composition_order": ["weapon", "character_holdout"],
                "layers": [
                    {"id": "weapon", "z": 0},
                    {"id": "character_holdout", "z": 1},
                ],
            },
            "source_contract": {
                "camera": {"type": "orthographic", "preset": "isometric"},
                "action": {"clip_name": "attack"},
                "components": [
                    {"id": "weapon_1", "role": "weapon", "attach_to": "hand_r"}
                ],
            },
        })
        return spec

    def test_compile_character_layer_prompt_is_isolated_and_aligned(self) -> None:
        manifest = ai_render_spec.build_reference_manifest(
            ["beauty", "lineart", "bones", "frame_control"]
        )

        prompt = ai_render_spec.compile_layer_prompt(
            self._layered_character_spec(), manifest, layer="character"
        )

        self.assertIn("character-only structural reference", prompt)
        self.assertIn("Do not draw any weapon, shield or visible prop", prompt)
        self.assertIn("Preserve the exact gripping hand pose", prompt)
        self.assertIn("exactly 64 cells", prompt)
        self.assertIn("original direction of each row", prompt)
        self.assertIn("original animation phase of each column", prompt)
        self.assertIn("fully transparent RGBA background", prompt)
        self.assertIn('"action": "attack"', prompt)
        self.assertNotIn('"role": "weapon"', prompt)
        self.assertNotIn("compose the weapon", prompt.casefold())
        self.assertEqual(
            prompt,
            ai_render_spec.compile_layer_prompt(
                self._layered_character_spec(), manifest, layer="character"
            ),
        )

    def test_compile_layer_prompt_rejects_unknown_layer_and_single_sheet(self) -> None:
        manifest = ai_render_spec.build_reference_manifest(["beauty"])
        with self.assertRaisesRegex(ValueError, "layer.*character.*weapon"):
            ai_render_spec.compile_layer_prompt(
                self._layered_character_spec(), manifest, layer="armor"
            )
        with self.assertRaisesRegex(ValueError, "character_weapon_holdout"):
            ai_render_spec.compile_layer_prompt(
                ai_render_spec.default_render_spec(), manifest, layer="character"
            )

    def test_compile_weapon_layer_prompt_is_isolated_and_spatially_aligned(self) -> None:
        spec = self._layered_character_spec()
        component = spec["source_contract"]["components"][0]
        component.update(
            {
                "asset_id": "axe_double_asset",
                "hand": "right",
                "transform": {
                    "position": [1.0, 2.0, 3.0],
                    "rotation": [0.0, 90.0, 0.0],
                    "scale": [1.25, 1.25, 1.25],
                },
            }
        )
        manifest = [
            {"index": 1, "type": "weapon_reference", "name": "weapon design"},
            {"index": 2, "type": "character_full", "name": "character_full.png"},
            {"index": 3, "type": "weapon_guide", "name": "weapon_only.png"},
        ]

        prompt = ai_render_spec.compile_layer_prompt(spec, manifest, layer="weapon")

        self.assertIn("IMAGE 1", prompt)
        self.assertIn("authoritative weapon design", prompt)
        self.assertIn("IMAGE 2", prompt)
        self.assertIn("character_full", prompt)
        self.assertIn("positioning and style context only", prompt)
        self.assertIn("IMAGE 3", prompt)
        self.assertIn("authoritative position, orientation, scale and animation phase", prompt)
        self.assertIn("fully transparent RGBA", prompt)
        self.assertIn("Do not draw any character, hand, body part", prompt)
        self.assertIn("additional prop", prompt)
        self.assertIn("exactly 64 cells", prompt)
        self.assertIn('"asset_id": "axe_double_asset"', prompt)
        self.assertIn('"attach_to": "hand_r"', prompt)
        self.assertIn('"hand": "right"', prompt)
        self.assertIn('"transform": {', prompt)
        self.assertIn("original direction of each row", prompt)
        self.assertNotIn('"name": "axe_double_asset"', prompt)
        self.assertEqual(
            prompt,
            ai_render_spec.compile_layer_prompt(spec, manifest, layer="weapon"),
        )

    def test_select_weapon_component_falls_back_to_single_visible_weapon(self) -> None:
        component = {
            "id": "weapon_1",
            "asset_id": "sword_asset",
            "role": "weapon",
            "attach_to": "hand_r",
            "hand": "right",
            "path": "assets/sword.glb",
        }

        selected = ai_render_spec.select_weapon_component(
            {"components": [component]}
        )

        self.assertEqual(
            selected,
            {
                "id": "weapon_1",
                "asset_id": "sword_asset",
                "attach_to": "hand_r",
                "hand": "right",
                "path": "assets/sword.glb",
            },
        )
        component["asset_id"] = "mutated"
        self.assertEqual(selected["asset_id"], "sword_asset")

    def test_select_weapon_component_prioritizes_explicit_id(self) -> None:
        selected = ai_render_spec.select_weapon_component(
            {
                "components": [
                    {"id": "prop_1", "role": "prop"},
                    {"id": "weapon_1", "role": "weapon"},
                ]
            },
            weapon_component_id="weapon_1",
        )

        self.assertEqual(selected["id"], "weapon_1")

    def test_select_weapon_component_rejects_missing_weapon(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "nenhuma arma visível.*prop_1.*prop"
        ):
            ai_render_spec.select_weapon_component(
                {
                    "components": [
                        {
                            "id": "prop_1",
                            "asset_id": "weapon_named_asset",
                            "role": "prop",
                        }
                    ]
                }
            )

    def test_select_weapon_component_rejects_two_visible_weapons(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "uma única arma visível.*weapon_1.*weapon_2"
        ):
            ai_render_spec.select_weapon_component(
                {
                    "components": [
                        {"id": "weapon_1", "role": "weapon"},
                        {"id": "weapon_2", "role": "weapon"},
                    ]
                }
            )

    def test_select_weapon_component_ignores_hidden_weapon(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "nenhuma arma visível.*hidden_weapon.*weapon"
        ):
            ai_render_spec.select_weapon_component(
                {
                    "components": [
                        {
                            "id": "hidden_weapon",
                            "role": "weapon",
                            "visible": False,
                        }
                    ]
                }
            )

    def test_select_weapon_component_rejects_unknown_explicit_id(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "weapon_component_id 'missing'.*weapon_1.*weapon"
        ):
            ai_render_spec.select_weapon_component(
                {"components": [{"id": "weapon_1", "role": "weapon"}]},
                weapon_component_id="missing",
            )

    def test_select_weapon_component_rejects_explicit_non_weapon_role(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "weapon_component_id 'shield_1'.*shield"
        ):
            ai_render_spec.select_weapon_component(
                {"components": [{"id": "shield_1", "role": "shield"}]},
                weapon_component_id="shield_1",
            )

    def test_layered_normalization_rejects_source_contract_without_selected_weapon(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec.update(
            {
                "generation_mode": "character_weapon_holdout",
                "layer_contract": {
                    "weapon_component_id": "sword_main",
                    "generation_order": ["character", "weapon"],
                    "composition_order": ["weapon", "character_holdout"],
                    "layers": [
                        {"id": "weapon", "z": 0},
                        {"id": "character_holdout", "z": 1},
                    ],
                },
                "source_contract": {
                    "components": [{"id": "prop_1", "role": "prop"}]
                },
            }
        )

        with self.assertRaisesRegex(ValueError, "weapon_component_id 'sword_main'"):
            ai_render_spec.normalize_render_spec(spec)

    def test_generation_mode_defaults_to_single_sheet_without_layer_contract(self) -> None:
        spec = ai_render_spec.normalize_render_spec({})

        self.assertEqual(spec["generation_mode"], "single_sheet")
        self.assertNotIn("layer_contract", spec)

    def test_character_weapon_holdout_normalizes_canonical_layer_contract(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec.update(
            {
                "generation_mode": "character_weapon_holdout",
                "layer_contract": {
                    "weapon_component_id": "sword_main",
                    "generation_order": ["character", "weapon"],
                    "composition_order": ["weapon", "character_holdout"],
                    "layers": [
                        {"id": "weapon", "z": 0, "file": "layers/weapon.png"},
                        {
                            "id": "character_holdout",
                            "z": 1,
                            "file": "layers/character_holdout.png",
                        },
                    ],
                    "preview": "composite_preview.png",
                },
                "source_contract": {
                    "components": [
                        {
                            "id": "sword_main",
                            "asset_id": "sword_asset",
                            "role": "weapon",
                            "attach_to": "hand_r",
                            "hand": "right",
                            "path": "assets/sword.glb",
                        }
                    ]
                },
            }
        )

        normalized = ai_render_spec.normalize_render_spec(spec)

        self.assertEqual(normalized["generation_mode"], "character_weapon_holdout")
        self.assertEqual(normalized["layer_contract"], spec["layer_contract"])
        self.assertEqual(normalized["output"]["grid"], {"rows": 8, "columns": 8})
        self.assertEqual(normalized["output"]["width"], normalized["output"]["height"])

    def test_character_weapon_holdout_requires_explicit_weapon_component_id(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec.update(
            {
                "generation_mode": "character_weapon_holdout",
                "layer_contract": {
                    "generation_order": ["character", "weapon"],
                    "composition_order": ["weapon", "character_holdout"],
                    "layers": [
                        {"id": "weapon", "z": 0},
                        {"id": "character_holdout", "z": 1},
                    ],
                },
            }
        )

        with self.assertRaisesRegex(ValueError, "layer_contract.weapon_component_id"):
            ai_render_spec.normalize_render_spec(spec)

    def test_character_weapon_holdout_rejects_invalid_composition_order(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec.update(
            {
                "generation_mode": "character_weapon_holdout",
                "layer_contract": {
                    "weapon_component_id": "sword_main",
                    "generation_order": ["character", "weapon"],
                    "composition_order": ["character_holdout", "weapon"],
                    "layers": [
                        {"id": "weapon", "z": 0},
                        {"id": "character_holdout", "z": 1},
                    ],
                },
            }
        )

        with self.assertRaisesRegex(ValueError, "layer_contract.composition_order"):
            ai_render_spec.normalize_render_spec(spec)

    def test_character_weapon_holdout_rejects_invalid_generation_order(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec.update(
            {
                "generation_mode": "character_weapon_holdout",
                "layer_contract": {
                    "weapon_component_id": "sword_main",
                    "generation_order": ["weapon", "character"],
                    "composition_order": ["weapon", "character_holdout"],
                    "layers": [
                        {"id": "weapon", "z": 0},
                        {"id": "character_holdout", "z": 1},
                    ],
                },
            }
        )

        with self.assertRaisesRegex(ValueError, "layer_contract.generation_order"):
            ai_render_spec.normalize_render_spec(spec)

    def test_character_weapon_holdout_rejects_absolute_layer_file_paths(self) -> None:
        for absolute_path in (
            "/home/ggnp/secret/weapon.png",
            r"C:\secret\weapon.png",
            r"\\server\share\weapon.png",
        ):
            spec = ai_render_spec.default_render_spec(name="hero")
            spec.update(
                {
                    "generation_mode": "character_weapon_holdout",
                    "layer_contract": {
                        "weapon_component_id": "sword_main",
                        "generation_order": ["character", "weapon"],
                        "composition_order": ["weapon", "character_holdout"],
                        "layers": [
                            {"id": "weapon", "z": 0, "file": absolute_path},
                            {"id": "character_holdout", "z": 1},
                        ],
                    },
                }
            )

            with self.subTest(path=absolute_path), self.assertRaisesRegex(
                ValueError, r"layer_contract\.layers\[0\]\.file"
            ):
                ai_render_spec.normalize_render_spec(spec)

    def test_character_weapon_holdout_rejects_absolute_preview_paths(self) -> None:
        for absolute_path in (
            "/etc/passwd",
            r"D:\private\preview.png",
            r"\\server\share\preview.png",
        ):
            spec = ai_render_spec.default_render_spec(name="hero")
            spec.update(
                {
                    "generation_mode": "character_weapon_holdout",
                    "layer_contract": {
                        "weapon_component_id": "sword_main",
                        "generation_order": ["character", "weapon"],
                        "composition_order": ["weapon", "character_holdout"],
                        "layers": [
                            {"id": "weapon", "z": 0},
                            {"id": "character_holdout", "z": 1},
                        ],
                        "preview": absolute_path,
                    },
                }
            )

            with self.subTest(path=absolute_path), self.assertRaisesRegex(
                ValueError, "layer_contract.preview"
            ):
                ai_render_spec.normalize_render_spec(spec)

    def test_default_spec_is_eight_by_eight_and_uses_canonical_directions(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")

        self.assertEqual(spec["version"], "2.0")
        self.assertEqual(spec["output"]["grid"], {"rows": 8, "columns": 8})
        self.assertEqual((spec["output"]["width"], spec["output"]["height"]), (2048, 2048))
        self.assertEqual(spec["output"]["background"], "transparent")
        self.assertEqual([row["id"] for row in spec["rows"]], [
            "north", "north_east", "east", "south_east",
            "south", "south_west", "west", "north_west",
        ])

    def test_default_structural_reference_is_beauty_only(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")

        self.assertTrue(spec["references"]["identity"]["enabled"])
        self.assertTrue(spec["references"]["beauty"]["enabled"])
        for channel in ("bones", "lineart", "frame_control"):
            self.assertFalse(spec["references"][channel]["enabled"])

    def test_output_size_accepts_1024_and_is_dynamic_in_provider_prompt(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec["output"]["width"] = 1024
        spec["output"]["height"] = 1024

        normalized = ai_render_spec.normalize_render_spec(spec)
        self.assertEqual((normalized["output"]["width"], normalized["output"]["height"]), (1024, 1024))
        prompt = ai_render_spec.compile_provider_prompt(
            spec,
            ai_render_spec.build_reference_manifest(["beauty", "bones"]),
            provider="google",
        )
        self.assertIn("1024x1024 PNG spritesheet", prompt)
        self.assertNotIn("2048x2048 PNG spritesheet", prompt)

    def test_output_size_rejects_non_square_or_unsupported_dimensions(self) -> None:
        for width, height in ((512, 512), (1024, 2048), (4096, 4096)):
            spec = ai_render_spec.default_render_spec(name="hero")
            spec["output"]["width"] = width
            spec["output"]["height"] = height
            with self.subTest(width=width, height=height), self.assertRaisesRegex(
                ValueError, "1024x1024 ou 2048x2048"
            ):
                ai_render_spec.normalize_render_spec(spec)

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

    def test_identity_lineart_is_inserted_after_identity_and_is_explicit(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        manifest = ai_render_spec.build_reference_manifest(
            ["beauty", "bones"],
            identity_name="concept.png",
            include_identity_lineart=True,
        )

        self.assertEqual(
            [(item["index"], item["type"]) for item in manifest],
            [
                (1, "identity"),
                (2, "identity_lineart"),
                (3, "beauty"),
                (4, "bones"),
            ],
        )
        self.assertEqual(manifest[1]["role"], "identity_contour_guide")
        prompt = ai_render_spec.compile_provider_prompt(
            spec,
            manifest,
            provider="google",
        )
        self.assertIn("Use IMAGE 2, the lineart derived from the identity reference", prompt)
        self.assertIn("IMAGE 1 remains authoritative for colors", prompt)
        self.assertIn("second is the lineart derived from the authoritative character reference", prompt)

    def test_canny_identity_guide_is_named_in_manifest_and_prompt(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        manifest = ai_render_spec.build_reference_manifest(
            ["beauty"],
            identity_name="concept.png",
            include_identity_lineart=True,
            identity_lineart_mode="canny_edges",
        )

        self.assertEqual(manifest[1]["type"], "identity_lineart")
        self.assertEqual(manifest[1]["guide_mode"], "canny_edges")
        self.assertIn("canny edges", manifest[1]["name"])
        prompt = ai_render_spec.compile_provider_prompt(
            spec,
            manifest,
            provider="google",
        )
        self.assertIn("second is the Canny edge guide derived from the authoritative character reference", prompt)
        self.assertIn("the Canny edge map derived from the identity reference", prompt)

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

    def test_component_contract_does_not_invent_a_prop_name(self) -> None:
        spec = ai_render_spec.default_render_spec(name="hero")
        spec["source_contract"] = {
            "components": [
                {
                    "id": "component_7",
                    "role": "attachment",
                    "asset_id": "asset_7",
                    "attach_to": "spine",
                }
            ]
        }

        prompt = ai_render_spec.compile_prompt(
            spec,
            ai_render_spec.build_reference_manifest(["beauty", "bones", "lineart"]),
        )

        self.assertIn('"name": "asset_7"', prompt)
        self.assertNotIn('"name": "prop"', prompt)

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
