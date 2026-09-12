import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))
sys.path.insert(0, str(SPRITE_LAB / "tests" / "fixtures"))

import image_generation_provider  # noqa: E402
import layered_bundle  # noqa: E402
import layered_compositor  # noqa: E402
import server  # noqa: E402
from st08_contract_fixture import create_st08_fixture  # noqa: E402


class St08ContractTests(unittest.TestCase):
    def _compose(self, fixture: dict, destination: Path) -> dict:
        return layered_compositor.compose_layered_spritesheets(
            fixture["cells"], destination, grid=(8, 8)
        )

    def test_fixture_is_complete_8x8_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = create_st08_fixture(root / "first")
            second = create_st08_fixture(root / "second")
            self.assertEqual(first["grid"], [8, 8])
            self.assertEqual(first["cell_size"], [8, 8])
            self.assertEqual(len(first["cells"]), 64)
            self.assertEqual(len(first["front_cells"]), 31)
            self.assertEqual(len(first["rear_cells"]), 32)
            self.assertEqual(len(list((root / "first" / "cells").glob("*.png"))), 192)
            self.assertEqual(first["front_cells"], second["front_cells"])
            self.assertEqual(first["rear_cells"], second["rear_cells"])
            self.assertEqual(first["empty_cell"], {"row": 7, "column": 7})
            self.assertEqual(
                {(cell["row"], cell["column"]) for cell in first["cells"]},
                {(row, column) for row in range(8) for column in range(8)},
            )

            first_outputs = self._compose(first, root / "first-composed")
            second_outputs = self._compose(second, root / "second-composed")
            for name in first_outputs["outputs"]:
                self.assertEqual(
                    Path(first_outputs["outputs"][name]).read_bytes(),
                    Path(second_outputs["outputs"][name]).read_bytes(),
                )

    def test_dry_run_compositor_and_bundle_contract_cover_depth_alpha_and_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = create_st08_fixture(root / "fixture")
            first_cell = fixture["cells"][0]
            provider = image_generation_provider.DryRunProvider()
            for role, field in (("character", "character_path"), ("weapon", "weapon_path")):
                output = root / "dry-run" / f"{role}.png"
                request = image_generation_provider.GenerationRequest(
                    job_id=f"st08-{role}",
                    prompt=f"local {role} contract fixture",
                    input_images=(Path(first_cell[field]),),
                    output_path=output,
                    model="fixture-model",
                    generation_role=role,
                    metadata={
                        "fixture": "st08-8x8",
                        "output_size": [1024, 1024],
                        "materialize_output": False,
                    },
                )
                result = provider.generate(request)
                self.assertEqual(result.status, "dry-run")
                self.assertFalse(output.exists())
                request_payload = json.loads(
                    Path(result.response_metadata["request"]).read_text(encoding="utf-8")
                )
                self.assertEqual(request_payload["generation_role"], role)
                self.assertEqual(request_payload["metadata"]["fixture"], "st08-8x8")

            composed = self._compose(fixture, root / "composed")
            self.assertEqual(composed["cell_count"], 64)
            self.assertEqual(composed["grid"], [8, 8])
            self.assertEqual(composed["cell_size"], [8, 8])
            for path in composed["outputs"].values():
                with Image.open(path) as image:
                    self.assertEqual(image.mode, "RGBA")
                    self.assertEqual(image.size, (64, 64))

            with Image.open(composed["character_holdout_spritesheet"]) as character:
                # (0, 0) is front: the weapon cuts the character out.
                self.assertEqual(character.getpixel((3, 3))[3], 0)
                # (0, 1) is rear: the character remains over the weapon.
                self.assertEqual(character.getpixel((11, 3))[3], 255)
                # The fixture deliberately carries a partial-alpha edge.
                self.assertGreater(character.getpixel((1, 2))[3], 0)
                self.assertLess(character.getpixel((1, 2))[3], 255)
                # (7, 7) is intentionally empty.
                self.assertEqual(character.getpixel((56, 56)), (0, 0, 0, 0))
            with Image.open(composed["weapon_spritesheet"]) as weapon:
                self.assertEqual(weapon.getpixel((3, 3))[:3], (220, 160, 35))
                self.assertEqual(weapon.getpixel((11, 3))[:3], (220, 160, 35))
                self.assertEqual(weapon.getpixel((56, 56)), (0, 0, 0, 0))
            with Image.open(composed["holdout_cut_mask"]) as mask:
                self.assertEqual(mask.getpixel((3, 3))[3], 255)
                self.assertEqual(mask.getpixel((11, 3))[3], 0)
                self.assertGreater(mask.getpixel((1, 2))[3], 0)
                self.assertLess(mask.getpixel((1, 2))[3], 255)
            with Image.open(composed["composite_preview"]) as preview:
                self.assertEqual(preview.getpixel((3, 3))[:3], (220, 160, 35))
                self.assertEqual(preview.getpixel((11, 3))[:3], (35, 120, 210))
                self.assertEqual(preview.getpixel((56, 56)), (0, 0, 0, 0))

            manifest = layered_bundle.build_layered_bundle(
                root / "composed",
                character_holdout=composed["character_holdout_spritesheet"],
                weapon=composed["weapon_spritesheet"],
                holdout_source=composed["holdout_cut_mask"],
                preview=composed["composite_preview"],
                source={
                    "job_id": "st08-fixture",
                    "render_id": "st08-local",
                    "provider": "dry-run",
                    "model": "fixture-model",
                },
            )
            self.assertEqual(manifest["layout"], {"rows": 8, "columns": 8, "cell_size": [8, 8]})
            self.assertEqual(
                [(layer["id"], layer["z"]) for layer in manifest["layers"]],
                [("weapon", 0), ("character_holdout", 1)],
            )
            self.assertEqual(set(manifest["hashes"]), {
                "character_holdout_spritesheet.png",
                "weapon_spritesheet.png",
                "holdout_cut_mask.png",
                "composite_preview.png",
            })
            layered_bundle.validate_layered_bundle(manifest, root / "composed")

            with mock.patch.object(server, "LAYERED_PUBLISHED_ROOT", root / "published"):
                publication = server.publish_layered_job(
                    "st08-fixture",
                    root / "composed",
                    source=manifest["source"],
                )
                listed = server.list_published_layered_bundles()

            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["job_id"], "st08-fixture")
            self.assertEqual(publication["manifest"], listed[0]["manifest"])
            self.assertEqual(
                set(publication["outputs"]),
                {"weapon", "character_holdout", "holdout_source", "preview"},
            )
            self.assertEqual(
                publication["outputs"]["weapon"],
                "/layered-outputs/st08-fixture/weapon_spritesheet.png",
            )
            self.assertEqual(
                publication["manifest_url"],
                "/api/layered-bundles/st08-fixture",
            )
            for value in publication["outputs"].values():
                self.assertTrue(value.startswith("/layered-outputs/st08-fixture/"))
                self.assertNotIn("..", value)
                self.assertNotIn("\\", value)
            registry_path = root / "published" / "st08-fixture" / "layered_artifact_hashes.json"
            self.assertEqual(
                publication["artifact_hashes"]["artifacts"]["weapon_output"]["path"],
                "weapon_spritesheet.png",
            )
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(registry["fingerprint"], publication["artifact_hashes"]["fingerprint"])


if __name__ == "__main__":
    unittest.main()
