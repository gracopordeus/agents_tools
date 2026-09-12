import tempfile
import unittest
from pathlib import Path

from PIL import Image

import sys

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import layer_channel_assembler  # noqa: E402


class LayerChannelAssemblerTests(unittest.TestCase):
    def _cells(self, root: Path, *, size=(2, 3)) -> list[dict]:
        cells = []
        for row in range(8):
            for column in range(8):
                path = root / f"row{row}_col{column}.png"
                Image.new("RGBA", size, (row, column, 0, 255)).save(path)
                cells.append({"row": row, "column": column, "channel_path": str(path)})
        return cells

    def test_assembles_64_cells_in_row_major_order_with_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = layer_channel_assembler.assemble_channel(
                root,
                "character_beauty",
                self._cells(root),
                path_field="channel_path",
            )

            self.assertEqual(result["path"], "character_beauty.png")
            self.assertEqual(result["cell_count"], 64)
            self.assertEqual(result["cell_size"], [2, 3])
            self.assertEqual(result["atlas_size"], [16, 24])
            self.assertEqual(len(result["sha256"]), 64)
            with Image.open(root / result["path"]) as atlas:
                self.assertEqual(atlas.getpixel((0, 0)), (0, 0, 0, 255))
                self.assertEqual(atlas.getpixel((15, 23)), (7, 7, 0, 255))

    def test_rejects_missing_duplicate_and_wrong_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._cells(root)
            cases = {
                "ausente": cells[:-1],
                "duplicada": cells[:-1] + [dict(cells[0])],
            }
            wrong = list(cells)
            Image.new("RGBA", (3, 3)).save(wrong[-1]["channel_path"])
            cases["dimensão"] = wrong
            for message, candidate in cases.items():
                with self.subTest(message=message), self.assertRaisesRegex(
                    ValueError, message
                ):
                    layer_channel_assembler.assemble_channel(
                        root, "character_beauty", candidate,
                        path_field="channel_path",
                    )

    def test_metadata_centralizes_relative_channels_and_alignment(self) -> None:
        metadata = layer_channel_assembler.layer_channels_metadata(
            {"directions": [f"r{i}" for i in range(8)],
             "sampled_frames": list(range(8)), "camera": {"type": "ORTHO"}},
            weapon_component_id="weapon_1",
            channels={"weapon_beauty": {"path": "weapon_beauty.png", "sha256": "a" * 64}},
        )

        self.assertEqual(metadata["weapon_component_id"], "weapon_1")
        self.assertEqual(metadata["sampled_frames"], list(range(8)))
        self.assertEqual(metadata["camera"], {"type": "ORTHO"})
        self.assertEqual(metadata["front_mask"], "weapon_front_mask")
        self.assertIsNone(metadata["visible_component"])
        self.assertEqual(metadata["layer_channels"]["weapon_beauty"]["path"], "weapon_beauty.png")

    def test_assembles_optional_native_holdout_channel_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cells = self._cells(root)
            required_fields = tuple(layer_channel_assembler.LAYER_CHANNELS.values())
            for cell in cells:
                source = cell["channel_path"]
                for field in required_fields:
                    cell[field] = source
                cell["component_visible_path"] = source

            channels = layer_channel_assembler.assemble_layer_channels(
                root, {"cells": cells}
            )
            metadata = layer_channel_assembler.layer_channels_metadata(
                {
                    "directions": [f"r{i}" for i in range(8)],
                    "sampled_frames": list(range(8)),
                    "camera": {"type": "ORTHO"},
                },
                weapon_component_id="weapon_1",
                channels=channels,
            )

            self.assertIn("component_visible", channels)
            self.assertEqual(metadata["visible_component"], "component_visible")
            self.assertTrue((root / "component_visible.png").is_file())


if __name__ == "__main__":
    unittest.main()
