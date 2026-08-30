import unittest

import numpy as np
from PIL import Image

from concept_palette_normalize import apply_chroma_shift


class ConceptPaletteNormalizeTest(unittest.TestCase):
    def test_shift_preserves_binary_alpha_and_changes_only_foreground(self) -> None:
        rgba = np.zeros((8, 8, 4), dtype=np.uint8)
        rgba[..., :3] = (20, 200, 20)
        rgba[2:6, 2:6, :3] = (130, 90, 50)
        rgba[2:6, 2:6, 3] = 255
        source = Image.fromarray(rgba, mode="RGBA")

        result = apply_chroma_shift(source, np.array([2.0, 3.0]), 0)
        output = np.asarray(result)

        self.assertEqual({0, 255}, set(np.unique(output[..., 3])))
        self.assertTrue(np.any(output[2:6, 2:6, :3] != rgba[2:6, 2:6, :3]))
        self.assertTrue(np.array_equal(output[..., 3], rgba[..., 3]))


if __name__ == "__main__":
    unittest.main()
