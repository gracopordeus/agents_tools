import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import conditioning_pack  # noqa: E402


def write_channel_frame(path: Path, color: tuple[int, int, int, int], offset: int = 0) -> None:
    image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8 + offset, 4, 23 + offset, 27), fill=color)
    image.save(path, format="PNG")


class ConditioningPackTests(unittest.TestCase):
    def test_build_pack_copies_channels_and_writes_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            output = root / "pack"
            for channel, color in {
                "beauty": (180, 80, 50, 255),
                "silhouette": (255, 255, 255, 255),
                "segmentation": (231, 76, 60, 255),
            }.items():
                (source / channel).mkdir(parents=True)
                for index in range(2):
                    write_channel_frame(
                        source / channel / f"f{index:02d}.png",
                        color,
                        offset=index,
                    )
            target = root / "target.png"
            write_channel_frame(target, (40, 160, 220, 255))

            manifest = conditioning_pack.build_pack(
                source,
                output,
                target,
                action="run",
                direction="r1",
                foot_anchor=(16, 28),
            )

            self.assertEqual(manifest["id"], "conditioning-run-r1")
            self.assertEqual(manifest["frame_count"], 2)
            self.assertEqual(manifest["foot_anchor"], [16, 28])
            self.assertTrue((output / "manifest.json").is_file())
            self.assertTrue((output / "conditioning-pack.png").is_file())
            self.assertTrue((output / "beauty" / "f00.png").is_file())
            self.assertTrue((output / "target-reference" / "target.png").is_file())

            with Image.open(output / "beauty" / "f01.png") as image:
                self.assertEqual(image.size, (32, 32))

    def test_build_pack_rejects_channel_with_different_frame_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            for channel in ("beauty", "silhouette", "segmentation"):
                (source / channel).mkdir(parents=True)
                write_channel_frame(source / channel / "f00.png", (255, 255, 255, 255))
            write_channel_frame(source / "segmentation" / "f01.png", (255, 0, 0, 255))
            target = root / "target.png"
            write_channel_frame(target, (40, 160, 220, 255))
            with self.assertRaisesRegex(ValueError, "canal segmentation"):
                conditioning_pack.build_pack(
                    source,
                    root / "pack",
                    target,
                    action="idle",
                    direction="r1",
                )


if __name__ == "__main__":
    unittest.main()
