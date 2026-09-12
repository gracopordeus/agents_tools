import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


class EnvAtlasJobsTests(unittest.TestCase):
    def _patch_jobs_path(self, path: Path) -> None:
        original = server.ENV_ATLAS_JOBS_PATH
        server.ENV_ATLAS_JOBS_PATH = path
        self.addCleanup(setattr, server, "ENV_ATLAS_JOBS_PATH", original)

    def test_create_get_and_list_roundtrip(self) -> None:
        with TemporaryDirectory() as temporary:
            jobs_path = Path(temporary) / "env_atlas_jobs.json"
            self._patch_jobs_path(jobs_path)
            job = server._create_env_atlas_job({"directions": 8})
            self.assertTrue(job["id"].startswith("env_atlas_"))
            self.assertEqual(job["status"], "queued")
            self.assertEqual(server.get_env_atlas_job(job["id"])["id"], job["id"])
            self.assertEqual(len(server.read_env_atlas_jobs()), 1)
            self.assertIsNone(server.get_env_atlas_job("missing"))

    def test_worker_marks_done_and_records_outputs(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._patch_jobs_path(root / "env_atlas_jobs.json")
            output_dir = root / "output"
            output_dir.mkdir()
            job = server._create_env_atlas_job({})

            def fake_run(*args, **kwargs):
                (output_dir / "env_atlas.png").write_bytes(b"png")
                return SimpleNamespace(returncode=0, stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run):
                server._run_env_atlas_job(job["id"], ["render"], output_dir, cwd=str(root))
            finished = server.get_env_atlas_job(job["id"])
            self.assertEqual(finished["status"], "done")
            self.assertEqual(
                finished["outputs"], {"atlas_path": "/env-atlas/output/env_atlas.png"}
            )
            self.assertIn("finished_at", finished)

    def test_worker_marks_error_on_blender_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._patch_jobs_path(root / "env_atlas_jobs.json")
            output_dir = root / "output"
            output_dir.mkdir()
            job = server._create_env_atlas_job({})

            def fake_run(*args, **kwargs):
                return SimpleNamespace(returncode=3, stderr="blender quebrou")

            with mock.patch("subprocess.run", side_effect=fake_run):
                server._run_env_atlas_job(job["id"], ["render"], output_dir, cwd=str(root))
            finished = server.get_env_atlas_job(job["id"])
            self.assertEqual(finished["status"], "error")
            self.assertIn("blender quebrou", finished["error"])

    def test_worker_marks_error_when_atlas_missing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._patch_jobs_path(root / "env_atlas_jobs.json")
            output_dir = root / "output"
            output_dir.mkdir()
            job = server._create_env_atlas_job({})

            def fake_run(*args, **kwargs):
                return SimpleNamespace(returncode=0, stderr="")

            with mock.patch("subprocess.run", side_effect=fake_run):
                server._run_env_atlas_job(job["id"], ["render"], output_dir, cwd=str(root))
            finished = server.get_env_atlas_job(job["id"])
            self.assertEqual(finished["status"], "error")
            self.assertIn("não foi gerado", finished["error"])


if __name__ == "__main__":
    unittest.main()
