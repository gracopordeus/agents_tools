import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import weapon_layer_request  # noqa: E402
from image_generation_provider import DryRunProvider  # noqa: E402


class WeaponLayerRequestTests(unittest.TestCase):
    def _inputs(self, root: Path):
        weapon = root / "weapon.png"
        character = root / "character_full.png"
        guide = root / "weapon_guide.png"
        for path in (weapon, character, guide):
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(path)
        return weapon, character, guide

    def test_builds_ordered_weapon_request_with_character_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            weapon, character, guide = self._inputs(root)
            manifest = [
                {"index": 1, "type": "weapon_reference", "name": "sword"},
                {"index": 2, "type": "character_full", "name": "character_full.png"},
                {"index": 3, "type": "weapon_guide", "name": "guide"},
            ]
            request = weapon_layer_request.build_weapon_layer_request(
                job_id="job-weapon",
                prompt="weapon prompt",
                reference_manifest=manifest,
                input_images=[weapon, character, guide],
                output_dir=root,
                model="model",
                output_size=1024,
                source_contract={"action": {"clip_name": "attack"}},
                supports_editing=False,
            )

            self.assertEqual(request.generation_role, "weapon")
            self.assertEqual(request.output_path.name, "weapon_full.png")
            self.assertEqual(request.input_images, (weapon, character, guide))
            self.assertEqual(request.base_image, weapon)
            self.assertEqual(request.reference_images, (character, guide))
            self.assertEqual(
                request.metadata["character_dependency"]["sha256"],
                hashlib.sha256(character.read_bytes()).hexdigest(),
            )
            self.assertEqual(request.metadata["output_size"], [1024, 1024])

            result = DryRunProvider().generate(request)
            payload = json.loads(Path(result.response_metadata["request"]).read_text())
            self.assertEqual(payload["generation_role"], "weapon")
            self.assertEqual(payload["input_images"], [str(path) for path in (weapon, character, guide)])
            self.assertTrue(request.output_path.is_file())

    def test_requires_weapon_and_approved_character_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            weapon, character, guide = self._inputs(root)
            base = [
                {"index": 1, "type": "weapon_reference"},
                {"index": 2, "type": "weapon_guide"},
            ]
            with self.assertRaisesRegex(ValueError, "character_full"):
                weapon_layer_request.build_weapon_layer_request(
                    job_id="job", prompt="p", reference_manifest=base,
                    input_images=[weapon, guide], output_dir=root, model="m",
                    output_size=1024, source_contract={}, supports_editing=False,
                )

    def test_openai_adapts_small_visual_reference_to_supported_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            weapon = root / "weapon.jpg"
            character = root / "character_full.png"
            guide = root / "weapon_guide.png"
            Image.new("RGB", (12, 18), (240, 120, 40)).save(weapon, format="JPEG")
            Image.new("RGBA", (2048, 2048), (0, 0, 0, 0)).save(character)
            Image.new("RGBA", (2048, 2048), (0, 0, 0, 0)).save(guide)
            request = weapon_layer_request.build_weapon_layer_request(
                job_id="job-openai",
                prompt="weapon prompt",
                reference_manifest=[
                    {"index": 1, "type": "weapon_reference"},
                    {"index": 2, "type": "character_full"},
                    {"index": 3, "type": "weapon_guide"},
                ],
                input_images=[weapon, character, guide],
                output_dir=root,
                model="gpt-image-2",
                output_size=2048,
                source_contract={},
                supports_editing=True,
            )
            self.assertEqual(request.input_images[0], weapon)
            self.assertNotEqual(request.base_image, weapon)
            self.assertEqual(request.metadata["weapon_reference"]["original_size"], [12, 18])
            with Image.open(request.base_image) as normalized:
                self.assertEqual(normalized.size, (2048, 2048))
            with Image.open(request.mask_path) as mask:
                self.assertEqual(mask.size, (2048, 2048))


if __name__ == "__main__":
    unittest.main()
