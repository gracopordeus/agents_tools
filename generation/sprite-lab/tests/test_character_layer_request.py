import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import character_layer_request  # noqa: E402
from image_generation_provider import DryRunProvider  # noqa: E402


class CharacterLayerRequestTests(unittest.TestCase):
    def test_builds_auditable_ordered_request_and_dry_run_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            roles = ["identity", "beauty", "lineart", "bones", "frame_control"]
            paths = []
            for index, role in enumerate(roles):
                path = root / f"{index}-{role}.png"
                Image.new("RGBA", (1024, 1024), (index, 0, 0, 255)).save(path)
                paths.append(path)
            manifest = [{"index": index + 1, "type": role} for index, role in enumerate(roles)]

            request = character_layer_request.build_character_layer_request(
                job_id="job-7",
                prompt="character layer prompt",
                reference_manifest=manifest,
                input_images=paths,
                output_dir=root,
                model="test-model",
                output_size=1024,
                source_contract={"action": {"clip_name": "attack"}},
                supports_editing=True,
            )

            self.assertEqual(request.generation_role, "character")
            self.assertEqual(request.input_images, tuple(paths))
            self.assertEqual(request.base_image, paths[1])
            self.assertEqual(request.reference_images, (paths[0], *paths[2:]))
            self.assertEqual(request.output_path.name, "character_full.png")
            self.assertEqual(request.metadata["output_size"], [1024, 1024])
            self.assertEqual(request.metadata["source_contract"]["action"]["clip_name"], "attack")
            self.assertEqual(
                request.metadata["input_hashes"][0]["sha256"],
                hashlib.sha256(paths[0].read_bytes()).hexdigest(),
            )
            self.assertIsNotNone(request.mask_path)
            with Image.open(request.mask_path) as mask:
                self.assertEqual(mask.size, (1024, 1024))

            result = DryRunProvider().generate(request)
            payload = json.loads(Path(result.response_metadata["request"]).read_text())
            self.assertEqual(payload["generation_role"], "character")
            self.assertEqual(payload["input_images"], [str(path) for path in paths])
            self.assertEqual(payload["base_image"], str(paths[1]))
            self.assertEqual(payload["references"], [str(paths[0]), *map(str, paths[2:])])
            self.assertEqual(payload["mask"]["path"], str(request.mask_path))

    def test_rejects_manifest_order_mismatch_and_unsupported_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "identity.png"
            Image.new("RGBA", (8, 8)).save(path)
            for manifest, size, message in (
                ([{"index": 2, "type": "identity"}], 1024, "ordem"),
                ([{"index": 1, "type": "identity"}], 512, "1024.*2048"),
            ):
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    character_layer_request.build_character_layer_request(
                        job_id="job", prompt="p", reference_manifest=manifest,
                        input_images=[path], output_dir=root, model="m",
                        output_size=size, source_contract={}, supports_editing=False,
                    )


if __name__ == "__main__":
    unittest.main()
