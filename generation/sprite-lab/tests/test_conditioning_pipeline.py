import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import conditioning_metrics  # noqa: E402
import conditioning_pack  # noqa: E402
import conditioning_runner  # noqa: E402
import postprocess_conditioning  # noqa: E402


def write_transparent_subject(path: Path, color: tuple[int, int, int, int], offset: int = 0) -> None:
    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((9 + offset, 4, 22 + offset, 27), fill=color)
    image.save(path, format="PNG")


def write_generated_frame(path: Path, offset: int = 0) -> None:
    image = Image.new("RGBA", (32, 32), (255, 255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8 + offset, 5, 23 + offset, 26), fill=(55, 110, 210, 255))
    image.save(path, format="PNG")


class ConditioningPipelineTests(unittest.TestCase):
    def _create_pack(self, root: Path) -> Path:
        source = root / "source"
        for channel, color in {
            "beauty": (180, 80, 50, 255),
            "silhouette": (255, 255, 255, 255),
            "segmentation": (231, 76, 60, 255),
        }.items():
            (source / channel).mkdir(parents=True)
            for index in range(3):
                write_transparent_subject(
                    source / channel / f"f{index:02d}.png",
                    color,
                    offset=index,
                )
        target = root / "target.png"
        write_transparent_subject(target, (40, 160, 220, 255))
        pack = root / "pack"
        conditioning_pack.build_pack(
            source,
            pack,
            target,
            action="run",
            direction="r1",
            fps=12,
            foot_anchor=(16, 28),
        )
        return pack

    def test_dry_run_creates_provider_requests_for_all_conditioning_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self._create_pack(root)
            output = root / "requests"
            report = conditioning_runner.run(
                pack / "manifest.json",
                provider_name="dry-run",
                model="gpt-image-2",
                condition="segmentation",
                output_dir=output,
            )

            self.assertEqual(len(report["results"]), 3)
            self.assertTrue(all(item["status"] == "dry-run" for item in report["results"]))
            request = json.loads((output / "f00.request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["metadata"]["channels"], ["beauty", "silhouette", "segmentation"])
            self.assertEqual(len(request["input_images"]), 4)
            self.assertFalse((output / "f00.png").exists())

    def test_postprocess_metrics_and_sprite_artifacts_form_a_valid_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = self._create_pack(root)
            generated = root / "generated"
            generated.mkdir()
            for index in range(3):
                write_generated_frame(generated / f"f{index:02d}.png", offset=index)

            postprocessed = root / "postprocessed"
            metadata = postprocess_conditioning.process_run(
                pack / "manifest.json",
                generated,
                postprocessed,
            )
            self.assertTrue(Path(metadata["spritesheet"]).is_file())
            self.assertTrue(Path(metadata["gifs"]["r1"]).is_file())
            self.assertEqual(len(list((postprocessed / "normalized").glob("*.png"))), 3)

            report = conditioning_metrics.evaluate_generated(
                pack / "manifest.json",
                postprocessed / "normalized",
                output_path=root / "metrics.json",
            )
            self.assertTrue(report["gate"]["all_frames_present"])
            self.assertTrue(report["gate"]["foot_anchor_pass"])
            self.assertEqual(report["valid_frame_count"], 3)
            self.assertTrue((root / "metrics.json").is_file())


if __name__ == "__main__":
    unittest.main()
