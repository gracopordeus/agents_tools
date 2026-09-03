import hashlib
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_sources import build_catalog, init_registry, validate_catalog, write_json_atomic


def _write_registry(root: Path) -> Path:
    registry_path = root / "catalog" / "sources.json"
    registry = {
        "schema": "sprite_lab.source_registry/v1",
        "pipeline_version": "1.0.0",
        "catalog_root": str(root),
        "sources": [
            {
                "id": "test_weapons",
                "name": "Test weapons",
                "kind": "fixture",
                "license": "test-license",
                "root": "sources/weapons",
                "category_by_extension": {
                    "fbx": "weapon",
                    "glb": "weapon",
                    "gltf": "weapon",
                },
                "tags": ["fixture"],
            },
            {
                "id": "test_characters",
                "name": "Test characters",
                "kind": "fixture",
                "license": "test-license",
                "root": "sources/characters",
                "category_by_extension": {"png": "character"},
                "extensions": ["png"],
                "tags": ["fixture"],
            },
        ],
    }
    write_json_atomic(registry_path, registry)
    return registry_path


class CatalogSourcesTests(unittest.TestCase):
    def test_index_scans_direct_files_and_zip_members_without_traversal(self) -> None:
        with self.subTest("fixture"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as temporary:
                root = Path(temporary) / "assets"
                weapons = root / "sources" / "weapons"
                characters = root / "sources" / "characters"
                weapons.mkdir(parents=True)
                characters.mkdir(parents=True)
                (characters / "Male.png").write_bytes(b"character")

                archive_path = weapons / "pack.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr("FBX/Sword_Big.fbx", b"sword")
                    archive.writestr("FBX/Shield.fbx", b"shield")
                    archive.writestr("GLB/Sword.glb", b"ready-glb")
                    archive.writestr("glTF/Shield.gltf", b"ready-gltf")
                    archive.writestr("../outside.fbx", b"must not be indexed")

                registry_path = _write_registry(root)
                catalog, report = build_catalog(registry_path)

                self.assertEqual(catalog["asset_count"], 5)
                self.assertEqual(report["summary"]["assets"], 5)
                self.assertTrue(
                    all("outside" not in asset["id"] for asset in catalog["assets"])
                )
                self.assertTrue(
                    all(
                        asset["format"] in {"fbx", "glb", "gltf", "png"}
                        for asset in catalog["assets"]
                    )
                )
                self.assertEqual(
                    {asset["format"] for asset in catalog["assets"]},
                    {"fbx", "glb", "gltf", "png"},
                )

                sword = next(
                    asset for asset in catalog["assets"] if "sword_big" in asset["id"]
                )
                self.assertEqual(sword["archive"], "sources/weapons/pack.zip")
                self.assertEqual(sword["relative_path"], "FBX/Sword_Big.fbx")
                self.assertEqual(sword["category"], "weapon")
                self.assertEqual(sword["license"], "test-license")
                self.assertEqual(sword["sha256"], hashlib.sha256(b"sword").hexdigest())

                output = root / "catalog" / "assets.json"
                write_json_atomic(output, catalog)
                self.assertEqual(validate_catalog(output), [])

    def test_init_registry_is_safe_and_contains_recovered_source_contract(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary:
            registry_path = Path(temporary) / "assets" / "catalog" / "sources.json"
            registry = init_registry(registry_path)

            self.assertTrue(registry_path.is_file())
            self.assertEqual(registry["schema"], "sprite_lab.source_registry/v1")
            self.assertEqual(len(registry["sources"]), 5)
            self.assertGreaterEqual(
                {source["id"] for source in registry["sources"]},
                {
                    "quaternius_stylized_nature_megakit_standard",
                    "quaternius_universal_animation_library_2_standard",
                },
            )

            with self.assertRaises(FileExistsError):
                init_registry(registry_path)

            persisted = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["catalog_root"], str(registry_path.parents[1]))

    def test_auto_discover_indexes_new_top_level_zip(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "assets"
            root.mkdir(parents=True)
            registry_path = root / "catalog" / "sources.json"
            write_json_atomic(
                registry_path,
                {
                    "schema": "sprite_lab.source_registry/v1",
                    "catalog_root": str(root),
                    "auto_discover": True,
                    "sources": [],
                },
            )
            with zipfile.ZipFile(root / "New Pack.zip", "w") as archive:
                archive.writestr("FBX/Sword_Big.fbx", b"sword")

            catalog, report = build_catalog(registry_path)
            self.assertEqual(report["summary"]["assets"], 1)
            self.assertEqual(catalog["assets"][0]["source_id"], "incoming__new_pack")

    def test_auto_discover_exclude_and_exact_content_deduplication(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "assets"
            sources = root / "sources"
            sources.mkdir(parents=True)
            (sources / "one.fbx").write_bytes(b"same-content")
            (sources / "two.fbx").write_bytes(b"same-content")
            (root / "Ignored Pack").mkdir()
            (root / "Ignored Pack" / "ignored.fbx").write_bytes(b"ignored")
            registry_path = root / "catalog" / "sources.json"
            write_json_atomic(
                registry_path,
                {
                    "schema": "sprite_lab.source_registry/v1",
                    "catalog_root": str(root),
                    "auto_discover": True,
                    "auto_discover_exclude": ["Ignored Pack"],
                    "deduplicate_exact_content": True,
                    "sources": [
                        {
                            "id": "test_sources",
                            "name": "Test sources",
                            "kind": "fixture",
                            "license": "test-license",
                            "root": "sources",
                            "category_by_extension": {"fbx": "model"},
                        }
                    ],
                },
            )

            catalog, report = build_catalog(registry_path)

            self.assertEqual(report["summary"]["assets_before_deduplication"], 2)
            self.assertEqual(report["summary"]["deduplicated_exact_content"], 1)
            self.assertEqual(catalog["asset_count"], 1)
            self.assertEqual(len(catalog["assets"][0]["aliases"]), 1)
            self.assertNotIn("ignored", json.dumps(catalog))
