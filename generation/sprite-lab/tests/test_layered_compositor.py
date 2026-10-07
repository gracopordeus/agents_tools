import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import layered_compositor  # noqa: E402


class LayeredCompositorTests(unittest.TestCase):
    def test_v2_masks_component_and_never_changes_character(self) -> None:
        character = np.array([[[10, 20, 30, 255], [40, 50, 60, 96]]], dtype=np.uint8)
        component = np.array([[[200, 10, 20, 255], [30, 210, 40, 128]]], dtype=np.uint8)
        visibility = np.array([[0, 255]], dtype=np.uint8)
        original_character = character.copy()

        visible, report = layered_compositor.apply_component_visibility(
            component,
            visibility,
            grid=(1, 1),
        )

        np.testing.assert_array_equal(character, original_character)
        self.assertEqual(visible.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(visible.getpixel((1, 0)), (30, 210, 40, 128))
        self.assertTrue(report["character_preserved"])
        self.assertTrue(report["component_masked"])

    def test_v2_alpha_128_over_opaque_character_stays_opaque(self) -> None:
        character = Image.new("RGBA", (1, 1), (0, 0, 255, 255))
        component = Image.new("RGBA", (1, 1), (255, 0, 0, 128))
        visibility = Image.new("L", (1, 1), 255)

        visible, _report = layered_compositor.apply_component_visibility(
            component,
            visibility,
            grid=(1, 1),
        )
        preview = layered_compositor.composite_component_over_character(
            character,
            visible,
        )

        self.assertEqual(visible.getpixel((0, 0)), (255, 0, 0, 128))
        self.assertEqual(preview.getpixel((0, 0)), (128, 0, 127, 255))

    def test_v2_visibility_dilation_never_crosses_cell_boundaries(self) -> None:
        component_alpha = np.full((4, 4), 255, dtype=np.uint8)
        visibility = np.zeros((4, 4), dtype=np.uint8)
        visibility[1, 1] = 255

        result = layered_compositor.calculate_component_visible_alpha(
            component_alpha,
            visibility,
            grid=(2, 2),
            dilation=1,
        )

        self.assertEqual(int(result[1, 1]), 255)
        self.assertEqual(int(result[0, 0]), 255)
        self.assertEqual(int(result[1, 2]), 0)
        self.assertEqual(int(result[2, 1]), 0)

    def test_front_weapon_removes_character_alpha_without_changing_weapon(self) -> None:
        character_alpha = np.full((2, 2), 255, dtype=np.uint8)
        weapon_alpha = np.array([[255, 0], [0, 0]], dtype=np.uint8)
        front_mask = np.array([[255, 0], [0, 0]], dtype=np.uint8)

        result = layered_compositor.calculate_holdout_alpha(
            character_alpha, weapon_alpha, front_mask, grid=(1, 1)
        )

        np.testing.assert_array_equal(result, [[0, 255], [255, 255]])
        np.testing.assert_array_equal(weapon_alpha, [[255, 0], [0, 0]])

    def test_weapon_behind_character_keeps_character_opaque(self) -> None:
        character_alpha = np.full((2, 2), 255, dtype=np.uint8)
        weapon_alpha = np.full((2, 2), 255, dtype=np.uint8)
        front_mask = np.zeros((2, 2), dtype=np.uint8)

        result = layered_compositor.calculate_holdout_alpha(
            character_alpha, weapon_alpha, front_mask, grid=(1, 1)
        )

        np.testing.assert_array_equal(result, character_alpha)

    def test_partially_transparent_weapon_and_mask_preserve_antialiasing(self) -> None:
        character_alpha = np.array([[255]], dtype=np.uint8)
        weapon_alpha = np.array([[128]], dtype=np.uint8)
        front_mask = np.array([[128]], dtype=np.uint8)

        result = layered_compositor.calculate_holdout_alpha(
            character_alpha, weapon_alpha, front_mask, grid=(1, 1)
        )

        expected = round(255 * (1 - (128 / 255) * (128 / 255)))
        self.assertEqual(int(result[0, 0]), expected)
        self.assertGreater(result[0, 0], 0)
        self.assertLess(result[0, 0], 255)

    def test_front_mask_is_restricted_to_its_current_cell(self) -> None:
        character_alpha = np.full((8, 8), 255, dtype=np.uint8)
        weapon_alpha = np.full((8, 8), 255, dtype=np.uint8)
        front_mask = np.zeros((8, 8), dtype=np.uint8)
        front_mask[3, 3] = 255
        front_mask[4, 4] = 255

        result = layered_compositor.calculate_holdout_alpha(
            character_alpha, weapon_alpha, front_mask, grid=(2, 2)
        )

        self.assertEqual(int(result[3, 3]), 0)
        self.assertEqual(int(result[4, 4]), 0)
        self.assertEqual(int(result[3, 4]), 255)
        self.assertEqual(int(result[4, 3]), 255)

    def test_dilation_does_not_cross_cell_boundaries_and_is_reported(self) -> None:
        character = Image.new("RGBA", (8, 8), (80, 100, 120, 255))
        weapon = Image.new("RGBA", (8, 8), (200, 180, 160, 255))
        front_mask = np.zeros((8, 8), dtype=np.uint8)
        front_mask[3, 3] = 255
        front_mask_image = Image.fromarray(front_mask, mode="L")

        holdout, report = layered_compositor.apply_character_holdout(
            character,
            weapon,
            front_mask_image,
            grid=(2, 2),
            dilation=1,
        )

        alpha = np.asarray(holdout)[..., 3]
        self.assertEqual(int(alpha[3, 3]), 0)
        self.assertEqual(int(alpha[2, 2]), 0)
        self.assertEqual(int(alpha[3, 4]), 255)
        self.assertEqual(report["dilation"], 1)
        self.assertTrue(report["dilation_applied"])
        self.assertEqual(report["grid"], [2, 2])

    def test_rgba_output_cleans_rgb_where_holdout_makes_alpha_zero(self) -> None:
        character = np.array([[[250, 20, 30, 255]]], dtype=np.uint8)
        weapon = np.array([[[1, 2, 3, 255]]], dtype=np.uint8)
        front_mask = np.array([[255]], dtype=np.uint8)

        holdout, report = layered_compositor.apply_character_holdout(
            character, weapon, front_mask, grid=(1, 1)
        )

        self.assertEqual(holdout.mode, "RGBA")
        self.assertEqual(holdout.getpixel((0, 0)), (0, 0, 0, 0))
        self.assertEqual(report["transparent_rgb_cleaned"], 1)

    def test_float_inputs_are_clamped_to_uint8(self) -> None:
        result = layered_compositor.calculate_holdout_alpha(
            np.array([[2.0]], dtype=np.float32),
            np.array([[-3.0]], dtype=np.float32),
            np.array([[9.0]], dtype=np.float32),
            grid=(1, 1),
        )

        self.assertEqual(result.dtype, np.uint8)
        self.assertEqual(int(result[0, 0]), 2)

    def test_mismatched_sizes_and_invalid_grid_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "mesma dimensão"):
            layered_compositor.calculate_holdout_alpha(
                np.zeros((2, 2), dtype=np.uint8),
                np.zeros((2, 3), dtype=np.uint8),
                np.zeros((2, 2), dtype=np.uint8),
                grid=(1, 1),
            )
        with self.assertRaisesRegex(ValueError, "grade"):
            layered_compositor.calculate_holdout_alpha(
                np.zeros((3, 3), dtype=np.uint8),
                np.zeros((3, 3), dtype=np.uint8),
                np.zeros((3, 3), dtype=np.uint8),
                grid=(2, 2),
            )


if __name__ == "__main__":
    unittest.main()
