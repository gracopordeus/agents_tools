import sys
import unittest
from pathlib import Path


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

from rig_compatibility import (  # noqa: E402
    bone_role,
    compatibility_report,
)


class RigCompatibilityTests(unittest.TestCase):
    def test_mixamo_and_ual_roles_resolve_to_the_same_map(self) -> None:
        ual = [
            "root", "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head",
            "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l", "index_01_l",
            "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r", "index_01_r",
            "thigh_l", "calf_l", "foot_l", "ball_l", "thigh_r", "calf_r", "foot_r", "ball_r",
        ]
        mixamo = [
            "mixamorig:Hips", "mixamorig:Spine", "mixamorig:Spine1", "mixamorig:Spine2",
            "mixamorig:Neck", "mixamorig:Head", "mixamorig:LeftShoulder", "mixamorig:LeftArm",
            "mixamorig:LeftForeArm", "mixamorig:LeftHand", "mixamorig:LeftHandIndex1",
            "mixamorig:RightShoulder", "mixamorig:RightArm", "mixamorig:RightForeArm",
            "mixamorig:RightHand", "mixamorig:RightHandIndex1", "mixamorig:LeftUpLeg",
            "mixamorig:LeftLeg", "mixamorig:LeftFoot", "mixamorig:LeftToeBase",
            "mixamorig:RightUpLeg", "mixamorig:RightLeg", "mixamorig:RightFoot",
            "mixamorig:RightToeBase",
        ]

        report = compatibility_report(mixamo, ual)

        self.assertTrue(report["compatible"])
        self.assertEqual(report["source_rig_family"], "mixamo")
        self.assertEqual(report["target_rig_family"], "ual1")
        self.assertEqual(report["missing_critical_roles"], [])
        self.assertEqual(report["mapping"]["pelvis"], "mixamorig:Hips")
        self.assertEqual(bone_role("mixamorig:LeftHandIndex1"), "index_01_l")

    def test_unmapped_rig_is_rejected_by_missing_critical_roles(self) -> None:
        report = compatibility_report(["root", "pelvis"], ["root", "pelvis"])

        self.assertFalse(report["compatible"])
        self.assertIn("hand_l", report["missing_critical_roles"])

    def test_common_humanoid_aliases_resolve(self) -> None:
        self.assertEqual(bone_role("Hip"), "pelvis")
        self.assertEqual(bone_role("Chest"), "spine_02")
        self.assertEqual(bone_role("upperArmLeft"), "upperarm_l")
        self.assertEqual(bone_role("Little2.R"), "pinky_02_r")


if __name__ == "__main__":
    unittest.main()
