"""Integration: async maintenance queue (202 + poll) and events rate-limit.

- ``POST /api/reindex`` answers 202 and the job reaches ``done`` via
  ``GET /api/maintenance/jobs/{id}`` (real catalog rebuild, no Blender).
- A server booted with ``SPRITE_LAB_EVENTS_BURST=3`` answers 429 after the
  burst, proving the SaaS abuse gate.
"""
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

SPRITE_LAB = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(method: str, url: str, payload: dict | None = None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _boot_server(extra_env: dict) -> tuple[subprocess.Popen, str, TemporaryDirectory]:
    temporary = TemporaryDirectory()
    port = _free_port()
    env = {
        **os.environ,
        "SPRITE_LAB_EVENTS_PATH": str(Path(temporary.name) / "events.jsonl"),
        "SPRITE_LAB_MAINTENANCE_JOBS_PATH": str(Path(temporary.name) / "maintenance.json"),
        "SPRITE_LAB_NOTIFY": "0",
        **extra_env,
    }
    process = subprocess.Popen(
        [sys.executable, str(SPRITE_LAB / "server.py"), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            temporary.cleanup()
            raise RuntimeError("server failed to boot")
        try:
            status, _, _ = _request("GET", f"{base}/api/health")
        except OSError:
            time.sleep(0.5)
            continue
        if status == 200:
            return process, base, temporary
        time.sleep(0.5)
    process.terminate()
    temporary.cleanup()
    raise RuntimeError("server did not become healthy in time")


class MaintenanceQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.process, cls.base, cls._temporary = _boot_server({})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.process.terminate()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait()
        cls._temporary.cleanup()

    def test_reindex_async_poll_to_done(self) -> None:
        status, _, body = _request("POST", f"{self.base}/api/reindex", {})
        self.assertEqual(status, 202)
        job_id = json.loads(body.decode())["id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status, _, body = _request("GET", f"{self.base}/api/maintenance/jobs/{job_id}")
            self.assertEqual(status, 200)
            job = json.loads(body.decode())
            if job["status"] == "done":
                break
            if job["status"] == "error":
                self.fail(f"reindex job failed: {job.get('error')}")
            time.sleep(0.5)
        self.assertEqual(job["status"], "done")
        self.assertTrue(job["result"]["ok"])
        self.assertGreaterEqual(job["result"]["asset_count"], 1)

    def test_reindex_sync_opt_in_keeps_old_shape(self) -> None:
        status, _, body = _request("POST", f"{self.base}/api/reindex", {"mode": "sync"})
        self.assertEqual(status, 200)
        payload = json.loads(body.decode())
        self.assertTrue(payload["ok"])

    def test_maintenance_jobs_list_and_missing(self) -> None:
        status, _, body = _request("GET", f"{self.base}/api/maintenance/jobs")
        self.assertEqual(status, 200)
        self.assertIn("jobs", json.loads(body.decode()))
        status, _, _ = _request("GET", f"{self.base}/api/maintenance/jobs/does-not-exist")
        self.assertEqual(status, 404)


class EventsRateLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.process, cls.base, cls._temporary = _boot_server({"SPRITE_LAB_EVENTS_BURST": "3"})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.process.terminate()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait()
        cls._temporary.cleanup()

    def test_burst_then_429_with_retry_after(self) -> None:
        payload = {"name": "page_view", "props": {"page": "catalog-page"}}
        statuses = [
            _request("POST", f"{self.base}/api/events", payload)[0] for _ in range(5)
        ]
        self.assertEqual(statuses[:3], [202, 202, 202])
        self.assertEqual(statuses[3:], [429, 429])
        status, headers, _ = _request("POST", f"{self.base}/api/events", payload)
        self.assertEqual(status, 429)
        retry = headers.get("Retry-After") or headers.get("retry-after")
        self.assertEqual(str(retry), "60")


if __name__ == "__main__":
    unittest.main()
