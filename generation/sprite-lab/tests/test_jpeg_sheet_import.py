import tempfile
import unittest
from pathlib import Path
import sys

from PIL import Image, ImageDraw


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import jpeg_sheet_import  # noqa: E402


class JpegSheetImportTests(unittest.TestCase):
    def test_import_builds_cells_and_one_gif_per_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "sheet.jpeg"
            sheet = Image.new("RGB", (32, 32), (0, 0, 0))
            for row in range(2):
                for column in range(2):
                    x = column * 16 + 5 + column
                    y = row * 16 + 5
                    for px in range(x, x + 4):
                        for py in range(y, y + 6):
                            sheet.putpixel((px, py), (220, 120 + row * 20, 40))
            sheet.save(source, quality=100, subsampling=0)

            metadata = jpeg_sheet_import.import_sheet(
                source=source,
                output=root / "output",
                rows=2,
                phases=2,
                fps=8.0,
                background_threshold=24,
            )

            self.assertEqual(metadata["cell_size"], [16, 16])
            self.assertEqual(len(metadata["cells"]), 4)
            self.assertEqual(set(metadata["gifs"]), {"r1", "r2"})
            self.assertTrue((root / "output" / "animation_r1.gif").is_file())
            with Image.open(root / "output" / "row0_col0.png") as cell:
                self.assertEqual(cell.mode, "RGBA")
                self.assertEqual(cell.getpixel((0, 0))[3], 0)
                self.assertEqual(cell.getpixel((5, 5))[3], 255)

    def test_import_removes_chroma_magenta_background(self) -> None:
        image = Image.new("RGB", (16, 16), (255, 0, 255))
        for x in range(5, 11):
            for y in range(4, 13):
                image.putpixel((x, y), (220, 120, 40))

        result = jpeg_sheet_import._transparent_background(image, threshold=24)

        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((7, 8))[3], 255)

    def test_import_removes_dark_magenta_chroma_spill(self) -> None:
        image = Image.new("RGB", (32, 32), (255, 0, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 6, 28, 25), fill=(90, 55, 35))
        image.putpixel((9, 15), (105, 0, 89))
        image.putpixel((9, 16), (154, 105, 134))
        image.putpixel((9, 17), (192, 180, 200))
        image.putpixel((18, 15), (120, 20, 120))

        result = jpeg_sheet_import._transparent_background(
            image,
            threshold=24,
            background_key="magenta",
        )

        self.assertEqual(result.getpixel((9, 15))[3], 0)
        self.assertEqual(result.getpixel((9, 16))[3], 0)
        self.assertEqual(result.getpixel((9, 17))[3], 0)
        self.assertEqual(result.getpixel((18, 15))[3], 255)

    def test_teed_crop_ignores_border_artifacts(self) -> None:
        edge = Image.new("L", (32, 32), 0)
        draw = ImageDraw.Draw(edge)
        draw.rectangle((0, 0, 31, 31), outline=255)
        draw.rectangle((10, 8, 21, 25), outline=220)

        bounds = jpeg_sheet_import._teed_crop_bounds(
            edge,
            threshold=180,
            padding=2,
            border_margin=4,
        )

        self.assertEqual(bounds, (8, 6, 24, 28))

    def test_teed_removes_enclosed_black_background_pocket(self) -> None:
        image = Image.new("RGB", (32, 32), "black")
        draw = ImageDraw.Draw(image)
        draw.rectangle((6, 6, 25, 25), fill=(120, 80, 40))
        draw.rectangle((13, 13, 18, 19), fill="black")
        edge = Image.new("L", (32, 32), 0)
        ImageDraw.Draw(edge).rectangle((12, 12, 19, 20), outline=255)

        result = jpeg_sheet_import._transparent_background(
            image,
            threshold=16,
            background_key="none",
            edge=edge,
            edge_threshold=180,
            enclosed_min_area=16,
        )

        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((15, 16))[3], 0)
        self.assertEqual(result.getpixel((8, 8))[3], 255)

    def test_import_rejects_a_sheet_that_does_not_divide_evenly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "bad.jpeg"
            Image.new("RGB", (31, 32), (0, 0, 0)).save(source)
            with self.assertRaisesRegex(ValueError, "divisíveis"):
                jpeg_sheet_import.import_sheet(
                    source=source,
                    output=Path(temporary) / "output",
                    rows=2,
                    phases=2,
                )


if __name__ == "__main__":
    unittest.main()
