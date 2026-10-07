import copy
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parallel_sprite_render as subject


class ParallelRenderTests(unittest.TestCase):
    def fixture(self, root, assigned):
        cells = []
        for row in assigned:
            for col in range(8):
                path = root / f"row{row}_col{col}.png"
                Image.new("RGBA", (4, 4), (row, col, 127, 255)).save(path)
                cells.append({"row": row, "column": col, "direction": f"r{row+1}",
                              "frame": col, "path": str(path)})
        return {"directions": [f"r{i+1}" for i in range(8)],
                "sampled_frames": list(range(8)), "cell": [4, 4],
                "camera": {"ortho_scale": 3}, "cells": cells}

    def test_real_concurrent_workers_produce_all_64_cells_in_order(self):
        for count in (2, 4):
            with self.subTest(workers=count), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                request = root / "request.json"
                request.write_text(json.dumps({"rows": 8, "phases": 8, "blender_workers": count}))
                barrier = threading.Barrier(count)
                def run(command, directory, result, backend, timeout, **kwargs):
                    payload = json.loads(Path(command[-1]).read_text())
                    self.assertEqual(payload["rows"], 8)
                    self.assertEqual(payload["phases"], 8)
                    barrier.wait(timeout=5)  # Fails if the workers are serialized.
                    report = self.fixture(directory, payload["assigned_rows"])
                    result.write_text(json.dumps(report))
                    return subprocess.CompletedProcess(command, 0, "", "")
                result = root / "result.json"
                completed = subject.run_backend(["blender", "--request", str(request)],
                    root, result, "software", 30, run_worker=run)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                report = json.loads(result.read_text())
                self.assertEqual(len(report["cells"]), 64)
                self.assertEqual([(c["row"], c["column"]) for c in report["cells"]],
                                 [(r, c) for r in range(8) for c in range(8)])
                for r in range(8):
                    for c in range(8):
                        with Image.open(root / f"row{r}_col{c}.png") as image:
                            self.assertEqual(image.getpixel((0, 0)), (r, c, 127, 255))

    def test_merge_rejects_missing_duplicate_and_camera_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.fixture(root, list(range(4)))
            second = self.fixture(root, list(range(4, 8)))
            for failure in ("missing", "duplicate", "camera"):
                other = copy.deepcopy(second)
                if failure == "missing": other["cells"].pop()
                if failure == "duplicate": other["cells"].append(first["cells"][0])
                if failure == "camera": other["camera"]["ortho_scale"] = 4
                with self.subTest(failure=failure), self.assertRaises(RuntimeError):
                    subject.merge_reports({"rows": 8, "phases": 8}, [first, other], root)

    def test_failure_cancels_peer_and_never_publishes_sheet(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "request.json"
            request.write_text(json.dumps({"rows": 8, "phases": 8, "blender_workers": 2}))
            barrier = threading.Barrier(2)
            def run(command, directory, result, backend, timeout, **kwargs):
                barrier.wait(timeout=5)
                if directory.name == "0":
                    return subprocess.CompletedProcess(command, 1, "", "render failed")
                self.assertTrue(kwargs["cancel_event"].wait(timeout=5))
                return subprocess.CompletedProcess(command, 1, "", "cancelled")
            result = root / "result.json"
            completed = subject.run_backend(["blender", "--request", str(request)],
                root, result, "software", 30, run_worker=run)
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(result.exists())

    def test_partitions_cover_64_and_ai_base_rows_without_overlap(self):
        for rows in (5, 8):
            for count in (1, 2, 4):
                partitions = subject.row_partitions(rows, count)
                self.assertEqual(sorted(r for group in partitions for r in group), list(range(rows)))
