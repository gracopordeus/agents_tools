import tempfile
import unittest
from pathlib import Path

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(SPRITE_LAB))

import build_render_channel_sheets as subject  # noqa: E402


class RenderChannelSheetTests(unittest.TestCase):
    def test_bones_sheet_preserves_openpose_rgb_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bones").mkdir()
            Image.new("RGB", (2, 2), (255, 0, 0)).save(root / "bones" / "row0_col0.png")
            result = subject.build_channel(root, "bones", 1, 1, 2)
            with Image.open(result) as sheet:
                self.assertEqual(sheet.mode, "RGBA")
                self.assertEqual(sheet.getpixel((0, 0))[:3], (255, 0, 0))


if __name__ == "__main__":
    unittest.main()
