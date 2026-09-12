import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import observability as obs


class ObservabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        obs.reset_state()

    def test_request_ids_are_unique(self) -> None:
        ids = {obs.new_request_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        self.assertTrue(all(len(item) == 16 for item in ids))

    def test_frontend_taxonomy_covers_navigation(self) -> None:
        for name in ("page_view", "viewer_model_error", "catalog_upload_done"):
            self.assertIn(name, obs.FRONTEND_EVENTS)

    def test_validate_accepts_known_event(self) -> None:
        name, props = obs.validate_frontend_event({"name": "page_view", "props": {"page": "catalog-page"}})
        self.assertEqual(name, "page_view")
        self.assertEqual(props, {"page": "catalog-page"})

    def test_validate_rejects_unknown_and_bad_shapes(self) -> None:
        with self.assertRaises(ValueError):
            obs.validate_frontend_event({"name": "nope", "props": {}})
        with self.assertRaises(ValueError):
            obs.validate_frontend_event({"name": "page_view", "props": "x"})
        with self.assertRaises(ValueError):
            obs.validate_frontend_event({"name": "x" * 65})
        with self.assertRaises(ValueError):
            obs.validate_frontend_event({"name": "page_view", "props": {"big": "y" * 9000}})

    def test_ingest_persists_jsonl_and_counts(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "events.jsonl"
            original = os.environ.get("SPRITE_LAB_EVENTS_PATH")
            os.environ["SPRITE_LAB_EVENTS_PATH"] = str(target)
            try:
                entry = obs.ingest_frontend_event("page_view", {"page": "catalog-page"}, request_id="abc")
            finally:
                if original is None:
                    os.environ.pop("SPRITE_LAB_EVENTS_PATH", None)
                else:
                    os.environ["SPRITE_LAB_EVENTS_PATH"] = original
            self.assertEqual(entry["event"], "page_view")
            rows = target.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(rows), 1)
            stored = json.loads(rows[0])
            self.assertEqual(stored["props"], {"page": "catalog-page"})
            self.assertEqual(obs.event_counts().get("frontend_event"), 1)

    def test_ring_buffer_caps_memory(self) -> None:
        for index in range(obs._RING_CAPACITY + 50):
            obs.log_event("probe", index=index)
        self.assertLessEqual(len(obs.recent_events(limit=10_000)), obs._RING_CAPACITY)
        self.assertEqual(obs.event_counts().get("probe"), obs._RING_CAPACITY + 50)

    def test_health_snapshot_shape(self) -> None:
        snapshot = obs.health_snapshot({"cache": {"converted_glb": 1}})
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["service"], "sprite-lab")
        self.assertIn("uptime_s", snapshot)
        self.assertEqual(snapshot["cache"], {"converted_glb": 1})

    def test_rate_limit_allows_burst_then_refuses(self) -> None:
        obs.reset_rate_limits()
        original = os.environ.get("SPRITE_LAB_EVENTS_BURST")
        os.environ["SPRITE_LAB_EVENTS_BURST"] = "3"
        try:
            self.assertTrue(obs.check_events_rate_limit("1.2.3.4", now=1000.0))
            self.assertTrue(obs.check_events_rate_limit("1.2.3.4", now=1001.0))
            self.assertTrue(obs.check_events_rate_limit("1.2.3.4", now=1002.0))
            self.assertFalse(obs.check_events_rate_limit("1.2.3.4", now=1003.0))
            # Other IPs are unaffected.
            self.assertTrue(obs.check_events_rate_limit("5.6.7.8", now=1003.0))
            # Window slides: after 60s the budget renews.
            self.assertTrue(obs.check_events_rate_limit("1.2.3.4", now=1063.1))
        finally:
            if original is None:
                os.environ.pop("SPRITE_LAB_EVENTS_BURST", None)
            else:
                os.environ["SPRITE_LAB_EVENTS_BURST"] = original
            obs.reset_rate_limits()

    def test_rate_limit_defaults_to_thirty(self) -> None:
        os.environ.pop("SPRITE_LAB_EVENTS_BURST", None)
        self.assertEqual(obs.events_burst_limit(), 30)


if __name__ == "__main__":
    unittest.main()
