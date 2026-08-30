import unittest

import numpy as np

from temporal_palette_refine import bounded_shift, stabilize_rgba


class TemporalPaletteRefineTest(unittest.TestCase):
    def test_bounded_shift_limits_magnitude(self) -> None:
        shift = bounded_shift(np.array([3.0, 4.0]), 1.5)
        self.assertAlmostEqual(1.5, float(np.linalg.norm(shift)), places=5)

    def test_stabilization_preserves_alpha(self) -> None:
        rgba = np.zeros((8, 8, 4), dtype=np.uint8)
        rgba[2:6, 2:6] = (130, 90, 50, 255)
        result = stabilize_rgba(rgba, np.array([1.0, 1.0]))
        self.assertTrue(np.array_equal(rgba[..., 3], result[..., 3]))
        self.assertTrue(np.any(rgba[2:6, 2:6, :3] != result[2:6, 2:6, :3]))

    def test_zero_shift_is_byte_identical(self) -> None:
        rgba = np.arange(8 * 8 * 4, dtype=np.uint8).reshape(8, 8, 4)
        result = stabilize_rgba(rgba, np.zeros(2, dtype=np.float32))
        self.assertTrue(np.array_equal(rgba, result))


if __name__ == "__main__":
    unittest.main()
