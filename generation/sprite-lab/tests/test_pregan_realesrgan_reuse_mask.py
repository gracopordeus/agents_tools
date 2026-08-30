import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import pregan_realesrgan_reuse_mask_pipeline as subject  # noqa: E402


class PreganPrecleanTests(unittest.TestCase):
    def test_preclean_skips_despill_for_neutral_black_background(self) -> None:
        image = Image.new("RGB", (16, 16), (0, 0, 0))
        image.paste((120, 80, 40), (4, 4, 12, 12))
        mask = Image.new("L", (16, 16), 0)
        mask.paste(255, (4, 4, 12, 12))

        result, report = subject.preclean_cell(image, mask)

        self.assertEqual(report["despill"], "skipped_neutral_background")
        self.assertTrue(np.array_equal(np.asarray(result.getchannel("A")), np.asarray(mask)))

    def test_preclean_removes_green_and_fills_transparent_rgb(self) -> None:
        rgb = np.full((16, 16, 3), (20, 240, 10), dtype=np.uint8)
        rgb[4:12, 4:12] = (110, 70, 40)
        rgb[4, 4:12] = (8, 120, 6)
        mask = np.zeros((16, 16), dtype=np.uint8)
        mask[4:12, 4:12] = 255

        result, report = subject.preclean_cell(
            Image.fromarray(rgb, mode="RGB"),
            Image.fromarray(mask, mode="L"),
        )
        output = np.asarray(result)

        self.assertTrue(np.array_equal(output[..., 3], mask))
        self.assertLessEqual(
            int(output[4, 8, 1]),
            max(int(output[4, 8, 0]), int(output[4, 8, 2])) + 2,
        )
        self.assertNotEqual(tuple(output[0, 0, :3]), (20, 240, 10))
        self.assertEqual(
            report["transparent_rgb_fill"],
            "nearest_foreground_full_canvas",
        )

    def test_explicit_key_cleans_green_after_transparent_rgb_was_lost(self) -> None:
        rgb = np.zeros((16, 16, 3), dtype=np.uint8)
        rgb[3:13, 3:13] = (10, 245, 8)
        rgb[5:11, 5:11] = (120, 75, 35)
        mask = np.zeros((16, 16), dtype=np.uint8)
        mask[3:13, 3:13] = 255

        result, report = subject.preclean_cell(
            Image.fromarray(rgb, mode="RGB"),
            Image.fromarray(mask, mode="L"),
            key_color=(0, 255, 0),
        )
        output = np.asarray(result)

        self.assertGreater(report["changed_pixels"], 0)
        self.assertLessEqual(
            int(output[3, 8, 1]),
            max(int(output[3, 8, 0]), int(output[3, 8, 2])) + 2,
        )


if __name__ == "__main__":
    unittest.main()
