import sys
import unittest
from pathlib import Path


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import composition_export  # noqa: E402


class CompositionExportTests(unittest.TestCase):
    def test_static_command_omits_animation_arguments(self) -> None:
        command = composition_export._build_blender_command(
            Path("/tmp/tree.glb"),
            None,
            None,
            [],
            Path("/tmp/tree-composition.glb"),
        )

        self.assertNotIn("--animation", command)
        self.assertNotIn("--action-name", command)
        self.assertIn("--character", command)
        self.assertIn("--components", command)

    def test_animated_command_keeps_animation_arguments(self) -> None:
        command = composition_export._build_blender_command(
            Path("/tmp/hero.glb"),
            Path("/tmp/ual.fbx"),
            {"action_name": "Run"},
            [],
            Path("/tmp/hero-composition.glb"),
        )

        self.assertIn("--animation", command)
        self.assertIn("--action-name", command)
        self.assertIn("Run", command)


if __name__ == "__main__":
    unittest.main()
