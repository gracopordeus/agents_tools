import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from source_alpha_intersection import intersect_with_source_alpha


class SourceAlphaIntersectionTests(unittest.TestCase):
    def test_non_decodable_fixture_is_reported_as_not_applicable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "generated.png"
            source.write_text("fake generated sheet", encoding="utf-8")

            report = intersect_with_source_alpha(
                source, root / "output", root / "masks", 8, 8
            )

            self.assertEqual(report, {
                "applied": False,
                "applied_cells": 0,
                "skipped": True,
                "reason": "source_not_decodable",
            })

    def test_transparent_provider_pixels_cannot_become_foreground(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
            source.putpixel((1, 1), (200, 50, 10, 255))
            source.save(root / "source.png")
            output = root / "output"
            masks = root / "masks"
            output.mkdir(); masks.mkdir()
            Image.new("RGBA", (8, 8), (99, 88, 77, 255)).save(output / "row0_col0.png")
            Image.new("L", (8, 8), 255).save(masks / "row0_col0.png")

            report = intersect_with_source_alpha(root / "source.png", output, masks, 1, 1)

            with Image.open(output / "row0_col0.png") as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
                self.assertEqual(image.getpixel((2, 2))[3], 255)
            self.assertEqual(report["applied_cells"], 1)


if __name__ == "__main__":
    unittest.main()
