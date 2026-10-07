import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import character_layer_persistence  # noqa: E402
import weapon_layer_persistence  # noqa: E402
from image_generation_provider import GenerationRequest, GenerationResult  # noqa: E402


class WeaponLayerPersistenceTests(unittest.TestCase):
    def _request(self, root: Path, *, prompt: str = "weapon") -> GenerationRequest:
        weapon = root / "weapon_reference.png"
        character = root / "character_full.png"
        guide = root / "weapon_guide.png"
        for path in (weapon, character, guide):
            if not path.is_file():
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(path)
        return GenerationRequest(
            "job", prompt, (weapon, character, guide), root / "weapon_full.png", "model",
            {
                "output_size": [1024, 1024],
                "character_dependency": {"path": "character_full.png", "sha256": character_layer_persistence._sha256(character)},
            },
            generation_role="weapon", base_image=weapon,
            reference_images=(character, guide),
        )

    def _approve_character(self, root: Path) -> None:
        character = root / "character_full.png"
        image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
        for x in range(140, 240):
            for y in range(140, 240):
                image.putpixel((x, y), (30, 40, 50, 255))
        image.save(character)
        response = {
            "status": "character_complete",
            "generation_role": "character",
            "canonical_input": "character_full.png",
            "sha256": character_layer_persistence._sha256(character),
            "request_fingerprint": "character-request",
        }
        (root / "character_response.json").write_text(json.dumps(response))

    def test_requires_approved_character_before_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._request(root)
            generate = mock.Mock()
            with self.assertRaisesRegex(weapon_layer_persistence.WeaponPrerequisiteError, "character_full"):
                weapon_layer_persistence.run_weapon_stage(root, request, generate)
            generate.assert_not_called()
            response = json.loads((root / "weapon_response.json").read_text())
            self.assertEqual(response["status"], "weapon_blocked")

    def test_validates_output_rejects_character_and_resumes_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._approve_character(root)
            request = self._request(root)
            provider_output = root / "provider.png"
            generate = mock.Mock()

            def succeed(current):
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(current.output_path)
                return GenerationResult("ok", "fake", current.model, current.output_path, {"id": "r1"})

            generate.side_effect = succeed
            first = weapon_layer_persistence.run_weapon_stage(root, request, generate)
            second = weapon_layer_persistence.run_weapon_stage(root, request, generate)
            self.assertEqual(generate.call_count, 1)
            self.assertFalse(first["resumed"])
            self.assertTrue(second["resumed"])
            self.assertEqual(second["status"], "weapon_complete")
            for name in ("weapon_full.png", "weapon_validation.png", "weapon.request.json", "weapon_response.json"):
                self.assertTrue((root / name).is_file(), name)

            # A response that reproduces the approved character is not an isolated weapon.
            character = root / "character_full.png"
            def leak(current):
                Image.open(character).save(current.output_path)
                return GenerationResult("ok", "fake", current.model, current.output_path, {})

            generate.side_effect = leak
            with self.assertRaisesRegex(ValueError, "personagem"):
                weapon_layer_persistence.run_weapon_stage(root, self._request(root, prompt="retry"), generate)
            response = json.loads((root / "weapon_response.json").read_text())
            self.assertEqual(response["status"], "weapon_failed")
            self.assertTrue((root / "weapon_full.png").is_file())

    def test_rejects_wrong_dimensions_and_retry_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._approve_character(root)
            request = self._request(root)
            calls = 0

            def flaky(current):
                nonlocal calls
                calls += 1
                size = (512, 512) if calls == 1 else (1024, 1024)
                Image.new("RGBA", size, (0, 0, 0, 0)).save(current.output_path)
                return GenerationResult("ok", "fake", current.model, current.output_path, {})

            with self.assertRaisesRegex(ValueError, "dimens"):
                weapon_layer_persistence.run_weapon_stage(root, request, flaky)
            result = weapon_layer_persistence.run_weapon_stage(root, request, flaky)
            self.assertEqual(result["status"], "weapon_complete")
            self.assertEqual(calls, 2)

    def test_rejects_rgba_output_without_any_transparent_background(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._approve_character(root)
            request = self._request(root)

            def opaque(current):
                Image.new("RGBA", (1024, 1024), (40, 80, 120, 255)).save(current.output_path)
                return GenerationResult("ok", "fake", current.model, current.output_path, {})

            with self.assertRaisesRegex(ValueError, "transpar"):
                weapon_layer_persistence.run_weapon_stage(root, request, opaque)
            self.assertEqual(
                json.loads((root / "weapon_response.json").read_text())["status"],
                "weapon_failed",
            )


if __name__ == "__main__":
    unittest.main()
