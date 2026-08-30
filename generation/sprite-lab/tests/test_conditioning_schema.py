import copy
import sys
import unittest
from pathlib import Path


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import conditioning_schema as schema  # noqa: E402


def valid_manifest() -> dict:
    return {
        "schema": schema.PACK_SCHEMA,
        "id": "conditioning-run-r1",
        "project": "generation",
        "action": "run",
        "direction": "r1",
        "cell_size": [32, 32],
        "foot_anchor": [16, 28],
        "frame_count": 2,
        "fps": 10,
        "channels": ["beauty", "silhouette", "segmentation"],
        "frames": [
            {
                "id": "f00",
                "index": 0,
                "channels": {
                    "beauty": "beauty/f00.png",
                    "silhouette": "silhouette/f00.png",
                    "segmentation": "segmentation/f00.png",
                },
            },
            {
                "id": "f01",
                "index": 1,
                "channels": {
                    "beauty": "beauty/f01.png",
                    "silhouette": "silhouette/f01.png",
                    "segmentation": "segmentation/f01.png",
                },
            },
        ],
        "target_reference": {"path": "target-reference/target.png", "role": "identity"},
        "prompt": {"version": "v1", "template": "Transform the character."},
        "authority": {
            "identity": ["target_reference"],
            "structure": ["beauty", "silhouette", "segmentation"],
        },
    }


class ConditioningSchemaTests(unittest.TestCase):
    def test_valid_manifest_is_normalized_with_foot_anchor(self) -> None:
        normalized = schema.validate_manifest(valid_manifest())
        self.assertEqual(normalized["cell_size"], [32, 32])
        self.assertEqual(normalized["foot_anchor"], [16, 28])
        self.assertEqual(normalized["frames"][1]["index"], 1)

    def test_default_foot_anchor_is_derived_from_cell(self) -> None:
        manifest = valid_manifest()
        del manifest["foot_anchor"]
        normalized = schema.validate_manifest(manifest)
        self.assertEqual(normalized["foot_anchor"], [16, 28])

    def test_required_channel_cannot_be_omitted(self) -> None:
        manifest = valid_manifest()
        manifest["channels"] = ["beauty", "silhouette"]
        with self.assertRaises(schema.ConditioningSchemaError):
            schema.validate_manifest(manifest)

    def test_paths_must_be_relative_and_safe(self) -> None:
        manifest = valid_manifest()
        manifest["frames"][0]["channels"]["beauty"] = "../outside.png"
        with self.assertRaises(schema.ConditioningSchemaError):
            schema.validate_manifest(manifest)

        manifest = valid_manifest()
        manifest["target_reference"]["path"] = "/tmp/target.png"
        with self.assertRaises(schema.ConditioningSchemaError):
            schema.validate_manifest(manifest)

    def test_frame_order_and_anchor_bounds_are_enforced(self) -> None:
        manifest = valid_manifest()
        manifest["frames"][1]["index"] = 0
        with self.assertRaises(schema.ConditioningSchemaError):
            schema.validate_manifest(manifest)

        manifest = copy.deepcopy(valid_manifest())
        manifest["foot_anchor"] = [16, 33]
        with self.assertRaises(schema.ConditioningSchemaError):
            schema.validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
