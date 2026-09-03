import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import asset_manifest  # noqa: E402


class AssetManifestTests(unittest.TestCase):
    def test_normalizes_type_defaults_and_aliases(self) -> None:
        actor = asset_manifest.normalize_asset_spec({})
        self.assertEqual(actor["type"], "actor")
        self.assertEqual(actor["representation"], "directional_sprite_atlas")
        self.assertEqual(actor["capabilities"], ["animated", "agent"])

        prop = asset_manifest.normalize_asset_spec(
            {"asset_type": "prop", "capabilities": ["interactable", "interactable"]}
        )
        self.assertEqual(prop["type"], "prop_static")
        self.assertEqual(prop["capabilities"], ["interactable"])

    def test_rejects_incompatible_representation(self) -> None:
        with self.assertRaisesRegex(ValueError, "actor exige"):
            asset_manifest.normalize_asset_spec(
                {"asset_type": "actor", "representation": "tile_atlas"}
            )
        with self.assertRaisesRegex(ValueError, "actor exige"):
            asset_manifest.normalize_asset_spec(
                {"asset_type": "actor", "representation": "sprite_atlas"}
            )
        with self.assertRaisesRegex(ValueError, "capability inválida"):
            asset_manifest.normalize_asset_spec(
                {"asset_type": "actor", "capabilities": ["physics"]}
            )

    def test_manifest_keeps_artifact_hashes_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            texture = root / "spritesheet.png"
            texture.write_bytes(b"sprite-data")
            spec = asset_manifest.normalize_asset_spec({})
            manifest = asset_manifest.build_manifest(
                spec,
                asset_id="hero",
                name="Hero",
                contract={"direction_contract": {"rows": []}},
                layout={"rows": 8, "columns": 8},
                artifacts=asset_manifest.collect_artifacts(
                    root, [("spritesheet", texture)]
                ),
            )
            self.assertEqual(manifest["schema"], asset_manifest.ASSET_MANIFEST_SCHEMA)
            self.assertEqual(manifest["asset"]["type"], "actor")
            self.assertEqual(manifest["artifacts"][0]["path"], "spritesheet.png")
            self.assertEqual(
                manifest["artifacts"][0]["sha256"],
                hashlib.sha256(b"sprite-data").hexdigest(),
            )
            asset_manifest.write_manifest(root / "asset_manifest.json", manifest)
            self.assertTrue((root / "asset_manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
