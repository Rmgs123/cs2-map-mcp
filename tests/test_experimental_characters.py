"""Experimental player-model code (not an MCP feature): bone presets and script generator."""
import unittest

from cs2map.experimental import characters


class ExperimentalCharacters(unittest.TestCase):
    def test_presets_and_auto_detection(self):
        self.assertEqual(set(("unity_umamusume", "mixamo", "vrm", "mmd", "valvebiped")), set(characters.available_bone_maps()))
        result = characters.detect_bone_map(["Hip", "Waist", "Spine", "Chest", "Neck", "Head",
                                             "Thigh_L", "Thigh_R", "Knee_L", "Knee_R",
                                             "Arm_L", "Arm_R", "Elbow_L", "Elbow_R",
                                             "Wrist_L", "Wrist_R", "Ankle_L", "Ankle_R"])
        self.assertEqual(result["preset"], "unity_umamusume")
        self.assertEqual(characters.detect_bone_map([
            "Hips", "Spine", "Spine1", "Spine2", "Neck", "Head",
            "LeftShoulder", "RightShoulder", "LeftArm", "RightArm",
            "LeftForeArm", "RightForeArm", "LeftHand", "RightHand",
            "LeftUpLeg", "RightUpLeg", "LeftLeg", "RightLeg",
            "LeftFoot", "RightFoot"])["preset"], "mixamo")

    def test_valvebiped_fingers_and_chest(self):
        from cs2map.experimental.blender_chars.mapping import resolve
        data = characters.bone_map('valvebiped')
        result = resolve(list(data['roles'].values()) + ['ValveBiped.Bip01_Spine1', 'Sp_He_Hair0_C_00'])
        self.assertEqual(result['preset'], 'valvebiped')
        mapped = result['source_to_target']
        self.assertEqual(mapped['ValveBiped.Bip01_L_Finger0'], 'finger_thumb_0_L')
        self.assertEqual(mapped['ValveBiped.Bip01_R_Finger12'], 'finger_index_2_R')
        self.assertEqual(mapped['ValveBiped.Bip01_L_Finger42'], 'finger_pinky_2_L')
        self.assertEqual(mapped['ValveBiped.Bip01_Spine4'], 'spine_3')
        self.assertIn('Sp_He_Hair0_C_00', result['unmapped_bones'])

    def test_generated_script_has_team_assignment(self):
        src = characters.generate_script("teams_sample", ["sample_t"], ["sample_ct"], "by_slot")
        self.assertIn("team === 3", src)
        self.assertIn("models/mcp_characters/sample_t/sample_t.vmdl", src)
        self.assertIn("GetPlayerSlot", src)


if __name__ == "__main__":
    unittest.main()
