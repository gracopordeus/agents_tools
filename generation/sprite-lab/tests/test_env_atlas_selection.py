import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


class EnvAtlasSelectionTests(unittest.TestCase):
    def test_resolves_asset_ids_to_local_paths(self) -> None:
        payload = {
            "render_profile": "env_atlas_v1",
            "selected_assets": [
                {"col": 0, "name": "Wall", "tile_key": "solid", "category": "wall", "asset_id": "a1"},
                {"col": 1, "name": "Floor", "tile_key": "floor", "category": "floor", "asset_id": "a2"},
            ],
        }
        with mock.patch.object(
            server.model_cache, "source_path", side_effect=["/srv/wall.fbx", "/srv/floor.fbx"]
        ):
            selection = server.build_env_atlas_selection(payload)
        self.assertEqual(selection["schema"], "sprite_lab.asset_selection/v1")
        self.assertEqual(selection["render_profile"], "env_atlas_v1")
        self.assertEqual(selection["assets"][0]["fbx_path"], "/srv/wall.fbx")
        self.assertEqual(selection["assets"][1]["col"], 1)

    def test_rejects_empty_and_oversized_selections(self) -> None:
        with self.assertRaises(ValueError):
            server.build_env_atlas_selection({})
        with self.assertRaises(ValueError):
            server.build_env_atlas_selection({"selected_assets": []})
        with self.assertRaises(ValueError):
            server.build_env_atlas_selection({"selected_assets": [{"col": i} for i in range(65)]})

    def test_rejects_unresolvable_assets(self) -> None:
        with mock.patch.object(
            server.model_cache, "source_path", side_effect=KeyError("asset não encontrado: nope")
        ):
            with self.assertRaisesRegex(ValueError, "nope"):
                server.build_env_atlas_selection({"selected_assets": [{"asset_id": "nope"}]})

    def test_rejects_missing_legacy_fbx_path(self) -> None:
        with self.assertRaises(ValueError):
            server.build_env_atlas_selection(
                {"selected_assets": [{"fbx_path": "/tmp/does-not-exist-sprite-lab.fbx"}]}
            )

    def test_accepts_existing_legacy_fbx_path(self) -> None:
        with TemporaryDirectory() as temporary:
            fbx = Path(temporary) / "wall.fbx"
            fbx.write_bytes(b"fbx")
            selection = server.build_env_atlas_selection(
                {"selected_assets": [{"name": "Wall", "fbx_path": str(fbx)}]}
            )
        self.assertEqual(selection["assets"][0]["fbx_path"], str(fbx))
        self.assertEqual(selection["assets"][0]["col"], 0)


class BodyLimitTests(unittest.TestCase):
    def _handler(self, length: int):
        return type(
            "H", (), {"headers": {"Content-Length": str(length)}, "rfile": io.BytesIO(b"{}")}
        )()

    def test_rejects_body_above_cap_without_reading(self) -> None:
        with self.assertRaises(server.BodyTooLargeError):
            server._body(self._handler(server.MAX_BODY_BYTES + 1))

    def test_accepts_normal_body(self) -> None:
        handler = type(
            "H",
            (),
            {"headers": {"Content-Length": "2"}, "rfile": io.BytesIO(b"{}")},
        )()
        self.assertEqual(server._body(handler), {})


if __name__ == "__main__":
    unittest.main()
