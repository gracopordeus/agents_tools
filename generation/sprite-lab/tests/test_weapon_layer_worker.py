import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import ai_render_spec  # noqa: E402
import character_layer_persistence  # noqa: E402
import character_layer_worker  # noqa: E402
import weapon_layer_worker  # noqa: E402
from image_generation_provider import DryRunProvider, GenerationResult  # noqa: E402


class WeaponLayerWorkerTests(unittest.TestCase):
    def _spec(self):
        spec = ai_render_spec.default_render_spec(name="hero")
        spec.update({
            "generation_mode": "character_weapon_holdout",
            "output": {**spec["output"], "width": 1024, "height": 1024},
            "layer_contract": {
                "weapon_component_id": "weapon_1",
                "generation_order": ["character", "weapon"],
                "composition_order": ["weapon", "character_holdout"],
                "layers": [{"id": "weapon", "z": 0}, {"id": "character_holdout", "z": 1}],
            },
            "source_contract": {"components": [{"id": "weapon_1", "role": "weapon"}]},
        })
        return spec

    def test_runs_weapon_after_character_checkpoint_and_emits_specific_states(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            character = root / "character_full.png"
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(character)
            (root / "character_response.json").write_text(
                '{"status":"character_complete","sha256":"%s"}'
                % character_layer_persistence._sha256(character)
            )
            weapon, guide = root / "weapon.png", root / "guide.png"
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(weapon)
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(guide)
            spec = self._spec()
            manifest = [
                {"index": 1, "type": "weapon_reference"},
                {"index": 2, "type": "character_full"},
                {"index": 3, "type": "weapon_guide"},
            ]
            provider = mock.Mock()

            def generate(request):
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(request.output_path)
                return GenerationResult("ok", "fake", request.model, request.output_path, {})

            provider.generate.side_effect = generate
            states = []
            result = weapon_layer_worker.run_weapon_layer(
                job_id="job", render_spec=spec, reference_manifest=manifest,
                input_images=[weapon, character, guide], output_dir=root, model="model",
                provider=provider, update_state=states.append,
            )
            self.assertEqual(result["status"], "weapon_complete")
            self.assertEqual(
                [item["stage"] for item in states],
                ["generating_weapon", "validating_weapon", "weapon_complete"],
            )
            self.assertEqual(provider.generate.call_count, 1)
            self.assertIn("WEAPON LAYER CONTRACT", result["prompt"])

    def test_dry_run_resumes_from_character_checkpoint_and_keeps_two_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity, beauty = root / "identity.png", root / "beauty.png"
            weapon, guide = root / "weapon.png", root / "guide.png"
            for path in (identity, beauty, weapon, guide):
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(path)
            spec = self._spec()
            character_manifest = [
                {"index": 1, "type": "identity"},
                {"index": 2, "type": "beauty"},
            ]
            character_provider = DryRunProvider()
            character_result = character_layer_worker.run_character_layer(
                job_id="job", render_spec=spec, reference_manifest=character_manifest,
                input_images=[identity, beauty], output_dir=root, model="model",
                provider=character_provider, update_state=lambda _: None,
            )
            self.assertEqual(character_result["status"], "character_complete")
            weapon_manifest = [
                {"index": 1, "type": "weapon_reference"},
                {"index": 2, "type": "character_full"},
                {"index": 3, "type": "weapon_guide"},
            ]
            first = weapon_layer_worker.run_weapon_layer(
                job_id="job", render_spec=spec, reference_manifest=weapon_manifest,
                input_images=[weapon, root / "character_full.png", guide], output_dir=root,
                model="model", provider=character_provider, update_state=lambda _: None,
            )
            second = weapon_layer_worker.run_weapon_layer(
                job_id="job", render_spec=spec, reference_manifest=weapon_manifest,
                input_images=[weapon, root / "character_full.png", guide], output_dir=root,
                model="model", provider=character_provider, update_state=lambda _: None,
            )
            self.assertFalse(first["resumed"])
            self.assertTrue(second["resumed"])
            self.assertTrue((root / "character.request.json").is_file())
            self.assertTrue((root / "weapon.request.json").is_file())
            self.assertEqual(json.loads((root / "weapon_response.json").read_text())["status"], "weapon_complete")


if __name__ == "__main__":
    unittest.main()
