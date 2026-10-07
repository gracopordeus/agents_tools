import unittest
import json
import tempfile
from pathlib import Path

from armour_3d_split.partition import assembled_plan, assignments
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


class AssembledSet(unittest.TestCase):
    """A standing figure facing -Y: the pieces are named by where they are."""

    def inventory(self, extra=()):
        box = lambda index, triangles, low, high: {"index": index, "triangles": triangles, "bbox_min": low, "bbox_max": high, "face_ids": []}
        components = [box(0, 50000, [-0.22, -0.09, 0.32], [0.22, 0.09, 0.84]),      # body armour
                      box(1, 9000, [-0.17, -0.07, 0.0], [-0.04, 0.06, 0.32]),       # boot at x < 0: the right one
                      box(2, 8800, [0.04, -0.07, 0.0], [0.17, 0.06, 0.32]),
                      box(3, 7000, [-0.37, -0.07, 0.44], [-0.17, 0.05, 0.67]),      # gauntlet at x < 0
                      box(4, 6500, [0.17, -0.07, 0.44], [0.37, 0.05, 0.67]),
                      box(5, 6000, [-0.06, -0.07, 0.84], [0.06, 0.06, 1.0]),        # helmet
                      *extra]
        return {"input_sha256": "fixture", "objects": [{"name": "Armour", "components": components}]}

    def names(self, plan):
        return {part["name"]: [selector["component"] for selector in part["selectors"]] for part in plan["parts"]}

    def test_names_the_six_pieces_and_their_sides(self):
        self.assertEqual(self.names(assembled_plan(self.inventory())),
                         {"Helmet": [5], "Suit": [0], "Glove_L": [4], "Glove_R": [3], "Boot_L": [2], "Boot_R": [1]})

    def test_a_fragment_goes_to_the_piece_that_holds_it(self):
        chip = {"index": 6, "triangles": 12, "bbox_min": [0.05, 0.0, 0.1], "bbox_max": [0.06, 0.01, 0.11], "face_ids": []}
        self.assertEqual(self.names(assembled_plan(self.inventory([chip])))["Boot_L"], [2, 6])

    def test_refuses_what_does_not_read_as_a_standing_set(self):
        lost = {"index": 6, "triangles": 12, "bbox_min": [0.5, 0.5, 0.5], "bbox_max": [0.51, 0.51, 0.51], "face_ids": []}
        seventh = {"index": 6, "triangles": 9000, "bbox_min": [0.4, 0.0, 0.0], "bbox_max": [0.5, 0.1, 0.3], "face_ids": []}
        for extra in ([lost], [seventh]):
            with self.assertRaises(ValueError):
                assembled_plan(self.inventory(extra))
        one_side = self.inventory()
        one_side["objects"][0]["components"][2].update(bbox_min=[-0.35, -0.07, 0.0], bbox_max=[-0.2, 0.06, 0.32])
        with self.assertRaises(ValueError):
            assembled_plan(one_side)


if __name__ == "__main__":
    unittest.main()
