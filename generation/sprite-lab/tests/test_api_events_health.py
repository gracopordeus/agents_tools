"""Integration: boot the real server and exercise the SaaS contract.

Covers ``GET /api/health`` (load-balancer probe), ``POST /api/events``
(navigation taxonomy), ``X-Request-Id`` propagation and the Blender-free
``GET /api/assets/{id}/viewer`` descriptor. ``/assets/{id}/model`` is
intentionally *not* hit here: it shells out to Blender.
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


class ApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = TemporaryDirectory()
        cls.port = _free_port()
        env = {
            **os.environ,
            "SPRITE_LAB_EVENTS_PATH": str(Path(cls._temporary.name) / "events.jsonl"),
            "SPRITE_LAB_NOTIFY": "0",
        }
        cls.process = subprocess.Popen(
            [sys.executable, str(SPRITE_LAB / "server.py"), "--host", "127.0.0.1", "--port", str(cls.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        cls.base = f"http://127.0.0.1:{cls.port}"
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                raise RuntimeError("server failed to boot")
            try:
                status, _, _ = _request("GET", f"{cls.base}/api/health")
            except OSError:
                time.sleep(0.5)
                continue
            if status == 200:
                return
            time.sleep(0.5)
        raise RuntimeError("server did not become healthy in time")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.process.terminate()
        try:
            cls.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.process.kill()
            cls.process.wait()
        cls._temporary.cleanup()

    def test_health_probe(self) -> None:
        status, headers, body = _request("GET", f"{self.base}/api/health")
        self.assertEqual(status, 200)
        payload = json.loads(body.decode())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["service"], "sprite-lab")
        self.assertIn("uptime_s", payload)
        self.assertIn("cache", payload)
        self.assertIn("converted_glb", payload["cache"])
        request_id = headers.get("X-Request-Id") or headers.get("X-request-id")
        self.assertTrue(request_id, "health must propagate X-Request-Id")

    def test_request_id_on_not_found(self) -> None:
        status, headers, _ = _request("GET", f"{self.base}/api/nope")
        self.assertEqual(status, 404)
        request_id = headers.get("X-Request-Id") or headers.get("X-request-id")
        self.assertTrue(request_id)

    def test_events_ingest_valid(self) -> None:
        status, _, body = _request(
            "POST", f"{self.base}/api/events", {"name": "page_view", "props": {"page": "catalog-page"}}
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(body.decode())["event"], "page_view")

    def test_events_rejects_unknown(self) -> None:
        status, _, _ = _request("POST", f"{self.base}/api/events", {"name": "nope"})
        self.assertEqual(status, 400)

    def test_events_rejects_oversized_body(self) -> None:
        status, _, _ = _request("POST", f"{self.base}/api/events", raw=b"x" * (64 * 1024 + 1))
        self.assertEqual(status, 413)

    def test_viewer_descriptor_without_blender(self) -> None:
        catalog_path = Path("/home/ggnp/tools/source-assets/catalog/assets.json")
        if not catalog_path.is_file():
            self.skipTest("no local catalog")
        assets = json.loads(catalog_path.read_text(encoding="utf-8")).get("assets", [])
        target = next(
            (
                asset
                for asset in assets
                if str(asset.get("format", "")).lower() in {"fbx", "glb", "gltf"}
            ),
            None,
        )
        if target is None:
            self.skipTest("no 3D asset in catalog")
        from urllib.parse import quote

        status, _, body = _request(
            "GET", f"{self.base}/api/assets/{quote(str(target['id']), safe='')}/viewer"
        )
        self.assertEqual(status, 200)
        payload = json.loads(body.decode())
        self.assertEqual(payload["viewer_format"], "glb")
        self.assertTrue(payload["model_url"].endswith("/model"))

    def test_env_atlas_jobs_list(self) -> None:
        status, _, body = _request("GET", f"{self.base}/api/env-atlas/jobs")
        self.assertEqual(status, 200)
        self.assertIn("jobs", json.loads(body.decode()))


if __name__ == "__main__":
    unittest.main()
