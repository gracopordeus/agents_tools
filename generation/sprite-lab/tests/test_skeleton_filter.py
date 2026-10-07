import unittest
from pathlib import Path
import sys


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

from skeleton_filter import keep_skeleton_bone  # noqa: E402


class SkeletonFilterTests(unittest.TestCase):
    def test_keeps_main_deform_chain(self) -> None:
        for name in (
            "pelvis",
            "spine_01",
            "neck_01",
            "Head",
            "clavicle_l",
            "upperarm_l",
            "lowerarm_l",
            "hand_l",
            "thigh_l",
            "calf_l",
            "foot_l",
        ):
            with self.subTest(name=name):
                self.assertTrue(keep_skeleton_bone(name))

    def test_removes_non_skeleton_rig_bones(self) -> None:
        for name in (
            "root",
            "index_01_l",
            "middle_02_r",
            "ball_l",
            "toe_l",
            "upperarm_ik_l",
            "pole_target_l",
            "hand_ctrl_l",
        ):
            with self.subTest(name=name):
                self.assertFalse(keep_skeleton_bone(name))

    def test_requires_deformation_bone(self) -> None:
        self.assertFalse(keep_skeleton_bone("upperarm_l", use_deform=False))


if __name__ == "__main__":
    unittest.main()
