import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animation_catalog import (
    ANIMATION_SCHEMA,
    classify_action,
    validate_animation_catalog,
)


class AnimationCatalogTests(unittest.TestCase):
    def test_classifies_ual_action_leaf_name(self) -> None:
        result = classify_action("Armature|Armature|Sword_Regular_A_Rec")

        self.assertEqual(result["clip_name"], "Sword_Regular_A_Rec")
        self.assertEqual(result["category"], "attack")
        self.assertIn("sword", result["semantic_tags"])

    def test_does_not_recommend_no_loop_as_loop(self) -> None:
        result = classify_action("Armature|Armature|Idle_No_Loop")

        self.assertEqual(result["category"], "idle")
        self.assertFalse(result["loop_name_hint"])
        self.assertTrue(result["explicit_no_loop"])

    def test_validate_manifest_rejects_orphan_animation(self) -> None:
        with TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "animations.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema": ANIMATION_SCHEMA,
                        "asset_count": 1,
                        "animation_count": 1,
                        "assets": [{"asset_id": "asset-a", "source_sha256": "hash"}],
                        "animations": [
                            {
                                "id": "animation-a",
                                "asset_id": "missing-asset",
                                "action_name": "Idle",
                                "frame_count": 1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            errors = validate_animation_catalog(manifest_path)

            self.assertTrue(any("asset ausente" in error for error in errors))

