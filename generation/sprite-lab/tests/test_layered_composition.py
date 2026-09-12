import hashlib
import random
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import layered_compositor  # noqa: E402


class LayeredCompositionTests(unittest.TestCase):
    def _cells(self, root: Path) -> list[dict[str, object]]:
        cells = []
        for row in range(8):
            for column in range(8):
                character = root / f"character_{row}_{column}.png"
                weapon = root / f"weapon_{row}_{column}.png"
                mask = root / f"mask_{row}_{column}.png"
                character_color = (10 + row, 20 + column, 30, 255)
                weapon_color = (180, 40 + row, 70 + column, 255)
                Image.new("RGBA", (2, 2), character_color).save(character)
                Image.new("RGBA", (2, 2), weapon_color).save(weapon)
                front = row == 0 and column == 0
                Image.new("L", (2, 2), 255 if front else 0).save(mask)
                cells.append({
                    "row": row,
                    "column": column,
                    "character_path": character,
                    "weapon_path": weapon,
                    "front_mask_path": mask,
                })
        return cells

    def test_composes_64_cells_in_row_major_order_and_preserves_weapon(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._cells(root)
            random.Random(42).shuffle(cells)
            outputs = layered_compositor.compose_layered_spritesheets(
                cells, root / "out"
            )

            self.assertEqual(outputs["cell_count"], 64)
            self.assertEqual(outputs["grid"], [8, 8])
            self.assertEqual(outputs["cell_size"], [2, 2])
            self.assertEqual(
                set(outputs["outputs"]),
                {
                    "character_holdout_spritesheet",
                    "weapon_spritesheet",
                    "holdout_cut_mask",
                    "composite_preview",
                },
            )
            for path in outputs["outputs"].values():
                with Image.open(path) as image:
                    self.assertEqual(image.mode, "RGBA")
                    self.assertEqual(image.size, (16, 16))

            with Image.open(outputs["weapon_spritesheet"]) as weapon_sheet:
                self.assertEqual(weapon_sheet.getpixel((0, 0)), (180, 40, 70, 255))
                self.assertEqual(weapon_sheet.getpixel((15, 15)), (180, 47, 77, 255))
            with Image.open(outputs["character_holdout_spritesheet"]) as character_sheet:
                self.assertEqual(character_sheet.getpixel((0, 0)), (0, 0, 0, 0))
                self.assertEqual(character_sheet.getpixel((2, 0)), (10, 21, 30, 255))

    def test_v2_keeps_character_full_and_masks_component_in_each_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._cells(root)
            random.Random(7).shuffle(cells)

            outputs = layered_compositor.compose_layered_spritesheets_v2(
                cells,
                root / "out_v2",
            )

            self.assertEqual(outputs["schema"], "sprite_lab.layered_composition/v2")
            self.assertEqual(
                outputs["composition_order"],
                ["character_full", "component_visible"],
            )
            self.assertTrue(outputs["character_immutable"])
            self.assertEqual(outputs["cell_count"], 64)
            self.assertEqual(outputs["grid"], [8, 8])
            self.assertEqual(outputs["cell_size"], [2, 2])
            self.assertEqual(
                set(outputs["outputs"]),
                {
                    "character_full_spritesheet",
                    "component_visible_spritesheet",
                    "component_visibility_mask",
                    "composite_preview",
                },
            )

            with Image.open(outputs["character_full_spritesheet"]) as character:
                self.assertEqual(character.size, (16, 16))
                self.assertEqual(character.getpixel((0, 0)), (10, 20, 30, 255))
                self.assertEqual(character.getpixel((2, 0)), (10, 21, 30, 255))
            with Image.open(outputs["component_visible_spritesheet"]) as component:
                self.assertEqual(component.getpixel((0, 0)), (180, 40, 70, 255))
                self.assertEqual(component.getpixel((2, 0)), (0, 0, 0, 0))
            with Image.open(outputs["component_visibility_mask"]) as mask:
                self.assertEqual(mask.getpixel((0, 0)), (255, 255, 255, 255))
                self.assertEqual(mask.getpixel((2, 0)), (255, 255, 255, 0))
            with Image.open(outputs["composite_preview"]) as preview:
                self.assertEqual(preview.getpixel((0, 0)), (180, 40, 70, 255))
                self.assertEqual(preview.getpixel((2, 0)), (10, 21, 30, 255))

    def test_preview_places_weapon_below_character_or_exposes_front_weapon(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._cells(root)
            outputs = layered_compositor.compose_layered_spritesheets(cells, root / "out")

            with Image.open(outputs["composite_preview"]) as preview:
                # Front cell: character was held out, so weapon is visible.
                self.assertEqual(preview.getpixel((0, 0)), (180, 40, 70, 255))
                # Rear cell: opaque character remains above the weapon.
                self.assertEqual(preview.getpixel((2, 0)), (10, 21, 30, 255))

            with Image.open(outputs["holdout_cut_mask"]) as holdout_mask:
                self.assertEqual(holdout_mask.getpixel((0, 0)), (255, 255, 255, 255))
                self.assertEqual(holdout_mask.getpixel((2, 0)), (255, 255, 255, 0))

    def test_missing_cell_is_rejected_before_writing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._cells(root)
            cells.pop()
            output = root / "out"
            with self.assertRaisesRegex(ValueError, "célula ausente"):
                layered_compositor.compose_layered_spritesheets(cells, output)
            self.assertFalse(output.exists())

    def test_same_inputs_produce_byte_identical_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._cells(root)
            first = layered_compositor.compose_layered_spritesheets(cells, root / "first")
            second = layered_compositor.compose_layered_spritesheets(cells, root / "second")

            for name in first["outputs"]:
                first_bytes = Path(first["outputs"][name]).read_bytes()
                second_bytes = Path(second["outputs"][name]).read_bytes()
                self.assertEqual(hashlib.sha256(first_bytes).digest(), hashlib.sha256(second_bytes).digest())


if __name__ == "__main__":
    unittest.main()
