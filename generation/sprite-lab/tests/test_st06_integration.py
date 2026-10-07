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


class St06IntegrationTests(unittest.TestCase):
    def _layers(self, root: Path) -> dict[str, dict[str, Path]]:
        generated = {}
        for layer_id, color in (
            ("character", (200, 20, 20, 255)),
            ("weapon", (20, 200, 20, 255)),
        ):
            path = root / f"{layer_id}_generated.png"
            Image.new("RGBA", (16, 8), color).save(path, format="PNG")
            structural = root / f"{layer_id}-structural"
            structural.mkdir()
            generated[layer_id] = {
                "generated_sheet": path,
                "structural_dir": structural,
            }
        masks = root / "weapon-front-masks"
        masks.mkdir()
        # Structural masks may be authored at a different resolution than
        # the final postprocessed cell; the orchestrator scales each mask
        # independently before invoking the compositor.
        Image.new("L", (4, 4), 255).save(masks / "row0_col0.png", format="PNG")
        Image.new("L", (4, 4), 0).save(masks / "row0_col1.png", format="PNG")
        generated["weapon"]["front_mask_dir"] = masks
        return generated

    def _process_fixture(self, generated_sheet, structural_dir, layer_output, **kwargs):
        del structural_dir
        with Image.open(generated_sheet) as normalized:
            self.assertEqual(
                normalized.size,
                (
                    kwargs["source_cell"] * kwargs["phases"],
                    kwargs["source_cell"] * kwargs["rows"],
                ),
            )
        layer_output = Path(layer_output)
        layer_id = kwargs["layer_id"]
        original = layer_output / "variants" / "original"
        original.mkdir(parents=True)
        color = (200, 20, 20, 255) if layer_id == "character" else (20, 200, 20, 255)
        Image.new("RGBA", (16, 8), color).save(
            original / "spritesheet.png", format="PNG"
        )
        return {
            "rows": 1,
            "phases": 2,
            "output_cell": 8,
            "foot_anchor": [4, 6],
        }

    def _source(self) -> dict[str, str]:
        return {
            "job_id": "job-7",
            "render_id": "render-7",
            "provider": "dry-run",
            "model": "fixture-model",
        }

    def test_integrates_postprocess_compositor_and_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layers = self._layers(root)
            with mock.patch.object(server, "LAYERED_PUBLISHED_ROOT", root / "published"):
                result = server.run_layered_postprocess_compose_publish(
                    "job-7",
                    layers,
                    source=self._source(),
                    profile_id="fixture-bicubic",
                    rows=1,
                    phases=2,
                    source_cell=4,
                    postprocess_output=root / "postprocess",
                    process_fn=self._process_fixture,
                )

            publication = result["publication"]
            destination = root / "published" / "job-7"
            self.assertEqual(result["holdout_stage"], "after_final_resolution")
            self.assertEqual(publication["manifest"]["source"], self._source())
            self.assertEqual(
                set(publication["manifest"]["hashes"]),
                {
                    "character_full_spritesheet.png",
                    "component_visible_spritesheet.png",
                    "component_visibility_mask.png",
                    "composite_preview.png",
                },
            )
            self.assertEqual(
                publication["manifest"]["schema"],
                "sprite_lab.layered_sprite_bundle/v2",
            )
            self.assertEqual(
                set(publication["outputs"]),
                {
                    "character_full",
                    "component_visible",
                    "component_visibility_mask",
                    "preview",
                },
            )
            self.assertEqual(
                {item["key"] for item in server._published_layered_artifacts(
                    "job-7", publication["manifest"]
                )},
                {
                    "character_full",
                    "component_visible",
                    "component_visibility_mask",
                    "preview",
                    "manifest",
                    "hashes",
                },
            )
            self.assertTrue(result["composition"]["validation"]["validated"])
            with Image.open(destination / "character_full_spritesheet.png") as character:
                self.assertEqual(character.getpixel((0, 0))[3], 255)
                self.assertEqual(character.getpixel((8, 0))[3], 255)
            with Image.open(destination / "component_visible_spritesheet.png") as component:
                self.assertEqual(component.getpixel((0, 0))[3], 255)
                self.assertEqual(component.getpixel((8, 0))[3], 0)
            with Image.open(destination / "composite_preview.png") as preview:
                self.assertEqual(preview.getpixel((0, 0))[:3], (20, 200, 20))
                self.assertEqual(preview.getpixel((8, 0))[:3], (200, 20, 20))
            persisted = result["postprocess"]["manifest"]
            self.assertEqual(
                json.loads(Path(persisted).read_text())["holdout_stage"],
                "after_final_resolution",
            )

    def test_requires_auditable_source_before_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layers = self._layers(root)
            calls = []

            def process(*args, **kwargs):
                calls.append((args, kwargs))
                return self._process_fixture(*args, **kwargs)

            with self.assertRaisesRegex(ValueError, "source.*audit|origem.*audit"):
                server.run_layered_postprocess_compose_publish(
                    "job-7",
                    layers,
                    source={},
                    postprocess_output=root / "postprocess",
                    process_fn=process,
                )
            self.assertEqual(calls, [])
            self.assertFalse((root / "postprocess").exists())

    def test_compositor_failure_never_calls_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layers = self._layers(root)
            published = []

            def fail_compose(*args, **kwargs):
                raise RuntimeError("final compositor failed")

            def publish(*args, **kwargs):
                published.append((args, kwargs))
                raise AssertionError("publication must not run")

            with self.assertRaisesRegex(RuntimeError, "final compositor failed"):
                server.run_layered_postprocess_compose_publish(
                    "job-7",
                    layers,
                    source=self._source(),
                    rows=1,
                    phases=2,
                    source_cell=4,
                    postprocess_output=root / "postprocess",
                    process_fn=self._process_fixture,
                    composer_fn=fail_compose,
                    publish_fn=publish,
                )
            self.assertEqual(published, [])


if __name__ == "__main__":
    unittest.main()
