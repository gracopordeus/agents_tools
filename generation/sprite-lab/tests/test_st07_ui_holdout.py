import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import server  # noqa: E402
import ai_render_spec  # noqa: E402


class St07HoldoutUiTests(unittest.TestCase):
    def test_html_exposes_conditional_holdout_controls_with_safe_defaults(self) -> None:
        html = (SPRITE_LAB / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="gemini-generation-mode"', html)
        self.assertIn('value="single_sheet" selected', html)
        self.assertIn('value="character_weapon_holdout"', html)
        self.assertIn('id="gemini-holdout-controls"', html)
        self.assertIn('id="gemini-weapon-component"', html)
        self.assertIn('id="gemini-weapon-reference-card"', html)
        self.assertIn('id="gemini-holdout-dilation"', html)
        self.assertIn('id="gemini-holdout-tolerance"', html)
        self.assertRegex(html, r'id="gemini-holdout-dilation"[^>]+value="0"')
        self.assertRegex(html, r'id="gemini-holdout-tolerance"[^>]+value="4"')
        self.assertRegex(html, r'id="gemini-holdout-controls"[^>]+ hidden')
        self.assertRegex(html, r'id="gemini-weapon-reference-card"[^>]+ hidden')

    def test_javascript_builds_single_and_layered_payload_contracts(self) -> None:
        source = (SPRITE_LAB / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function selectedAiGenerationMode()", source)
        self.assertIn("function renderWeaponComponents()", source)
        self.assertIn('spec.generation_mode = selectedAiGenerationMode();', source)
        self.assertIn('weapon_component_id: selectedWeaponComponentId()', source)
        self.assertIn('generation_order: ["character", "weapon"]', source)
        self.assertIn('publish_layered_bundle: generationMode === "character_weapon_holdout"', source)
        self.assertIn('holdout_dilation: selectedHoldoutDilation()', source)
        self.assertIn('holdout_tolerance: selectedHoldoutTolerance()', source)
        self.assertIn('renderWeaponComponents();', source)
        self.assertIn('updateHoldoutControls();', source)

    def test_source_fixture_exposes_stable_weapon_component_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source-1"
            source.mkdir()
            for filename in server.GEMINI_CHANNEL_FILES.values():
                Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(source / filename)
            (source / "request.json").write_text(
                json.dumps(
                    {
                        "components": [
                            {
                                "id": "weapon-1",
                                "role": "weapon",
                                "asset_id": "sword",
                                "attach_to": "hand_r",
                                "visible": True,
                            },
                            {"id": "body-1", "role": "character", "visible": True},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(server, "SPRITE_WORK", root):
                listed = server.list_gemini_sources()
            specs = listed[0]["inherited"]["component_specs"]
            self.assertEqual(specs[0]["id"], "weapon-1")
            self.assertEqual(specs[0]["role"], "weapon")
            self.assertEqual(specs[0]["attach_to"], "hand_r")

    def test_single_and_layered_ui_contracts_normalize_with_same_source_fixture(self) -> None:
        source_contract = {
            "components": [
                {"id": "weapon-1", "role": "weapon", "visible": True},
            ]
        }
        single = ai_render_spec.normalize_render_spec(
            {"generation_mode": "single_sheet", "source_contract": source_contract}
        )
        layered = ai_render_spec.normalize_render_spec(
            {
                "generation_mode": "character_weapon_holdout",
                "source_contract": source_contract,
                "layer_contract": {
                    "weapon_component_id": "weapon-1",
                    "generation_order": ["character", "weapon"],
                    "composition_order": ["weapon", "character_holdout"],
                    "layers": [
                        {"id": "weapon", "z": 0},
                        {"id": "character_holdout", "z": 1},
                    ],
                    "preview": None,
                },
            }
        )
        self.assertEqual(single["generation_mode"], "single_sheet")
        self.assertNotIn("layer_contract", single)
        self.assertEqual(layered["generation_mode"], "character_weapon_holdout")
        self.assertEqual(
            layered["layer_contract"]["weapon_component_id"], "weapon-1"
        )

    def test_holdout_parameters_have_bounded_server_defaults(self) -> None:
        self.assertEqual(server.normalize_holdout_dilation(None), 0)
        self.assertEqual(server.normalize_holdout_tolerance(None), 4)
        self.assertEqual(server.normalize_holdout_dilation("8"), 8)
        self.assertEqual(server.normalize_holdout_tolerance(12.0), 12)
        for normalizer in (
            server.normalize_holdout_dilation,
            server.normalize_holdout_tolerance,
        ):
            with self.subTest(normalizer=normalizer.__name__):
                with self.assertRaises(ValueError):
                    normalizer(33)
                with self.assertRaises(ValueError):
                    normalizer(-1)
                with self.assertRaises(ValueError):
                    normalizer("4.5")


if __name__ == "__main__":
    unittest.main()
