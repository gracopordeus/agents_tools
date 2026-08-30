import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import semantic_preview  # noqa: E402


class SemanticPreviewTests(unittest.TestCase):
    def test_default_preview_fps_produces_readable_gif_timing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = []
            for index in range(2):
                frame = root / f"frame_{index}.png"
                Image.new("RGBA", (8, 8), (index * 80, 0, 0, 255)).save(frame)
                frames.append(str(frame))

            output = root / "animation.gif"
            semantic_preview._build_gif(
                frames, output, semantic_preview.DEFAULT_PREVIEW_FPS
            )

            with Image.open(output) as gif:
                # GIF stores frame delays in 10 ms units, so 6 FPS is saved as
                # 160 ms per frame (6.25 effective FPS).
                self.assertEqual(gif.info["duration"], 160)


if __name__ == "__main__":
    unittest.main()
