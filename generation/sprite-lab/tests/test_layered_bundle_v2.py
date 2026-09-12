import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import layered_bundle  # noqa: E402


class LayeredBundleV2Tests(unittest.TestCase):
    def _png(self, path: Path, color: tuple[int, int, int, int]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (32, 16), color).save(path, format="PNG")
        return path

    def _build(self, root: Path) -> dict:
        self._png(root / "character_full.png", (60, 80, 100, 255))
        self._png(root / "coat_visible.png", (200, 20, 20, 128))
        self._png(root / "coat_visible_mask.png", (255, 255, 255, 255))
        self._png(root / "sword_visible.png", (20, 200, 20, 128))
        self._png(root / "sword_visible_mask.png", (255, 255, 255, 255))
        self._png(root / "preview.png", (100, 100, 100, 255))
        runtime = layered_bundle.default_layered_runtime(
            [8, 8], rows=2, columns=4, fps=10, action_id="attack"
        )
        return layered_bundle.build_layered_bundle_v2(
            root,
            base={"id": "character", "file": "character_full.png", "z": 0},
            components=[
                {
                    "id": "coat",
                    "kind": "clothing",
                    "file": "coat_visible.png",
                    "visible_mask": "coat_visible_mask.png",
                    "z": 10,
                },
                {
                    "id": "sword",
                    "kind": "weapon",
                    "file": "sword_visible.png",
                    "visible_mask": "sword_visible_mask.png",
                    "occluders": ["character", "coat"],
                    "z": 20,
                },
            ],
            preview="preview.png",
            source={"job_id": "job-7", "provider": "chatgpt-local"},
            runtime=runtime,
            rows=2,
            columns=4,
        )

    def test_builds_modular_bundle_with_runtime_and_all_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._build(root)

            self.assertEqual(manifest["schema"], "sprite_lab.layered_sprite_bundle/v2")
            self.assertEqual(manifest["generation_mode"], "character_component_holdout")
            self.assertEqual(manifest["layout"]["cell_size"], [8, 8])
            self.assertEqual(manifest["runtime"]["frame_size"], [8, 8])
            self.assertEqual(manifest["runtime"]["actions"], [{
                "id": "attack", "start_column": 1, "frame_count": 4, "loop": True
            }])
            self.assertEqual(
                [(layer["id"], layer["role"], layer["z"]) for layer in manifest["layers"]],
                [("character", "base", 0), ("coat", "component", 10), ("sword", "component", 20)],
            )
            self.assertTrue(manifest["layers"][0]["immutable"])
            self.assertEqual(
                manifest["layers"][2]["occlusion"]["occluders"],
                ["character", "coat"],
            )
            self.assertEqual(len(manifest["hashes"]), 6)
            self.assertEqual(
                manifest["hashes"]["character_full.png"],
                hashlib.sha256((root / "character_full.png").read_bytes()).hexdigest(),
            )
            self.assertIs(layered_bundle.validate_layered_bundle(manifest, root), manifest)
            round_tripped = json.loads(json.dumps(manifest))
            self.assertIs(layered_bundle.validate_layered_bundle_v2(round_tripped, root), round_tripped)

    def test_rejects_hash_changes_unknown_occluders_and_duplicate_z(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._build(root)
            changed = copy.deepcopy(manifest)
            changed["hashes"]["coat_visible.png"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "hashes.*coat_visible"):
                layered_bundle.validate_layered_bundle_v2(changed, root)

            unknown = copy.deepcopy(manifest)
            unknown["layers"][1]["occlusion"]["occluders"] = ["ghost"]
            with self.assertRaisesRegex(ValueError, "occluders"):
                layered_bundle.validate_layered_bundle_v2(unknown)

            duplicate_z = copy.deepcopy(manifest)
            duplicate_z["layers"][2]["z"] = 10
            with self.assertRaisesRegex(ValueError, "z.*único"):
                layered_bundle.validate_layered_bundle_v2(duplicate_z)

    def test_runtime_rejects_overlapping_actions_and_mismatched_frame_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = self._build(Path(temporary))
            overlap = copy.deepcopy(manifest)
            overlap["runtime"]["actions"].append(
                {"id": "recover", "start_column": 4, "frame_count": 1, "loop": False}
            )
            with self.assertRaisesRegex(ValueError, "sobrepor"):
                layered_bundle.validate_layered_bundle_v2(overlap)

            mismatched = copy.deepcopy(manifest)
            mismatched["runtime"]["frame_size"] = [4, 4]
            with self.assertRaisesRegex(ValueError, "frame_size"):
                layered_bundle.validate_layered_bundle_v2(mismatched)

    def test_v1_remains_supported_by_dispatch_validator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [
                self._png(root / name, (20, 40, 60, 255))
                for name in ("character.png", "weapon.png", "mask.png", "preview.png")
            ]
            manifest = layered_bundle.build_layered_bundle(
                root,
                character_holdout=paths[0],
                weapon=paths[1],
                holdout_source=paths[2],
                preview=paths[3],
                source={"job_id": "legacy"},
            )
            self.assertEqual(manifest["schema"], layered_bundle.LAYERED_BUNDLE_SCHEMA_V1)
            self.assertIs(layered_bundle.validate_layered_bundle(manifest, root), manifest)


if __name__ == "__main__":
    unittest.main()
