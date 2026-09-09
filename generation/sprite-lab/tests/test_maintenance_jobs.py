import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


class MaintenanceWorkersTests(unittest.TestCase):
    def test_reindex_worker_returns_counts(self) -> None:
        manifest = {"asset_count": 10, "animation_count": 4, "generated_at": "t"}
        with mock.patch.object(server.rel, "build_relationship_catalog", return_value=manifest):
            result = server._maintenance_reindex_worker()
        self.assertEqual(result, {"ok": True, "asset_count": 10, "animation_count": 4})

    def test_save_worker_persists_export_on_success(self) -> None:
        relationship = {"id": "r1"}
        export = {"url": "/composition-exports/r1.glb"}
        manifest = {"generated_at": "t"}
        with mock.patch.object(server.rel, "add_relationship", return_value=relationship), \
            mock.patch.object(server.composition_export, "export_relationship", return_value=export), \
            mock.patch.object(server.rel, "build_relationship_catalog", return_value=manifest):
            result = server._relationships_save_worker({"character_asset_id": "c"})
        self.assertEqual(result["relationship"], {**relationship, "export": export})
        self.assertEqual(result["export"], export)
        self.assertEqual(result["catalog_generated_at"], "t")

    def test_save_worker_keeps_relationship_when_export_fails(self) -> None:
        relationship = {"id": "r1"}
        manifest = {"generated_at": "t"}
        with mock.patch.object(server.rel, "add_relationship", return_value=relationship), \
            mock.patch.object(
                server.composition_export, "export_relationship", side_effect=RuntimeError("blender down")
            ), \
            mock.patch.object(server.rel, "build_relationship_catalog", return_value=manifest):
            result = server._relationships_save_worker({"character_asset_id": "c"})
        self.assertEqual(result["relationship"], relationship)
        self.assertIsNone(result["export"])
        self.assertIn("blender down", result["export_error"])

    def test_delete_worker_returns_relationship(self) -> None:
        relationship = {"id": "r9"}
        manifest = {"generated_at": "t"}
        with mock.patch.object(server.rel, "delete_relationship", return_value=relationship), \
            mock.patch.object(server.rel, "build_relationship_catalog", return_value=manifest):
            result = server._relationships_delete_worker("r9")
        self.assertEqual(result["relationship"], relationship)

    def test_wants_sync_opt_in(self) -> None:
        self.assertTrue(server._wants_sync({"mode": "sync"}))
        self.assertTrue(server._wants_sync({"mode": "SYNC"}))
        self.assertFalse(server._wants_sync({}))
        self.assertFalse(server._wants_sync({"mode": "async"}))
        self.assertFalse(server._wants_sync("not-a-dict"))


if __name__ == "__main__":
    unittest.main()
