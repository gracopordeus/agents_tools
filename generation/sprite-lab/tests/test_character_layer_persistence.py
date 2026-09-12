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
import character_layer_request  # noqa: E402
from image_generation_provider import GenerationRequest, GenerationResult  # noqa: E402


class CharacterLayerPersistenceTests(unittest.TestCase):
    def _request(self, root: Path) -> GenerationRequest:
        return GenerationRequest(
            "job", "prompt", (), root / "provider.png", "model",
            {"output_size": [1024, 1024]}, generation_role="character",
        )

    def test_persists_valid_character_and_resumes_without_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider_output = root / "provider.png"
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(provider_output)
            generate = mock.Mock(return_value=GenerationResult(
                "ok", "fake", "model", provider_output, {"request_id": "r1"}
            ))

            first = character_layer_persistence.run_character_stage(
                root, self._request(root), generate
            )
            second = character_layer_persistence.run_character_stage(
                root, self._request(root), generate
            )

            self.assertEqual(generate.call_count, 1)
            self.assertFalse(first["resumed"])
            self.assertTrue(second["resumed"])
            for name in ("character_full.png", "character_validation.png",
                         "character.request.json", "character_response.json"):
                self.assertTrue((root / name).is_file(), name)
            response = json.loads((root / "character_response.json").read_text())
            self.assertEqual(response["status"], "character_complete")
            self.assertEqual(response["canonical_input"], "character_full.png")
            self.assertEqual(response["validation_overlay"]["presentation_only"], True)

    def test_rejects_invalid_outputs_without_completing_character(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalids = []
            wrong_size = root / "wrong.png"
            Image.new("RGBA", (512, 512)).save(wrong_size)
            invalids.append((wrong_size, "dimens"))
            rgb = root / "rgb.png"
            Image.new("RGB", (1024, 1024)).save(rgb)
            invalids.append((rgb, "alpha"))
            jpeg = root / "image.jpg"
            Image.new("RGB", (1024, 1024)).save(jpeg)
            invalids.append((jpeg, "PNG"))
            bleed = root / "bleed.png"
            image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
            image.putpixel((128, 0), (255, 0, 0, 255))
            image.save(bleed)
            invalids.append((bleed, "bleed"))
            invalids.append((root / "missing.png", "arquivo"))

            for index, (path, message) in enumerate(invalids):
                case = root / str(index)
                case.mkdir()
                result = GenerationResult("ok", "fake", "model", path, {})
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    character_layer_persistence.run_character_stage(
                        case, self._request(case), lambda _request, value=result: value
                    )
                response = json.loads((case / "character_response.json").read_text())
                self.assertEqual(response["status"], "character_failed")
                self.assertFalse((case / "character_full.png").exists())

    def test_accepts_both_supported_canvas_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for size in (1024, 2048):
                source = root / f"character-{size}.png"
                overlay = root / f"validation-{size}.png"
                Image.new("RGBA", (size, size), (0, 0, 0, 0)).save(source)

                report = character_layer_persistence.validate_character_output(
                    source, overlay, (size, size)
                )

                self.assertEqual(report["size"], [size, size])
                self.assertTrue(overlay.is_file())

    def _built_request(self, root: Path, *, prompt: str = "prompt") -> GenerationRequest:
        identity = root / "identity.png"
        beauty = root / "beauty.png"
        Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(identity)
        Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(beauty)
        return character_layer_request.build_character_layer_request(
            job_id="job", prompt=prompt,
            reference_manifest=[
                {"index": 1, "type": "identity"},
                {"index": 2, "type": "beauty"},
            ],
            input_images=[identity, beauty], output_dir=root, model="model",
            output_size=1024, source_contract={"action": "attack"},
            supports_editing=False,
        )

    def test_real_builder_output_is_promoted_without_same_file_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = self._built_request(root)

            def generate(current: GenerationRequest) -> GenerationResult:
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(current.output_path)
                return GenerationResult("ok", "fake", current.model, current.output_path, {})

            response = character_layer_persistence.run_character_stage(root, request, generate)

            self.assertEqual(response["status"], "character_complete")
            self.assertTrue((root / "character_full.png").is_file())

    def test_changed_real_request_invalidates_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generate = mock.Mock()

            def produce(current: GenerationRequest) -> GenerationResult:
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(current.output_path)
                return GenerationResult("ok", "fake", current.model, current.output_path, {})

            generate.side_effect = produce
            first = character_layer_persistence.run_character_stage(
                root, self._built_request(root, prompt="prompt-a"), generate
            )
            second = character_layer_persistence.run_character_stage(
                root, self._built_request(root, prompt="prompt-b"), generate
            )

            self.assertEqual(generate.call_count, 2)
            self.assertNotEqual(first["request_fingerprint"], second["request_fingerprint"])
            self.assertFalse(second["resumed"])


if __name__ == "__main__":
    unittest.main()
