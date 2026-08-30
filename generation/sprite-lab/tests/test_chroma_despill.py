import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import chroma_despill as subject  # noqa: E402


class ChromaDespillTests(unittest.TestCase):
    def test_despill_changes_only_green_edge_and_preserves_alpha(self) -> None:
        rgba = np.zeros((16, 16, 4), dtype=np.uint8)
        rgba[..., :3] = (20, 240, 10)
        rgba[3:13, 3:13, 3] = 255
        rgba[3:13, 3:13, :3] = (100, 70, 40)
        rgba[3, 3:13, :3] = (5, 120, 4)
        rgba[8, 8, :3] = (10, 160, 10)
        image = Image.fromarray(rgba, mode="RGBA")

        result, report = subject.despill_rgba(
            image,
            edge_radius=2,
            tolerance=4,
        )
        output = np.asarray(result)

        self.assertTrue(np.array_equal(output[..., 3], rgba[..., 3]))
        self.assertLess(int(output[3, 8, 1]), int(rgba[3, 8, 1]))
        self.assertLessEqual(
            int(output[3, 8, 1]),
            max(int(output[3, 8, 0]), int(output[3, 8, 2])) + 4,
        )
        self.assertEqual(tuple(output[8, 8, :3]), tuple(rgba[8, 8, :3]))
        self.assertEqual(report["dominant_channel"], "green")
        self.assertGreater(report["changed_pixels"], 0)

    def test_foreground_cleanup_removes_a_small_green_island(self) -> None:
        rgba = np.zeros((16, 16, 4), dtype=np.uint8)
        rgba[..., :3] = (20, 240, 10)
        rgba[3:13, 3:13, 3] = 255
        rgba[3:13, 3:13, :3] = (100, 70, 40)
        rgba[7:9, 7:9, :3] = (56, 244, 34)
        image = Image.fromarray(rgba, mode="RGBA")

        result, report = subject.process_frame(
            image,
            edge_radius=6,
            tolerance=2,
            strength=1.0,
            bleed_radius=2,
            key_color=None,
            scope="foreground",
            remove_islands=True,
            key_distance=96,
            max_island_size=64,
        )
        output = np.asarray(result)

        self.assertEqual(int(output[7, 7, 3]), 0)
        self.assertGreater(report["removed_key_island_pixels"], 0)

    def test_process_frame_bleeds_corrected_rgb_without_changing_alpha(self) -> None:
        rgba = np.zeros((16, 16, 4), dtype=np.uint8)
        rgba[..., :3] = (20, 240, 10)
        rgba[5:11, 5:11, 3] = 255
        rgba[5:11, 5:11, :3] = (4, 100, 3)
        image = Image.fromarray(rgba, mode="RGBA")

        result, _ = subject.process_frame(
            image,
            edge_radius=2,
            tolerance=4,
            strength=1.0,
            bleed_radius=2,
            key_color=None,
        )
        output = np.asarray(result)

        self.assertEqual(int(output[4, 7, 3]), 0)
        self.assertLess(int(output[4, 7, 1]), 100)
        self.assertTrue(np.array_equal(output[..., 3], rgba[..., 3]))


if __name__ == "__main__":
    unittest.main()
