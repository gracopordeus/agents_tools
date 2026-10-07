import argparse
import json
from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import postprocess_runtime as runtime
import huggingface_realesrgan as catalog

HAS_TORCH = find_spec("torch") is not None
HAS_CV2 = find_spec("cv2") is not None


class RuntimeTests(unittest.TestCase):
    @unittest.skipUnless(HAS_TORCH, "requires torch")
    def test_auto_selects_cuda_and_cpu(self):
        with patch("torch.cuda.is_available", return_value=True):
            self.assertEqual(runtime.resolve_device("auto"), "cuda")
            self.assertEqual(runtime.resolve_device("cpu"), "cpu")
        with patch("torch.cuda.is_available", return_value=False):
            self.assertEqual(runtime.resolve_device("auto"), "cpu")
            with self.assertRaisesRegex(RuntimeError, "CUDA"):
                runtime.resolve_device("cuda")

    @unittest.skipUnless(HAS_TORCH, "requires torch")
    def test_invalid_precision_is_not_silently_downgraded(self):
        with self.assertRaises(ValueError):
            runtime.resolve_dtype("cpu", "fp16")
        with self.assertRaises(ValueError):
            runtime.resolve_dtype("cuda", "bf16")

    def test_default_precision_is_fp32(self):
        parser = argparse.ArgumentParser()
        runtime.add_runtime_arguments(parser)
        args = parser.parse_args([])
        self.assertEqual(args.precision, "fp32")
        self.assertEqual(args.batch_size, 2)
        self.assertEqual(args.cpu_workers, 4)

    def test_parallelism_rejects_non_positive_values(self):
        with self.assertRaises(ValueError):
            runtime.validate_parallelism(0, 4)
        with self.assertRaises(ValueError):
            runtime.validate_parallelism(4, 0)

    def test_alternative_checkpoints_have_pinned_hashes(self):
        for name in ("swinir_light_x2", "swinir_m_classical_df2k_x2", "realcugan_x2", "realcugan_conservative_x2"):
            p = catalog.profile(name)
            self.assertEqual(len(p["sha256"]), 64)
            self.assertEqual(p["network_scale"], 2)
            self.assertNotEqual(p["architecture"], "rrdb")

    def test_modified_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file = root / "work/upscale-models/swinir_light_x2.pth"
            file.parent.mkdir(parents=True)
            file.write_bytes(b"invalid")
            with patch.object(catalog, "BASE", root):
                with self.assertRaisesRegex(RuntimeError, "alterado"):
                    catalog.download_weight("swinir_light_x2")

    @unittest.skipUnless(HAS_CV2 and HAS_TORCH, "requires cv2 and torch")
    def test_bicubic_cli_preserves_alpha_and_reports_cpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            array = np.zeros((32, 32, 4), dtype=np.uint8)
            array[8:24, 8:24] = [100, 50, 20, 255]
            original = Image.fromarray(array)
            original.save(source / "row0_col0.png")
            result = subprocess.run([sys.executable, str(ROOT / "realesrgan_anime_scale.py"),
                str(source), str(root / "output"), "--realesrgan-repo", str(root),
                "--model-profile", "bicubic", "--device", "cpu", "--rows", "1", "--phases", "1",
                "--alpha-filter", "nearest"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["realesrgan"]["device"], "cpu")
            actual = Image.open(root / "output/row0_col0.png")
            expected = original.getchannel("A").resize((64, 64), Image.Resampling.NEAREST)
            np.testing.assert_array_equal(np.asarray(actual.getchannel("A")), np.asarray(expected))


if __name__ == "__main__":
    unittest.main()
