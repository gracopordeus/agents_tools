import json
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_cache


def _make_kit(root: Path) -> Path:
    archive = root / "kit.zip"
    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"uri": "Model.bin", "byteLength": 3}],
        "images": [{"uri": "Tex_BaseColor.png"}, {"uri": "data:,ignored"}],
    }
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("Exports/Model.gltf", json.dumps(gltf))
        handle.writestr("Exports/Model.bin", b"bin")
        handle.writestr("Exports/Tex_BaseColor.png", b"png")
        handle.writestr("Exports/Other_Huge.fbx", b"f" * 1024)
        handle.writestr("Textures/Shared.png", b"s")
    return archive


class SelectiveExtractionTests(unittest.TestCase):
    def _catalog(self, root: Path, archive: Path) -> dict:
        return {"catalog_root": str(root), "archive": archive.name}

    def test_gltf_extracts_only_companions(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = _make_kit(root)
            cache = root / "cache"
            asset = {"relative_path": "Exports/Model.gltf", "archive": archive.name}
            catalog = {"catalog_root": str(root)}
            with mock.patch.object(model_cache, "SOURCE_CACHE_PATH", cache):
                source_root, path = model_cache._source_root_and_path(asset, catalog, selective=True)
            self.assertTrue(path.is_file())
            extracted = sorted(
                item.relative_to(source_root).as_posix()
                for item in source_root.rglob("*")
                if item.is_file() and not item.name.startswith(".partial")
            )
            self.assertIn("Exports/Model.gltf", extracted)
            self.assertIn("Exports/Model.bin", extracted)
            self.assertIn("Exports/Tex_BaseColor.png", extracted)
            self.assertNotIn("Exports/Other_Huge.fbx", extracted)
            self.assertNotIn("Textures/Shared.png", extracted)

    def test_wanted_outside_subset_still_resolves_after_full_extract(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = _make_kit(root)
            cache = root / "cache"
            catalog = {"catalog_root": str(root)}
            gltf_asset = {"relative_path": "Exports/Model.gltf", "archive": archive.name}
            fbx_asset = {"relative_path": "Exports/Other_Huge.fbx", "archive": archive.name}
            with mock.patch.object(model_cache, "SOURCE_CACHE_PATH", cache):
                model_cache._source_root_and_path(gltf_asset, catalog, selective=True)
                # FBX has no declared companions: falls back to full extraction,
                # reusing the files already on disk.
                source_root, path = model_cache._source_root_and_path(fbx_asset, catalog, selective=True)
            self.assertTrue(path.is_file())
            self.assertTrue((source_root / ".complete").is_file())

    def test_glb_needs_only_its_member(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "kit.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("model.glb", b"glb-bytes")
                handle.writestr("unrelated.bin", b"u" * 512)
            cache = root / "cache"
            asset = {"relative_path": "model.glb", "archive": archive.name}
            catalog = {"catalog_root": str(root)}
            with mock.patch.object(model_cache, "SOURCE_CACHE_PATH", cache):
                source_root, path = model_cache._source_root_and_path(asset, catalog, selective=True)
            self.assertEqual(path.read_bytes(), b"glb-bytes")
            self.assertFalse((source_root / "unrelated.bin").exists())

    def test_unsafe_member_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = _make_kit(root)
            cache = root / "cache"
            asset = {"relative_path": "../evil.fbx", "archive": archive.name}
            catalog = {"catalog_root": str(root)}
            with mock.patch.object(model_cache, "SOURCE_CACHE_PATH", cache):
                with self.assertRaises(ValueError):
                    model_cache._source_root_and_path(asset, catalog, selective=True)


if __name__ == "__main__":
    unittest.main()
