import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import holdout_validation  # noqa: E402


class HoldoutValidationTests(unittest.TestCase):
    def _image(self, path: Path, *, box: tuple[int, int, int, int], color=(220, 80, 40, 255)) -> Path:
        image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        image.paste(color, box)
        image.save(path, format="PNG")
        return path

    def _cell(self, root: Path, *, generated_box=(2, 2, 5, 5), rgb=(220, 80, 40, 255)) -> dict:
        structural = self._image(root / "structural.png", box=(2, 2, 5, 5), color=(255, 255, 255, 255))
        generated = self._image(root / "generated.png", box=generated_box, color=rgb)
        return {
            "row": 0,
            "column": 0,
            "structural_path": structural,
            "generated_path": generated,
            "attachment": [3.0, 3.0],
        }

    def test_aligned_cell_reports_bbox_iou_and_no_violations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = holdout_validation.validate_holdout_outputs(
                [self._cell(Path(temporary))], grid=(1, 1)
            )

            self.assertTrue(report["validated"])
            self.assertEqual(report["violation_count"], 0)
            metrics = report["cells"][0]
            self.assertEqual(metrics["structural_bbox"], [2, 2, 4, 4])
            self.assertEqual(metrics["generated_bbox"], [2, 2, 4, 4])
            self.assertEqual(metrics["silhouette_iou"], 1.0)
            self.assertEqual(metrics["center_error_px"], 0.0)
            self.assertEqual(metrics["attachment_error_px"], 0.0)

    def test_shifted_weapon_is_blocking_with_specific_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = holdout_validation.validate_holdout_outputs(
                [self._cell(Path(temporary), generated_box=(6, 2, 10, 5))],
                grid=(1, 1),
                thresholds={"max_center_error_px": 2.0, "warning_center_error_px": 1.0},
            )

            self.assertFalse(report["validated"])
            codes = {item["code"] for item in report["violations"]}
            self.assertIn("silhouette_iou", codes)
            self.assertIn("center_offset", codes)
            self.assertIn("out_of_envelope", codes)
            self.assertTrue(all(item["severity"] == "error" for item in report["violations"]))

    def test_border_pixels_and_invisible_rgb_are_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            structural = self._image(root / "structural.png", box=(2, 2, 5, 5), color=(255, 255, 255, 255))
            generated = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            generated.putpixel((0, 3), (120, 30, 10, 255))
            generated.putpixel((1, 1), (4, 5, 6, 0))
            generated.save(root / "generated.png", format="PNG")

            report = holdout_validation.validate_holdout_outputs(
                [{
                    "row": 0,
                    "column": 0,
                    "structural_path": structural,
                    "generated_path": root / "generated.png",
                }],
                grid=(1, 1),
            )

            codes = {item["code"] for item in report["violations"]}
            self.assertIn("border_pixels", codes)
            self.assertIn("invisible_rgb", codes)
            self.assertGreater(report["cells"][0]["invisible_rgb_pixels"], 0)

    def test_tolerable_deviation_is_warning_and_report_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "holdout_validation.json"
            report = holdout_validation.validate_holdout_outputs(
                [self._cell(root, generated_box=(3, 2, 6, 5))],
                grid=(1, 1),
                output_path=output,
                thresholds={
                    "min_iou": 0.20,
                    "warning_iou": 0.80,
                    "max_center_error_px": 3.0,
                    "warning_center_error_px": 1.0,
                    "max_outside_structural_pixels": 20,
                    "max_border_pixels": 20,
                },
            )

            self.assertTrue(report["validated"])
            self.assertGreater(report["warning_count"], 0)
            self.assertTrue(output.is_file())
            persisted = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema"], "sprite_lab.holdout_validation/v1")
            self.assertEqual(persisted["thresholds"]["min_iou"], 0.2)
            self.assertEqual(persisted["cells"][0]["row"], 0)

    def test_validates_64_cells_and_rejects_non_rgba_generated_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = []
            for row in range(8):
                for column in range(8):
                    structural = self._image(
                        root / f"structural_{row}_{column}.png",
                        box=(2, 2, 5, 5),
                        color=(255, 255, 255, 255),
                    )
                    generated = self._image(
                        root / f"generated_{row}_{column}.png",
                        box=(2, 2, 5, 5),
                    )
                    cells.append({
                        "row": row,
                        "column": column,
                        "structural_path": structural,
                        "generated_path": generated,
                    })

            report = holdout_validation.validate_holdout_outputs(cells)

            self.assertEqual(report["cell_count"], 64)
            self.assertTrue(report["validated"])
            self.assertEqual(len(report["cells"]), 64)

    def test_rejects_non_finite_thresholds_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "holdout_validation.json"
            for value in (math.nan, math.inf, -math.inf):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finito"):
                    holdout_validation.validate_holdout_outputs(
                        [self._cell(root)],
                        grid=(1, 1),
                        thresholds={"min_iou": value},
                        output_path=output,
                    )
                self.assertFalse(output.exists())

    def test_rejects_non_finite_attachment_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for value in (math.nan, math.inf, -math.inf):
                cell = self._cell(root)
                cell["attachment"] = [value, 3.0]
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "finit"):
                    holdout_validation.validate_holdout_outputs([cell], grid=(1, 1))

    def test_persisted_report_is_strict_json_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "holdout_validation.json"
            report = holdout_validation.validate_holdout_outputs(
                [self._cell(root)], grid=(1, 1), output_path=output
            )
            serialized = output.read_text(encoding="utf-8")
            self.assertNotIn("NaN", serialized)
            self.assertNotIn("Infinity", serialized)
            persisted = json.loads(serialized)
            self.assertEqual(persisted, report)


if __name__ == "__main__":
    unittest.main()
