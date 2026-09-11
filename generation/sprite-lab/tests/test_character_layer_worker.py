import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import ai_render_spec  # noqa: E402
import character_layer_worker  # noqa: E402
from image_generation_provider import GenerationResult  # noqa: E402


class CharacterLayerWorkerTests(unittest.TestCase):
    def test_integrates_prompt_request_provider_state_and_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity.png"
            beauty = root / "beauty.png"
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(identity)
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(beauty)
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
            manifest = ai_render_spec.build_reference_manifest(["beauty"])
            provider = mock.Mock()

            def generate(request):
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(request.output_path)
                return GenerationResult("ok", "fake", request.model, request.output_path, {})

            provider.generate.side_effect = generate
            states = []
            first = character_layer_worker.run_character_layer(
                job_id="job", render_spec=spec, reference_manifest=manifest,
                input_images=[identity, beauty], output_dir=root, model="model",
                provider=provider, update_state=states.append,
            )
            second = character_layer_worker.run_character_layer(
                job_id="job", render_spec=spec, reference_manifest=manifest,
                input_images=[identity, beauty], output_dir=root, model="model",
                provider=provider, update_state=states.append,
            )

            self.assertEqual(provider.generate.call_count, 1)
            self.assertEqual(first["status"], "character_complete")
            self.assertTrue(second["resumed"])
            self.assertEqual(states[-1]["stage"], "character_complete")
            self.assertIn("Do not draw any weapon", first["prompt"])

    def test_failed_generation_can_retry_without_rebuilding_structural_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = root / "identity.png"
            beauty = root / "beauty.png"
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(identity)
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(beauty)
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
            manifest = ai_render_spec.build_reference_manifest(["beauty"])
            provider = mock.Mock()

            def succeed(request):
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(request.output_path)
                return GenerationResult("ok", "fake", request.model, request.output_path, {})

            attempts = 0

            def flaky(request):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("provider down")
                return succeed(request)

            provider.generate.side_effect = flaky
            states = []
            arguments = dict(
                job_id="job", render_spec=spec, reference_manifest=manifest,
                input_images=[identity, beauty], output_dir=root, model="model",
                provider=provider, update_state=states.append,
            )
            with self.assertRaisesRegex(RuntimeError, "provider down"):
                character_layer_worker.run_character_layer(**arguments)
            response = character_layer_worker.run_character_layer(**arguments)

            self.assertEqual(provider.generate.call_count, 2)
            self.assertEqual(response["status"], "character_complete")
            self.assertIn("character_failed", [state["stage"] for state in states])


if __name__ == "__main__":
    unittest.main()
