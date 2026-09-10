import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import controlnet_sprite_channels as subject  # noqa: E402


class _FakePiDiNet:
    def __call__(self, image, **_kwargs):
        return Image.new("L", image.size, 64)


class _FakeOpenPose:
    def __call__(self, image, **_kwargs):
        pose = np.zeros((image.height, image.width, 3), dtype=np.uint8)
        pose[0, 0] = (255, 0, 0)
        return Image.fromarray(pose, mode="RGB")


class ControlNetSpriteChannelTests(unittest.TestCase):
    def test_infer_channels_preserves_softedge_alpha_and_openpose_colors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGBA", (8, 8), (128, 128, 128, 255)).save(root / "row0_col0.png")
            with (
                mock.patch.object(subject, "require_cuda", return_value="cuda"),
                mock.patch.object(subject, "GPULease"),
                mock.patch.object(
                    subject,
                    "_load_detectors",
                    return_value=(_FakePiDiNet(), _FakeOpenPose()),
                ),
            ):
                report = subject.infer_channels(root, 1, 1, 8, device="cuda")

            self.assertEqual(report["lineart"]["processor"], "hed_softedge")
            self.assertEqual(report["bones"]["processor"], "openpose")
            self.assertEqual(report["device"], "cuda")
            with Image.open(root / "lineart" / "row0_col0.png") as lineart:
                self.assertEqual(lineart.mode, "RGBA")
                self.assertEqual(lineart.getpixel((0, 0)), (255, 255, 255, 64))
            with Image.open(root / "bones" / "row0_col0.png") as bones:
                self.assertEqual(bones.mode, "RGB")
                self.assertEqual(bones.getpixel((0, 0)), (255, 0, 0))

    def test_cpu_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "CPU desabilitada"):
            subject.require_cuda("cpu")

    def test_two_channel_workers_preserve_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for row in range(2):
                Image.new("RGBA", (8, 8), (128, 128, 128, 255)).save(
                    root / f"row{row}_col0.png"
                )
            with (
                mock.patch.object(subject, "require_cuda", return_value="cuda"),
                mock.patch.object(subject, "GPULease"),
                mock.patch.object(subject, "_cuda_stream", return_value=None),
                mock.patch.object(
                    subject,
                    "_load_detectors",
                    return_value=(_FakePiDiNet(), _FakeOpenPose()),
                ),
            ):
                report = subject.infer_channels(root, 2, 1, 8, channel_workers=2)

            self.assertEqual(report["channel_workers"], 2)
            self.assertEqual(len(report["cells"]), 2)
            self.assertTrue((root / "lineart" / "row1_col0.png").is_file())
            self.assertTrue((root / "bones" / "row1_col0.png").is_file())

    def test_eight_cell_workers_preserve_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for row in range(8):
                Image.new("RGBA", (8, 8), (128, 128, 128, 255)).save(
                    root / f"row{row}_col0.png"
                )
            with (
                mock.patch.object(subject, "require_cuda", return_value="cuda"),
                mock.patch.object(subject, "GPULease"),
                mock.patch.object(subject, "_cuda_stream", return_value=None),
                mock.patch.object(
                    subject,
                    "_load_detectors",
                    return_value=(_FakePiDiNet(), _FakeOpenPose()),
                ),
            ):
                report = subject.infer_channels(root, 8, 1, 8, channel_workers=8)

            self.assertEqual(report["worker_strategy"], "parallel_cells_shared_models")
            self.assertEqual(len(report["cells"]), 8)
            self.assertTrue((root / "lineart" / "row7_col0.png").is_file())
            self.assertTrue((root / "bones" / "row7_col0.png").is_file())

    def test_channel_workers_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "channel_workers"):
            subject.infer_channels(Path("/tmp"), 1, 1, 8, channel_workers=3)

    def test_missing_cuda_never_falls_back(self):
        with mock.patch.object(subject, "resolve_device", return_value="cpu"):
            with self.assertRaisesRegex(RuntimeError, "CUDA indisponível"):
                subject.require_cuda("auto")


if __name__ == "__main__":
    unittest.main()
