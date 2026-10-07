import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

try:
    import pregan_realesrgan_reuse_mask_pipeline as subject  # noqa: E402
    HAS_SCIPY = True
except ImportError:
    subject = None  # type: ignore[assignment]
    HAS_SCIPY = False


def _lineart_module():
    if subject is None:
        raise unittest.SkipTest("requires scipy")
    return subject


@unittest.skipUnless(HAS_SCIPY, "requires scipy")
class PreganPrecleanTests(unittest.TestCase):
    def test_lineart_is_white_and_separate_from_beauty(self) -> None:
        module = _lineart_module()
        image = Image.new("RGBA", (8, 8), (180, 120, 60, 0))
        image.putalpha(Image.new("L", (8, 8), 255))
        lineart = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
        original = np.asarray(image).copy()
        result = module.build_lineart_layer(image, lineart, 1.0)
        output = np.asarray(result)
        self.assertEqual(tuple(output[4, 4]), (255, 255, 255, 255))
        self.assertEqual(tuple(output[0, 0]), (255, 255, 255, 255))
        np.testing.assert_array_equal(np.asarray(image), original)
        result.close()
        image.close()
        lineart.close()

    def test_lineart_alpha_is_intersected_with_approved_alpha(self) -> None:
        module = _lineart_module()
        alpha = np.zeros((8, 8), dtype=np.uint8)
        alpha[2:6, 2:6] = 255
        image = Image.fromarray(np.dstack([np.full((8, 8, 3), 100, dtype=np.uint8), alpha]), "RGBA")
        lineart = Image.new("RGBA", (8, 8), (255, 255, 255, 200))
        result = module.build_lineart_layer(image, lineart, 0.85)
        expected = np.minimum(alpha, round(200 * 0.85)).astype(np.uint8)
        np.testing.assert_array_equal(np.asarray(result.getchannel("A")), expected)
        self.assertTrue(np.all(np.asarray(result)[expected > 0, :3] == 255))
        result.close()
        image.close()
        lineart.close()
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
