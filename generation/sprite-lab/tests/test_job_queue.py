import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import job_queue


class JobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        job_queue.reset_executor()
        self._temporary = TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.path = Path(self._temporary.name) / "jobs.json"

    def tearDown(self) -> None:
        job_queue.reset_executor()

    def test_enqueue_list_get_roundtrip(self) -> None:
        job = job_queue.enqueue("reindex", {}, self.path)
        self.assertTrue(job["id"].startswith("job_"))
        self.assertEqual(job["status"], "queued")
        self.assertEqual(len(job_queue.list_jobs(self.path)), 1)
        self.assertEqual(job_queue.get_job(job["id"], self.path)["kind"], "reindex")
        self.assertIsNone(job_queue.get_job("missing", self.path))

    def test_submit_runs_worker_to_done(self) -> None:
        deadline = time.monotonic() + 10
        job = job_queue.submit("reindex", {}, lambda: {"asset_count": 3}, self.path)
        while time.monotonic() < deadline:
            current = job_queue.get_job(job["id"], self.path)
            if current["status"] == "done":
                break
            time.sleep(0.05)
        current = job_queue.get_job(job["id"], self.path)
        self.assertEqual(current["status"], "done")
        self.assertEqual(current["result"], {"asset_count": 3})
        self.assertIn("started_at", current)
        self.assertIn("finished_at", current)

    def test_worker_exception_becomes_error_result(self) -> None:
        def boom():
            raise ValueError("quebrou")

        deadline = time.monotonic() + 10
        job = job_queue.submit("reindex", {}, boom, self.path)
        while time.monotonic() < deadline:
            current = job_queue.get_job(job["id"], self.path)
            if current["status"] == "error":
                break
            time.sleep(0.05)
        current = job_queue.get_job(job["id"], self.path)
        self.assertEqual(current["status"], "error")
        self.assertEqual(current["error"], "quebrou")

    def test_executor_is_bounded_and_shared(self) -> None:
        entered: list[str] = []
        release = threading.Event()

        def slow(name: str):
            entered.append(name)
            release.wait(timeout=10)
            return {"name": name}

        first = job_queue.submit("a", {}, lambda: slow("a"), self.path)
        second = job_queue.submit("b", {}, lambda: slow("b"), self.path)
        deadline = time.monotonic() + 10
        while len(entered) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        release.set()
        for job_id in (first["id"], second["id"]):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if job_queue.get_job(job_id, self.path)["status"] == "done":
                    break
                time.sleep(0.05)
        self.assertEqual(
            {job_queue.get_job(first["id"], self.path)["result"]["name"], job_queue.get_job(second["id"], self.path)["result"]["name"]},
            {"a", "b"},
        )

    def test_max_workers_env_override(self) -> None:
        import os

        os.environ["SPRITE_LAB_JOB_WORKERS"] = "7"
        try:
            self.assertEqual(job_queue.max_workers(), 7)
        finally:
            os.environ.pop("SPRITE_LAB_JOB_WORKERS", None)
        self.assertEqual(job_queue.max_workers(), 4)


if __name__ == "__main__":
    unittest.main()
