import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_cache


class ModelCacheConcurrencyTests(unittest.TestCase):
    def test_locks_are_stable_per_key_and_distinct_across_keys(self) -> None:
        self.assertIs(model_cache._lock_for("convert:a.glb"), model_cache._lock_for("convert:a.glb"))
        self.assertIsNot(model_cache._lock_for("convert:a.glb"), model_cache._lock_for("convert:b.glb"))
        self.assertIsNot(model_cache._lock_for("extract:x"), model_cache._lock_for("convert:x"))

    def test_global_lock_removed_in_favor_of_per_key_locks(self) -> None:
        self.assertFalse(hasattr(model_cache, "CACHE_LOCK"))

    def test_converted_path_is_deterministic(self) -> None:
        asset = {"id": "a", "sha256": "deadbeef"}
        self.assertEqual(model_cache._converted_model_path(asset), model_cache._converted_model_path(asset))

    def test_conversion_timeout_defaults_and_env_override(self) -> None:
        os.environ.pop("SPRITE_LAB_CONVERT_TIMEOUT", None)
        self.assertEqual(model_cache._conversion_timeout(), 900.0)
        os.environ["SPRITE_LAB_CONVERT_TIMEOUT"] = "120"
        try:
            self.assertEqual(model_cache._conversion_timeout(), 120.0)
        finally:
            os.environ.pop("SPRITE_LAB_CONVERT_TIMEOUT", None)
        os.environ["SPRITE_LAB_CONVERT_TIMEOUT"] = "nonsense"
        try:
            self.assertEqual(model_cache._conversion_timeout(), 900.0)
        finally:
            os.environ.pop("SPRITE_LAB_CONVERT_TIMEOUT", None)

    def test_concurrent_same_asset_serializes_without_second_blender(self) -> None:
        calls: list[str] = []
        holder = threading.Event()
        releaser = threading.Event()

        def fake_run(*args, **kwargs):
            calls.append("blender")
            holder.set()
            releaser.wait(timeout=10)
            return mock.Mock(returncode=1, stdout="", stderr="boom")

        asset = {"id": "asset_x", "sha256": "x" * 64, "relative_path": "x.fbx", "format": "fbx"}
        output = model_cache._converted_model_path(asset)
        if output.is_file():
            output.unlink()

        with mock.patch.object(model_cache, "_load_assets", return_value=({}, {"asset_x": asset})), \
            mock.patch.object(
                model_cache,
                "canonical_model_source",
                return_value={"asset": asset, "source_asset": asset, "strategy": "blender_cache"},
            ), \
            mock.patch.object(
                model_cache, "_source_root_and_path", return_value=(Path("/tmp"), Path("/tmp/x.fbx"))
            ), \
            mock.patch.object(model_cache, "subprocess") as mock_subprocess:
            mock_subprocess.run.side_effect = fake_run
            mock_subprocess.TimeoutExpired = __import__("subprocess").TimeoutExpired
            errors: list[Exception] = []

            def worker() -> None:
                try:
                    model_cache.model_path("asset_x")
                except (OSError, RuntimeError, ValueError) as exc:
                    errors.append(exc)

            first = threading.Thread(target=worker)
            first.start()
            self.assertTrue(holder.wait(timeout=10))
            second = threading.Thread(target=worker)
            second.start()
            second.join(timeout=10)
            releaser.set()
            first.join(timeout=10)
            second.join(timeout=15)

        # Only one Blender ran; the waiter failed fast instead of spawning another.
        self.assertEqual(calls, ["blender"])
        self.assertTrue(any("concorrente" in str(exc) for exc in errors))
        if output.is_file():
            output.unlink()

    def test_prewarm_skips_existing_and_counts_failures(self) -> None:
        with mock.patch.object(
            model_cache,
            "canonical_model_source",
            side_effect=[
                {"asset": {}, "source_asset": {"id": "a"}, "strategy": "existing_glb"},
                {"asset": {}, "source_asset": {"id": "b"}, "strategy": "blender_cache"},
                KeyError("missing"),
            ],
        ), mock.patch.object(
            model_cache, "_converted_model_path", return_value=Path("/tmp/never.glb")
        ), mock.patch.object(
            model_cache, "model_path", side_effect=RuntimeError("blender quebrou")
        ):
            summary = model_cache.prewarm(["a", "b", "c"])
        self.assertEqual(summary, {"converted": 0, "cached": 1, "failed": 2})


if __name__ == "__main__":
    unittest.main()
