import json
import sys
import tempfile
import unittest
from pathlib import Path


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import relationship_catalog as catalog  # noqa: E402


class RelationshipCatalogTests(unittest.TestCase):
    def test_ual2_mannequin_is_a_mesh_not_an_action(self) -> None:
        mannequin = {
            "category": "animation",
            "format": "fbx",
            "name": "Mannequin_F",
            "relative_path": "Universal Animation Library 2[Standard]/Female Mannequin/Unity/Mannequin_F.fbx",
        }
        action = {
            "category": "animation",
            "format": "fbx",
            "name": "UAL2_Standard",
            "relative_path": "Universal Animation Library 2[Standard]/Unity/UAL2_Standard.fbx",
            "source_id": "quaternius_universal_animation_library_2_standard",
        }
        self.assertEqual(catalog.asset_kind(mannequin), "character")
        self.assertEqual(catalog.asset_kind(action), "character")

        unrelated_action = {**action, "name": "UAL2_Action", "relative_path": "UAL2_Action.fbx"}
        self.assertEqual(catalog.asset_kind(unrelated_action), "animation")

    def test_index_annotation_relationship_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets_path = root / "assets.json"
            animations_path = root / "animations.json"
            output_path = root / "relationships.json"
            annotations_path = root / "semantic_annotations.json"
            assets_path.write_text(
                json.dumps(
                    {
                        "catalog_root": str(root),
                        "assets": [
                            {"id": "hero", "name": "Hero", "category": "model", "format": "fbx"},
                            {"id": "claymore", "name": "Claymore", "category": "weapon", "format": "fbx"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            animations_path.write_text(
                json.dumps(
                    {
                        "animations": [
                            {
                                "id": "hero_attack",
                                "asset_id": "hero",
                                "clip_name": "Sword_Regular_A",
                                "action_name": "Sword_Regular_A",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            indexed = catalog.build_relationship_catalog(
                assets_path, animations_path, output_path, annotations_path
            )
            self.assertEqual(indexed["asset_count"], 2)
            self.assertEqual(indexed["relationship_count"], 0)

            annotation = catalog.update_annotation(
                "claymore",
                {"semantic_name": "Greatsword", "tags": ["melee", "heavy"]},
                assets_path,
                annotations_path,
            )
            self.assertEqual(annotation["semantic_name"], "Greatsword")

            relationship = catalog.add_relationship(
                {
                    "character_asset_id": "hero",
                    "animation_id": "hero_attack",
                    "weapon_asset_id": "claymore",
                    "semantic_name": "Hero greatsword attack",
                },
                output_path,
            )
            self.assertTrue(relationship["id"])
            rebuilt = catalog.build_relationship_catalog(
                assets_path, animations_path, output_path, annotations_path
            )
            self.assertEqual(rebuilt["relationship_count"], 1)
            hero = next(item for item in rebuilt["assets"] if item["id"] == "hero")
            self.assertEqual(hero["annotation"]["semantic_name"], "Hero")
            self.assertEqual(catalog.validate_relationship_catalog(output_path), [])

            deleted = catalog.delete_relationship(relationship["id"], output_path)
            self.assertEqual(deleted["id"], relationship["id"])
            self.assertEqual(catalog.load_relationship_state(output_path)["relationship_count"], 0)
            self.assertEqual(catalog.validate_relationship_catalog(output_path), [])

    def test_explicit_components_are_persisted_and_legacy_ids_are_derived(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "relationships.json"
            output_path.write_text(
                json.dumps(
                    {
                        "schema": catalog.RELATIONSHIP_SCHEMA,
                        "relationship_count": 0,
                        "relationships": [],
                    }
                ),
                encoding="utf-8",
            )

            relationship = catalog.add_relationship(
                {
                    "id": "hero-loadout",
                    "character_asset_id": "hero",
                    "animation_id": "hero_attack",
                    "components": [
                        {
                            "id": "weapon",
                            "asset_id": "claymore",
                            "role": "weapon",
                            "attach_to": "hand_r",
                            "fit": {"mode": "character_height", "ratio": 0.8},
                        },
                        {
                            "id": "lantern",
                            "asset_id": "lantern",
                            "role": "prop",
                            "parent": "scene",
                            "transform": {
                                "position": [1, 0, 2],
                                "rotation": [0, 45, 0],
                                "scale": [0.5, 0.5, 0.5],
                            },
                        },
                    ],
                },
                output_path,
            )

            self.assertEqual(relationship["weapon_asset_id"], "claymore")
            self.assertIsNone(relationship["shield_asset_id"])
            self.assertEqual([item["id"] for item in relationship["components"]], ["weapon", "lantern"])

    def test_save_as_new_keeps_the_original_relationship(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "relationships.json"
            output_path.write_text(
                json.dumps({"relationship_count": 0, "relationships": []}),
                encoding="utf-8",
            )
            original = catalog.add_relationship(
                {
                    "id": "hero-idle",
                    "character_asset_id": "hero",
                    "animation_id": "idle",
                    "semantic_name": "Hero idle",
                },
                output_path,
            )
            duplicate = catalog.add_relationship(
                {
                    "id": original["id"],
                    "save_as_new": True,
                    "character_asset_id": "hero",
                    "animation_id": "run",
                    "semantic_name": "Hero run",
                },
                output_path,
            )
            saved = catalog.load_relationship_state(output_path)["relationships"]
            self.assertNotEqual(duplicate["id"], original["id"])
            self.assertEqual({item["id"] for item in saved}, {original["id"], duplicate["id"]})

    def test_legacy_relationship_is_indexed_with_components(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets_path = root / "assets.json"
            animations_path = root / "animations.json"
            output_path = root / "relationships.json"
            annotations_path = root / "semantic_annotations.json"
            assets_path.write_text(
                json.dumps(
                    {
                        "catalog_root": str(root),
                        "assets": [
                            {"id": "hero", "category": "model", "format": "fbx"},
                            {"id": "claymore", "category": "weapon", "format": "fbx"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            animations_path.write_text(
                json.dumps({"animations": [{"id": "attack", "asset_id": "hero"}]}),
                encoding="utf-8",
            )
            output_path.write_text(
                json.dumps(
                    {
                        "relationships": [
                            {
                                "id": "legacy",
                                "character_asset_id": "hero",
                                "animation_id": "attack",
                                "weapon_asset_id": "claymore",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            indexed = catalog.build_relationship_catalog(
                assets_path, animations_path, output_path, annotations_path
            )

            self.assertEqual(indexed["relationships"][0]["components"][0]["attach_to"], "hand_r")


if __name__ == "__main__":
    unittest.main()
