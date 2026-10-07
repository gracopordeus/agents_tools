import unittest
import json
import tempfile
from pathlib import Path

from armour_3d_split.partition import assignments
from armour_3d_split.run import dependencies


class PartitionTests(unittest.TestCase):
    def setUp(self):
        self.inventory = {"input_sha256": "fixture", "objects": [
            {"name": "Armour", "polygons": 4, "materials": ["Metal", "Leather"],
             "polygon_material_slots": [0, 0, 1, 1],
             "components": [{"face_ids": [0, 1]}, {"face_ids": [2, 3]}]}]}

    def plan(self, selectors):
        return {"version": 1, "input_sha256": "fixture", "parts": [
            {"name": name, "selectors": selection} for name, selection in selectors]}

    def test_material_partition(self):
        result = assignments(self.plan([
            ("Plate", [{"object": "Armour", "material_slot": 0}]),
            ("Lining", [{"object": "Armour", "material_slot": 1}])]), self.inventory)
        self.assertEqual(result, {"Plate": {"Armour": [0, 1]}, "Lining": {"Armour": [2, 3]}})

    def test_rejects_duplicate_and_orphan_faces(self):
        for selectors in [
            [("Plate", [{"object": "Armour", "faces": [0, 0, 1, 2, 3]}])],
            [("Plate", [{"object": "Armour", "component": 0}])],
        ]:
            with self.assertRaises(ValueError):
                assignments(self.plan(selectors), self.inventory)

    def test_rejects_foreign_hash_names_and_unknown_fields(self):
        valid = self.plan([("Plate", [{"object": "Armour"}])])
        invalid = [dict(valid, input_sha256="different"), dict(valid, automatic=True),
                   self.plan([("../Helmet", [{"object": "Armour"}])]),
                   self.plan([("Plate", [{"object": "Body"}])]),
                   self.plan([("Plate", [{"object": "Armour", "faces": [True, 1, 2, 3]}])])]
        for plan in invalid:
            with self.assertRaises(ValueError):
                assignments(plan, self.inventory)

    def test_external_buffer_changes_invalidate_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "armour.gltf"
            buffer = root / "mesh.bin"
            source.write_text(json.dumps({"buffers": [{"uri": "mesh.bin"}]}))
            buffer.write_bytes(b"first")
            before = {uri: resource["sha256"] for uri, resource in dependencies(source).items()}
            buffer.write_bytes(b"different")
            after = {uri: resource["sha256"] for uri, resource in dependencies(source).items()}
            plan = dict(self.plan([("Plate", [{"object": "Armour"}])]), dependency_hashes=before)
            inventory = dict(self.inventory, dependency_hashes=after)
            with self.assertRaises(ValueError):
                assignments(plan, inventory)


if __name__ == "__main__":
    unittest.main()
