import json
import sys
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import model_cache


class ModelCacheTests(unittest.TestCase):
    def test_existing_glb_variant_is_used_without_blender_conversion(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Hero.fbx").write_bytes(b"raw-fbx")
            expected = root / "Hero.glb"
            expected.write_bytes(b"ready-glb")
            catalog_path = root / "assets.json"
            catalog_path.write_text(
                json.dumps(
                    {
                        "catalog_root": str(root),
                        "assets": [
                            {
                                "id": "hero-fbx",
                                "name": "Hero",
                                "source_id": "characters",
                                "format": "fbx",
                                "relative_path": "Hero.fbx",
                                "source_root": ".",
                                "sha256": "fbx-hash",
                            },
                            {
                                "id": "hero-glb",
                                "name": "Hero",
                                "source_id": "characters",
                                "format": "glb",
                                "relative_path": "Hero.glb",
                                "source_root": ".",
                                "sha256": "glb-hash",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(model_cache, "ASSETS_PATH", catalog_path):
                canonical = model_cache.canonical_model_source("hero-fbx")
                result = model_cache.model_path("hero-fbx")

            self.assertEqual(canonical["strategy"], "existing_glb")
            self.assertEqual(canonical["source_asset"]["id"], "hero-glb")
            self.assertEqual(result, expected)

    def test_existing_gltf_variant_is_preferred_over_raw_fbx(self) -> None:
        assets = {
            "hero-fbx": {
                "id": "hero-fbx",
                "name": "Hero",
                "source_id": "characters",
                "format": "fbx",
            },
            "hero-gltf": {
                "id": "hero-gltf",
                "name": "Hero",
                "source_id": "characters",
                "format": "gltf",
            },
        }
        with patch.object(model_cache, "_load_assets", return_value=({}, assets)):
            canonical = model_cache.canonical_model_source("hero-fbx")

        self.assertEqual(canonical["strategy"], "gltf_to_glb_cache")
        self.assertEqual(canonical["source_asset"]["id"], "hero-gltf")

    def test_viewer_descriptor_encodes_archive_paths(self) -> None:
        catalog = json.loads(model_cache.ASSETS_PATH.read_text(encoding="utf-8"))
        asset = next(row for row in catalog["assets"] if row.get("format") == "fbx")
        descriptor = model_cache.viewer_descriptor(asset["id"])

        self.assertEqual(descriptor["source_format"], "fbx")
        self.assertEqual(descriptor["viewer_format"], "glb")
        self.assertNotIn(" ", descriptor["source_url"])
        self.assertIn(unquote(asset["relative_path"]), unquote(descriptor["source_url"]))

    def test_source_path_materializes_an_archived_fbx(self) -> None:
        catalog = json.loads(model_cache.ASSETS_PATH.read_text(encoding="utf-8"))
        asset = next(row for row in catalog["assets"] if row.get("format") == "fbx")
        path = model_cache.source_path(asset["id"])

        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix.casefold(), ".fbx")


if __name__ == "__main__":
    unittest.main()
