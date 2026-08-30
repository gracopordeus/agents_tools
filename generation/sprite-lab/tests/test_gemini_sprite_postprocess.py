import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import gemini_sprite_postprocess as subject  # noqa: E402


class GeminiSpritePostprocessTests(unittest.TestCase):
    def test_process_uses_birefnet_mask_pass_before_quality_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / "generated.png"
            generated.touch()
            structural = root / "structural"
            structural.mkdir()
            output = root / "output"
            commands: list[list[str]] = []

            def fake_run(command: list[str], label: str) -> dict:
                commands.append(command)
                if "realesrgan_birefnet_pipeline.py" in command[1]:
                    masks = output / "mask_pass_realesrgan_birefnet" / "foreground_cleanup_masks"
                    masks.mkdir(parents=True)
                    for row in range(1):
                        for column in range(2):
                            (masks / f"row{row}_col{column}.png").touch()
                elif "pregan_realesrgan_reuse_mask_pipeline.py" in command[1]:
                    official = Path(command[4])
                    official.mkdir(parents=True)
                    for row in range(1):
                        for column in range(2):
                            Image.new("RGBA", (8, 8), (100, 80, 20, 255)).save(
                                official / f"row{row}_col{column}.png"
                            )
                elif "temporal_palette_refine.py" in command[1]:
                    variant = Path(command[3])
                    variant.mkdir(parents=True)
                    (variant / "animation_all_directions_1-2-5-4-3-8-7-6.gif").touch()
                return {"label": label}

            with patch.object(subject, "_run", side_effect=fake_run), patch.object(
                subject, "MASK_CACHE_ROOT", root / "mask-cache"
            ):
                report = subject.process(
                    generated,
                    structural,
                    output,
                    rows=1,
                    phases=2,
                    source_cell=256,
                    realesrgan_repo=root / "Real-ESRGAN",
                    python_executable="python",
                )

            self.assertIn("realesrgan_birefnet_pipeline.py", commands[0][1])
            self.assertIn("--birefnet-threshold", commands[0])
            self.assertEqual(
                commands[0][commands[0].index("--model-profile") + 1],
                "anime_x4plus_6b",
            )
            self.assertIn("pregan_realesrgan_reuse_mask_pipeline.py", commands[1][1])
            self.assertEqual(
                commands[1][commands[1].index("--model-profile") + 1],
                "anime_x4plus_6b",
            )
            self.assertEqual(
                Path(commands[1][3]),
                output / "mask_pass_realesrgan_birefnet" / "foreground_cleanup_masks",
            )
            self.assertIn("approved_birefnet_mask_512", report["pipeline"])
            self.assertNotIn("structural_alpha_and_alignment", report["pipeline"])

    def test_builds_ordered_gif_for_original_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for row in range(8):
                for column in range(2):
                    Image.new("RGBA", (8, 8), (row * 20, column * 40, 0, 255)).save(
                        output / f"row{row}_col{column}.png"
                    )

            result = subject._build_ordered_gif(output, 8, 2, 10)

            self.assertTrue(result.is_file())


if __name__ == "__main__":
    unittest.main()
