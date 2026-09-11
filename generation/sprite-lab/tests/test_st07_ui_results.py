import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from PIL import Image

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import server  # noqa: E402


class St07UiResultsTests(unittest.TestCase):
    def _png_bundle(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        for name in (
            "character_holdout_spritesheet.png",
            "weapon_spritesheet.png",
            "holdout_cut_mask.png",
            "composite_preview.png",
        ):
            Image.new("RGBA", (16, 16), (20, 40, 60, 255)).save(root / name)

    def _serve(self, published: Path):
        patcher = mock.patch.object(server, "LAYERED_PUBLISHED_ROOT", published)
        patcher.start()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return patcher, httpd, thread, f"http://127.0.0.1:{httpd.server_port}"

    def test_results_api_lists_only_promoted_bundle_and_all_downloads_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work" / "job-7"
            self._png_bundle(work)
            published = root / "published" / "layered"
            patcher, httpd, thread, base = self._serve(published)
            try:
                publication = server.publish_layered_job(
                    "job-7", work, source={"job_id": "job-7"}
                )
                # A work tree and a staging-looking directory must never become
                # discoverable entries or appear as URLs in the manager.
                (published / "work-copy").mkdir(parents=True)
                (published / ".job-7.staging-probe").mkdir(parents=True)
                with urllib.request.urlopen(f"{base}/api/layered-bundles", timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual([row["job_id"] for row in payload["bundles"]], ["job-7"])
                descriptor = payload["bundles"][0]
                self.assertEqual(
                    {row["key"] for row in descriptor["artifacts"]},
                    {"character_holdout", "weapon", "holdout_source", "preview", "manifest", "hashes"},
                )
                self.assertNotIn("/work/", json.dumps(descriptor))
                self.assertNotIn("staging", json.dumps(descriptor))
                self.assertEqual(descriptor["manifest"], publication["manifest"])

                expected = {
                    "character_holdout": ("png", "character_holdout_spritesheet.png"),
                    "weapon": ("png", "weapon_spritesheet.png"),
                    "holdout_source": ("png", "holdout_cut_mask.png"),
                    "preview": ("png", "composite_preview.png"),
                    "manifest": ("json", "layered_sprite_bundle.json"),
                    "hashes": ("json", "layered_artifact_hashes.json"),
                }
                for key, (suffix, source_name) in expected.items():
                    with self.subTest(artifact=key), urllib.request.urlopen(
                        f"{base}/api/layered-bundles/job-7/download/{key}", timeout=10
                    ) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(
                            response.headers["Content-Disposition"],
                            f'attachment; filename="sprite_lab_job-7_{key}.{suffix}"',
                        )
                        content = response.read()
                    if suffix == "png":
                        self.assertEqual(content, (work / source_name).read_bytes())
                    elif key == "manifest":
                        self.assertEqual(
                            json.loads(content.decode("utf-8"))["source"]["job_id"],
                            "job-7",
                        )
                    else:
                        self.assertEqual(
                            json.loads(content.decode("utf-8"))["schema"],
                            "sprite_lab.layered_artifact_hashes/v1",
                        )
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=10)
                patcher.stop()

    def test_results_api_rejects_unknown_artifacts_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work" / "job-7"
            self._png_bundle(work)
            published = root / "published" / "layered"
            patcher, httpd, thread, base = self._serve(published)
            try:
                server.publish_layered_job("job-7", work, source={"job_id": "job-7"})
                for endpoint in (
                    "/api/layered-bundles/job-7/download/unknown",
                    "/api/layered-bundles/%2e%2e/download/weapon",
                    "/api/layered-bundles/job-7/download/%2e%2e",
                    "/layered-outputs/job-7/%2e%2e/server.py",
                    "/layered-outputs/job-7/%5c..%5cserver.py",
                ):
                    with self.subTest(endpoint=endpoint), self.assertRaises(urllib.error.HTTPError) as context:
                        urllib.request.urlopen(base + endpoint, timeout=10)
                    self.assertIn(context.exception.code, (400, 404))
                    context.exception.read()
                    context.exception.close()
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=10)
                patcher.stop()

    def test_results_dom_contract_and_behavior_harness(self) -> None:
        app = (SPRITE_LAB / "web" / "app.js").read_text(encoding="utf-8")
        html = (SPRITE_LAB / "web" / "index.html").read_text(encoding="utf-8")
        css = (SPRITE_LAB / "web" / "style.css").read_text(encoding="utf-8")
        self.assertIn('data-page="results-page"', html)
        self.assertIn('id="results-job-list"', html)
        self.assertIn('id="results-empty"', html)
        self.assertIn('id="results-detail"', html)
        self.assertIn('id="refresh-results"', html)
        self.assertIn('"results-page": "/results"', app)
        self.assertIn('api("/api/layered-bundles")', app)
        self.assertIn('const PUBLISHED_LAYERED_ARTIFACT_KEYS', app)
        self.assertIn('function isPublishedLayeredBundle(bundle)', app)
        self.assertIn('function safePublishedLayeredUrl(bundle, artifact)', app)
        self.assertIn('if (!isLayeredGeminiJob(job)) return "";', app)
        self.assertIn('if (!isLayeredGeminiJob(job)) return [];', app)
        self.assertIn('class="results-layout"', html)
        self.assertIn('grid-template-areas: "history detail"', css)
        self.assertIn('grid-template-areas: "history" "detail"', css)
        self.assertIn('.published-artifact-grid', css)
        harness = SPRITE_LAB / "tests" / "fixtures" / "st07_results_behavior_harness.js"
        result = subprocess.run(
            ["node", str(harness)],
            cwd=SPRITE_LAB,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("ST07_RESULTS_BEHAVIOR_OK", result.stdout)

    def test_results_route_serves_spa_shell(self) -> None:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{httpd.server_port}/results", timeout=10
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertIn('id="results-page"', response.read().decode("utf-8"))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
