import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import postprocess_generated_sheet as subject  # noqa: E402


class GeneratedSheetPostprocessTests(unittest.TestCase):
    def test_reconstruct_alpha_removes_uniform_black_background(self) -> None:
        image = Image.new("RGB", (32, 32), "black")
        ImageDraw.Draw(image).rectangle((10, 5, 21, 27), fill=(180, 90, 30))
        result = subject.reconstruct_alpha(image)
        alpha = np.asarray(result.getchannel("A"))
        self.assertEqual(int(alpha[0, 0]), 0)
        self.assertGreater(int(alpha[10, 12]), 240)

    def test_alignment_restores_structural_scale_and_position(self) -> None:
        generated = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        ImageDraw.Draw(generated).rectangle((2, 3, 11, 19), fill="white")
        target = np.zeros((32, 32), dtype=bool)
        target[7:28, 13:26] = True
        alignment = subject.estimate_alignment(
            generated,
            target,
            max_translation=8,
            scale_steps=13,
        )
        normalized = subject.apply_alignment(generated, alignment)
        mask = np.asarray(normalized.getchannel("A")) > 32
        self.assertGreater(subject._iou(mask, target), 0.75)

    def test_process_sheet_writes_grid_gifs_metrics_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            structural = root / "structural"
            (structural / "lineart").mkdir(parents=True)
            cells = []
            sheet = Image.new("RGB", (64, 32), "black")
            for column in range(2):
                name = f"row0_col{column}"
                beauty = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
                ImageDraw.Draw(beauty).rectangle((9 + column, 5, 22 + column, 27), fill="white")
                beauty.save(structural / f"{name}.png")
                lineart = Image.new("L", (32, 32), 0)
                ImageDraw.Draw(lineart).rectangle((9 + column, 5, 22 + column, 27), outline=255)
                lineart.save(structural / "lineart" / f"{name}.png")
                generated = Image.new("RGB", (32, 32), "black")
                ImageDraw.Draw(generated).rectangle((7, 4, 20, 26), fill=(180, 80, 30))
                sheet.paste(generated, (column * 32, 0))
                cells.append(
                    {
                        "row": 0,
                        "column": column,
                        "bones": [
                            {"name": "ball_l", "head": [12 + column, 27], "tail": [15 + column, 27]},
                        ],
                    }
                )
            (structural / "render_metadata.json").write_text(
                json.dumps({"fps": 10, "directions": ["r1"], "cells": cells}),
                encoding="utf-8",
            )
            generated_sheet = root / "generated.jpg"
            sheet.save(generated_sheet, quality=95)
            output = root / "output"
            report = subject.process_sheet(
                generated_sheet,
                structural,
                output,
                rows=1,
                columns=2,
                cell_size=32,
            )
            self.assertEqual(len(report["frames"]), 2)
            self.assertTrue((output / "spritesheet.png").is_file())
            self.assertTrue((output / "animation_r1.gif").is_file())
            self.assertTrue((output / "review_sheet.png").is_file())
            self.assertTrue((output / "metrics.json").is_file())
            self.assertEqual(report["alignment_scale_limits"], [0.94, 1.06])
            self.assertEqual(len(report["background_color"]), 3)

    def test_production_alignment_rejects_large_per_frame_scale_jumps(self) -> None:
        generated = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        ImageDraw.Draw(generated).rectangle((20, 12, 39, 51), fill="white")
        target = np.zeros((64, 64), dtype=bool)
        target[4:60, 17:45] = True

        alignment = subject.estimate_alignment(
            generated,
            target,
            min_scale=0.94,
            max_scale=1.06,
        )

        self.assertGreaterEqual(alignment.scale, 0.94)
        self.assertLessEqual(alignment.scale, 1.06)


if __name__ == "__main__":
    unittest.main()
