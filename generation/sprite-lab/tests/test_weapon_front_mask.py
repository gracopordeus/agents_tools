import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import weapon_front_mask  # noqa: E402


class WeaponFrontMaskTests(unittest.TestCase):
    def test_report_preserves_alignment_and_64_detached_cell_metrics(self) -> None:
        primary = {
            "directions": [f"r{index}" for index in range(1, 9)],
            "sampled_frames": list(range(30, 38)),
            "camera": {"type": "ORTHO", "ortho_scale": 4.0},
            "cell": [512, 512],
        }
        reports = [
            {
                "row": row,
                "column": column,
                "front_pixel_count": row + column,
                "area": row + column,
                "bounding_box": None,
                "dilated_area": row + column + 1,
            }
            for row in range(8)
            for column in range(8)
        ]

        metadata = weapon_front_mask.front_mask_metadata(primary, reports, 2)

        self.assertEqual(len(metadata["cells"]), 64)
        self.assertEqual(metadata["directions"], primary["directions"])
        self.assertEqual(metadata["sampled_frames"], primary["sampled_frames"])
        self.assertEqual(metadata["camera"], primary["camera"])
        self.assertEqual(metadata["dilation"], 2)
        reports[0]["area"] = 999
        self.assertEqual(metadata["cells"][0]["area"], 0)

    def test_extracts_only_visible_weapon_role_and_preserves_transparency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segmentation = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            segmentation.putpixel((2, 3), weapon_front_mask.WEAPON_COLOR)
            segmentation.putpixel((3, 3), weapon_front_mask.WEAPON_COLOR)
            segmentation.putpixel((4, 3), (231, 76, 60, 255))
            source = root / "segmentation.png"
            output = root / "mask.png"
            segmentation.save(source)

            report = weapon_front_mask.extract_weapon_front_mask(
                source, output, dilation=0
            )

            with Image.open(output) as mask:
                self.assertEqual(mask.mode, "RGBA")
                self.assertEqual(mask.getpixel((2, 3)), (255, 255, 255, 255))
                self.assertEqual(mask.getpixel((3, 3)), (255, 255, 255, 255))
                self.assertEqual(mask.getpixel((4, 3)), (255, 255, 255, 0))
                self.assertEqual(mask.getpixel((0, 0)), (255, 255, 255, 0))
            self.assertEqual(report["front_pixel_count"], 2)
            self.assertEqual(report["area"], 2)
            self.assertEqual(report["bounding_box"], [2, 3, 3, 3])
            self.assertEqual(report["dilated_area"], 2)

    def test_production_role_colors_cannot_be_classified_as_weapon(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segmentation = Image.new("RGBA", (4, 1), (0, 0, 0, 0))
            segmentation.putpixel((0, 0), weapon_front_mask.WEAPON_COLOR)
            # Exact production ROLE_COLORS values for unclassified and torso meshes.
            segmentation.putpixel((1, 0), (189, 195, 199, 255))
            segmentation.putpixel((2, 0), (241, 196, 15, 255))
            source = root / "production_roles.png"
            output = root / "mask.png"
            segmentation.save(source)

            report = weapon_front_mask.extract_weapon_front_mask(source, output)

            with Image.open(output) as mask:
                self.assertEqual(
                    [mask.getpixel((x, 0))[3] for x in range(4)],
                    [255, 0, 0, 0],
                )
            self.assertEqual(report["front_pixel_count"], 1)

    def test_production_palette_resolver_is_binary_for_all_nonselected_parts(self) -> None:
        palette = weapon_front_mask.front_mask_palette()

        self.assertEqual(
            palette[weapon_front_mask.front_mask_palette_key("weapon_1", "weapon_1")],
            weapon_front_mask.WEAPON_COLOR,
        )
        for component_identifier in (None, "torso_1", "weapon_2"):
            self.assertEqual(
                palette[
                    weapon_front_mask.front_mask_palette_key(
                        component_identifier, "weapon_1"
                    )
                ],
                weapon_front_mask.BACKGROUND_COLOR,
            )

    def test_weapon_behind_character_produces_empty_mask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            segmentation = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            segmentation.putpixel((3, 3), (231, 76, 60, 255))
            source = root / "rear.png"
            output = root / "mask.png"
            segmentation.save(source)

            report = weapon_front_mask.extract_weapon_front_mask(source, output)

            self.assertEqual(report["front_pixel_count"], 0)
            self.assertEqual(report["bounding_box"], None)
            with Image.open(output) as mask:
                self.assertIsNone(mask.getchannel("A").getbbox())

    def test_dilation_is_configurable_and_cannot_cross_cell_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
            first.putpixel((3, 2), weapon_front_mask.WEAPON_COLOR)
            second = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
            first_path = root / "first.png"
            second_path = root / "second.png"
            first.save(first_path)
            second.save(second_path)

            first_report = weapon_front_mask.extract_weapon_front_mask(
                first_path, root / "first_mask.png", dilation=1
            )
            second_report = weapon_front_mask.extract_weapon_front_mask(
                second_path, root / "second_mask.png", dilation=1
            )

            self.assertEqual(first_report["front_pixel_count"], 1)
            self.assertEqual(first_report["dilated_area"], 6)
            self.assertEqual(first_report["dilated_bounding_box"], [2, 1, 3, 3])
            self.assertEqual(second_report["dilated_area"], 0)


if __name__ == "__main__":
    unittest.main()
