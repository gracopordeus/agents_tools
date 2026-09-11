import copy
import json
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

import ai_render_spec  # noqa: E402
import server  # noqa: E402


class LayeredServerWeaponTests(unittest.TestCase):
    def _png_bundle(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        for name in (
            "character_holdout_spritesheet.png",
            "weapon_spritesheet.png",
            "holdout_cut_mask.png",
            "composite_preview.png",
        ):
            Image.new("RGBA", (16, 16), (20, 40, 60, 255)).save(root / name)

    def test_published_bundle_has_api_manifest_and_download_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work" / "job-7"
            self._png_bundle(work)
            published = root / "published" / "layered"
            with mock.patch.object(server, "LAYERED_PUBLISHED_ROOT", published):
                publication = server.publish_layered_job(
                    "job-7", work, source={"job_id": "job-7"}
                )
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{httpd.server_port}"
                try:
                    for name in (
                        "character_holdout_spritesheet.png",
                        "weapon_spritesheet.png",
                        "holdout_cut_mask.png",
                        "composite_preview.png",
                    ):
                        with self.subTest(artifact=name), urllib.request.urlopen(
                            f"{base}/layered-outputs/job-7/{name}", timeout=10
                        ) as response:
                            self.assertEqual(response.status, 200)
                            self.assertEqual(response.read(), (work / name).read_bytes())

                    with urllib.request.urlopen(
                        f"{base}/api/layered-bundles/job-7", timeout=10
                    ) as response:
                        self.assertEqual(response.status, 200)
                        descriptor = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(descriptor["job_id"], "job-7")
                    self.assertEqual(descriptor["manifest"], publication["manifest"])
                    self.assertEqual(
                        descriptor["artifact_hashes"]["manifest"]["path"],
                        "layered_sprite_bundle.json",
                    )
                    self.assertTrue(
                        descriptor["outputs"]["weapon"].endswith(
                            "/layered-outputs/job-7/weapon_spritesheet.png"
                        )
                    )
                    with urllib.request.urlopen(
                        f"{base}/layered-outputs/job-7/layered_artifact_hashes.json",
                        timeout=10,
                    ) as response:
                        self.assertEqual(response.status, 200)
                        registry = json.loads(response.read().decode("utf-8"))
                    self.assertEqual(
                        registry["manifest"]["sha256"],
                        publication["artifact_hashes"]["manifest"]["sha256"],
                    )

                    for traversal in (
                        "%2e%2e/%2e%2e/server.py",
                        "%5c..%5c..%5cserver.py",
                        "%5cserver.py",
                    ):
                        with self.subTest(traversal=traversal), self.assertRaises(
                            urllib.error.HTTPError
                        ) as context:
                            urllib.request.urlopen(
                                f"{base}/layered-outputs/job-7/{traversal}",
                                timeout=10,
                            )
                        self.assertIn(context.exception.code, (400, 404))
                        context.exception.read()
                        context.exception.close()
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=10)

    def test_published_endpoint_hides_staging_during_atomic_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work" / "job-7"
            self._png_bundle(work)
            published = root / "published" / "layered"
            started = threading.Event()
            release = threading.Event()
            result: dict[str, object] = {}
            errors: list[BaseException] = []
            original_copy2 = server.layered_bundle.shutil.copy2

            def blocking_copy(source, destination, *args, **kwargs):
                copied = original_copy2(source, destination, *args, **kwargs)
                if Path(destination).name == "character_holdout_spritesheet.png":
                    started.set()
                    if not release.wait(10):
                        raise TimeoutError("copy probe was not released")
                return copied

            with mock.patch.object(server, "LAYERED_PUBLISHED_ROOT", published), \
                    mock.patch.object(server.layered_bundle.shutil, "copy2", side_effect=blocking_copy):
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                # Capture a worker exception without leaving the copy blocked.
                def publish_with_error_capture() -> None:
                    try:
                        result["publication"] = server.publish_layered_job(
                            "job-7", work, source={"job_id": "job-7"}
                        )
                    except BaseException as exc:  # pragma: no cover - probe guard
                        errors.append(exc)

                publisher = threading.Thread(target=publish_with_error_capture, daemon=True)
                publisher.start()
                base = f"http://127.0.0.1:{httpd.server_port}"
                try:
                    started_ok = started.wait(10)
                    self.assertTrue(started_ok, "publisher did not reach copy probe")
                    staging = next(published.glob(".job-7.staging-*"))
                    staging_url = (
                        f"{base}/layered-outputs/{staging.name}/"
                        "character_holdout_spritesheet.png"
                    )
                    with self.assertRaises(urllib.error.HTTPError) as context:
                        urllib.request.urlopen(staging_url, timeout=10)
                    self.assertIn(context.exception.code, (400, 404))
                    context.exception.read()
                    context.exception.close()
                finally:
                    release.set()
                    publisher.join(timeout=10)
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=10)
                self.assertFalse(errors)
                self.assertIn("publication", result)

    def test_missing_or_divergent_registry_blocks_publication_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "work" / "job-7"
            self._png_bundle(work)
            published = root / "published" / "layered"
            with mock.patch.object(server, "LAYERED_PUBLISHED_ROOT", published):
                server.publish_layered_job("job-7", work, source={"job_id": "job-7"})
                registry_path = published / "job-7" / server.layered_bundle.ARTIFACT_HASH_REGISTRY_FILENAME
                registry = json.loads(registry_path.read_text(encoding="utf-8"))
                registry["artifacts"]["weapon_output"]["sha256"] = "0" * 64
                registry_path.write_text(json.dumps(registry), encoding="utf-8")
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{httpd.server_port}"
                try:
                    for endpoint in (
                        "/api/layered-bundles/job-7",
                        "/layered-outputs/job-7/weapon_spritesheet.png",
                    ):
                        with self.subTest(endpoint=endpoint), self.assertRaises(
                            urllib.error.HTTPError
                        ) as context:
                            urllib.request.urlopen(base + endpoint, timeout=10)
                        self.assertEqual(context.exception.code, 400)
                        context.exception.read()
                        context.exception.close()
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join(timeout=10)
                self.assertTrue((published / "job-7" / "layered_sprite_bundle.json").is_file())

    def test_run_gemini_job_layered_reaches_done_with_two_pass_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            for name in ("spritesheet_beauty.png", "spritesheet_bones.png", "spritesheet_lineart.png"):
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(source / name)
            identity = root / "identity.png"
            weapon = root / "weapon.png"
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(identity)
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(weapon)
            output = root / "work" / "ai_render_probe"
            output.mkdir(parents=True)
            spec = ai_render_spec.default_render_spec(name="probe")
            spec.update({
                "generation_mode": "character_weapon_holdout",
                "output": {**spec["output"], "width": 1024, "height": 1024},
                "layer_contract": {
                    "weapon_component_id": "weapon_1",
                    "generation_order": ["character", "weapon"],
                    "composition_order": ["weapon", "character_holdout"],
                    "layers": [
                        {"id": "weapon", "z": 0},
                        {"id": "character_holdout", "z": 1},
                    ],
                },
                "source_contract": {
                    "components": [{"id": "weapon_1", "role": "weapon"}],
                },
            })
            job = {
                "id": "ai_render_probe",
                "payload": {
                    "source_id": "source",
                    "render_name": "probe",
                    "prompt": "",
                    "additional_instructions": "",
                    "render_spec": spec,
                    "provider": "dry-run",
                    "model": "dry-model",
                    "reference_name": "identity",
                    "reference_id": "identity-ref",
                    "weapon_reference_id": "weapon-ref",
                    "reference_channels": ["beauty"],
                    "blender_channels": ["beauty"],
                    "identity_lineart_mode": "lineart_standard",
                    "frame_control": False,
                    "qwen_seed": None,
                    "gemini_temperature": None,
                    "gemini_top_k": None,
                    "publish_layered_bundle": True,
                },
            }
            updates = []
            layered_calls = []

            def fake_lineart(_source, destination, mode):
                Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(destination)
                return {"path": str(destination), "mode": mode}

            with mock.patch.object(server, "GEMINI_WORK", root / "work"), \
                    mock.patch.object(server, "_gemini_source_directory", return_value=source), \
                    mock.patch.object(server, "gemini_reference_path", return_value=identity), \
                    mock.patch.object(server, "_prepare_identity_lineart", side_effect=fake_lineart), \
                    mock.patch.object(server, "weapon_reference_path", return_value=weapon), \
                    mock.patch.object(server, "get_weapon_reference", return_value={"name": "sword"}), \
                    mock.patch.object(server, "update_pipeline_progress", return_value=None), \
                    mock.patch.object(server, "with_ai_render_source_contract", side_effect=lambda value, _source: copy.deepcopy(spec)), \
                    mock.patch.object(
                        server,
                        "run_layered_postprocess_compose_publish",
                        side_effect=lambda *args, **kwargs: layered_calls.append((args, kwargs))
                        or {
                            "holdout_stage": "after_final_resolution",
                            "postprocess": {},
                            "composition": {},
                            "publication": {},
                        },
                    ), \
                    mock.patch.object(server, "update_gemini_job", side_effect=lambda _job_id, patch: updates.append(copy.deepcopy(patch)) or patch):
                server.run_gemini_job(job)

            self.assertEqual(len(layered_calls), 1)
            self.assertEqual(layered_calls[0][0][0], "ai_render_probe")
            self.assertEqual(
                set(layered_calls[0][0][1]),
                {"character", "weapon"},
            )
            self.assertEqual(layered_calls[0][1]["source"]["provider"], "dry-run")
            self.assertEqual(updates[-1]["status"], "done")
            self.assertNotIn("completed", [patch.get("status") for patch in updates])
            self.assertEqual(updates[-1]["outputs"]["image"], "ai_render_probe/weapon_full.png")
            self.assertEqual(updates[-1]["outputs"]["validation"], "ai_render_probe/weapon_validation.png")
            self.assertTrue((output / "character.request.json").is_file())
            self.assertTrue((output / "weapon.request.json").is_file())
            self.assertEqual(
                json.loads((output / "character.request.json").read_text())["generation_role"],
                "character",
            )
            self.assertEqual(
                json.loads((output / "weapon.request.json").read_text())["generation_role"],
                "weapon",
            )
            self.assertTrue((output / "character_full.png").is_file())
            self.assertTrue((output / "weapon_full.png").is_file())


if __name__ == "__main__":
    unittest.main()
