"""Unit tests of the stage gates and their remedies (system Python, no Blender)."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from armour_3d_gates import remedies  # noqa: E402
from armour_3d_gates.run import evaluate, stages  # noqa: E402

LIMITS = json.loads((ROOT / "armour_3d_gates" / "gates.plate.json").read_text(encoding="utf-8"))


def part(name: str, mirror_of: str | None = None, symmetric: bool = False, lid: float | None = 0.5) -> dict:
    return {"name": name, "status": "PASS", "gates": {"triangles_within_budget": True}, "mirror_of": mirror_of,
            "symmetrized": {"mirror_error_m": 0.0, "open_edges": 0, "non_manifold_edges": 0} if symmetric else None,
            "recess": None if lid is None else {"recessed": True, "depth_m": lid, "piece_length_m": 1.0}}


def measured() -> dict:
    """A run in which every gate passes."""
    slots = {"Helmet": "helmet", "Suit": "suit", "Glove_L": "glove.L", "Glove_R": "glove.R", "Boot_L": "boot.L", "Boot_R": "boot.R"}
    gate = lambda value, limit: {"value": value, "limit": limit, "pass": True}
    return {
        "split": {"status": "PASS"},
        "decimate": {"triangles_after": 28000, "config": {"recess_lids": [{"pieces": ["Helmet", "Boot_L", "Boot_R", "Glove_L", "Glove_R"]}]},
                     "pairs": [{"pieces": ["Glove_L", "Glove_R"], "kept": "Glove_R", "replaced_by_mirror": "Glove_L"},
                               {"pieces": ["Boot_L", "Boot_R"], "kept": "Boot_R", "replaced_by_mirror": "Boot_L"}],
                     "parts": [part("Helmet", symmetric=True), part("Suit", symmetric=True, lid=None), part("Glove_R"), part("Boot_R"),
                               part("Glove_L", "Glove_R"), part("Boot_L", "Boot_R")]},
        "fit": {"pieces": {name: {"slot": slot, "centred_parts": [{"name": "leg.R", "centred": True, "off_centre_after_m": 0.0005}] if name == "Suit" else []}
                           for name, slot in slots.items()},
                "handedness": {"pieces": {"Glove_L": {"detected": "left", "matches": True}, "Glove_R": {"detected": "right", "matches": True}}},
                "gates": {"pose_poke_through": gate(120.0, 150.0)}},
        "tables": [
            {"check": "length", "rows": [{"piece": "Suit", "rule": "lengthen sleeve.R", "bone_length_cm": 28.7, "beyond_cm": 0.6, "short_cm": 0.0,
                                          "told_%": 100, "design_%": 100, "applied": True},
                                         {"piece": "Glove_R", "rule": "fit_length", "bone_length_cm": 29.2, "beyond_cm": 9.0, "short_cm": 0.0,
                                          "told_%": 100, "design_%": 150, "applied": False},
                                         {"piece": "Boot_R", "rule": "fit_length", "bone_length_cm": 50.8, "beyond_cm": 0.5, "short_cm": 0.0,
                                          "told_%": 100, "design_%": 100, "applied": True}]},
            {"check": "proportion", "rows": [{"piece": "Helmet", "width_design_%": 25.3, "height_design_%": 30.8, "fitted_over_design": 1.0, "sized_by_body": False},
                                             {"piece": "Boot_R", "fitted_over_design": 0.84, "sized_by_body": True}]},
            {"check": "symmetry", "rows": [{"piece": "Suit", "against": "its own mirror image", "max_mm": 6.7, "p95_mm": 1.6},
                                           {"piece": "Glove_L", "against": "Glove_R", "max_mm": 0.0, "p95_mm": 0.0}]},
            {"check": "collar", "piece": "Suit", "rows": [{"gap_left_cm": 2.1, "gap_right_cm": 2.2, "gap_front_cm": 0.7, "gap_back_cm": None}]},
            {"check": "shape", "rows": [{"piece": "Helmet", "p95_%": 0.0}, {"piece": "Suit", "p95_%": 36.9}, {"piece": "Glove_L", "p95_%": 20.8},
                                        {"piece": "Glove_R", "p95_%": 20.8}, {"piece": "Boot_L", "p95_%": 21.7}, {"piece": "Boot_R", "p95_%": 21.7}]},
        ]}


def failing(results: list[dict]) -> list[str]:
    return [gate["gate"] for gate in results if not gate["pass"]]


class Gates(unittest.TestCase):
    def test_a_clean_run_passes_every_gate_of_the_limits_file(self):
        results = evaluate(measured(), LIMITS)
        self.assertEqual(failing(results), [])
        self.assertEqual({gate["gate"] for gate in results}, set(LIMITS["gates"]))
        self.assertTrue(all(stage["status"] == "PASS" for stage in stages(results)))

    def test_each_defect_trips_its_own_gate(self):
        def broken(change) -> list[str]:
            data = measured()
            change(data)
            return failing(evaluate(data, LIMITS))
        table = lambda data, check: next(item for item in data["tables"] if item["check"] == check)["rows"]
        cases = {
            "pairs_mirrored": lambda d: d["decimate"]["pairs"].pop(),
            "halves_mirrored_mm": lambda d: d["decimate"]["parts"][1].update(symmetrized=None),
            "halves_closed": lambda d: d["decimate"]["parts"][0]["symmetrized"].update(open_edges=12),
            "lid_depth_share": lambda d: d["decimate"]["parts"][2]["recess"].update(depth_m=0.3),
            "budget": lambda d: d["decimate"]["parts"][3].update(status="FAIL"),
            "handedness": lambda d: d["fit"]["handedness"]["pieces"]["Glove_L"].update(matches=False),
            "length_as_designed_pct": lambda d: table(d, "length")[0].update({"design_%": 150}),
            "length_beyond_cm": lambda d: table(d, "length")[0].update(beyond_cm=6.9),
            "length_short_cm": lambda d: table(d, "length")[0].update(short_cm=15.0),
            "mouth_off_centre_cm": lambda d: d["fit"]["pieces"]["Suit"]["centred_parts"][0].update(off_centre_after_m=0.026),
            "collar_gap_cm": lambda d: table(d, "collar")[0].update(gap_front_cm=-1.8),
            "collar_sides_differ_cm": lambda d: table(d, "collar")[0].update(gap_left_cm=3.5),
            "set_proportion": lambda d: table(d, "proportion")[0].update(fitted_over_design=1.26),
            "pair_symmetry_mm": lambda d: table(d, "symmetry")[1].update(max_mm=4.0),
            "self_symmetry_p95_mm": lambda d: table(d, "symmetry")[0].update(p95_mm=4.2),
            "shape_p95_pct": lambda d: table(d, "shape")[0].update({"p95_%": 12.0}),
            "fit_gates": lambda d: d["fit"]["gates"]["pose_poke_through"].update({"pass": False, "value": 180.0}),
        }
        for gate, change in cases.items():
            self.assertEqual(broken(change), [gate], gate)

    def test_a_piece_sized_by_a_bone_is_not_held_to_the_design(self):
        data = measured()
        next(item for item in data["tables"] if item["check"] == "proportion")["rows"][1]["fitted_over_design"] = 0.6
        self.assertEqual(failing(evaluate(data, LIMITS)), [])


class Remedies(unittest.TestCase):
    def setUp(self):
        self.profile = {"slots": {
            "suit": {"collar": {"gap_m": 0.012}, "parts": [{"name": "sleeve.R", "proportion": {"lengthen": {"fraction": 1.0}}, "centre": True}]},
            "boot.R": {"fit_length": {"fraction": 1.0}}, "helmet": {"proportion": {}}}}
        self.budgets = {"symmetrize": [{"pieces": ["Suit"], "keep": "R"}]}

    def results(self, change) -> list[dict]:
        data = measured()
        change(data)
        return evaluate(data, LIMITS)

    def test_a_rim_past_its_joint_shortens_the_rule_inside_its_range(self):
        table = lambda data: next(item for item in data["tables"] if item["check"] == "length")["rows"]
        found = remedies.patches(self.results(lambda d: table(d)[0].update(beyond_cm=2.5)), LIMITS, self.profile, self.budgets)
        self.assertEqual(found[0]["path"], ["slots", "suit", "parts", 0, "proportion", "lengthen", "fraction"])
        self.assertAlmostEqual(found[0]["value"], round(1.0 - (2.5 + 0.3) / 28.7, 4))
        found = remedies.patches(self.results(lambda d: table(d)[2].update(beyond_cm=40.0)), LIMITS, self.profile, self.budgets)
        values = {tuple(patch["path"][2:]): patch["value"] for patch in found}
        self.assertEqual(values[("fit_length", "fraction")], 0.3)              # the end of the allowed range, not past it
        self.assertIs(values[("fit_length", "always")], True)

    def test_a_rule_that_ignores_the_drawing_is_told_to_read_it(self):
        table = lambda data: next(item for item in data["tables"] if item["check"] == "length")["rows"]
        found = remedies.patches(self.results(lambda d: table(d)[0].update({"design_%": 150})), LIMITS, self.profile, self.budgets)
        self.assertEqual([(patch["path"][-1], patch["value"]) for patch in found], [("fraction", "design")])

    def test_a_piece_off_its_design_gets_the_design_written_in(self):
        table = lambda data: next(item for item in data["tables"] if item["check"] == "proportion")["rows"]
        found = remedies.patches(self.results(lambda d: table(d)[0].update(fitted_over_design=1.26)), LIMITS, self.profile, self.budgets)
        self.assertEqual(found[0]["value"], {"slot": "suit", "width": 0.253, "height": 0.308})
        patched = remedies.apply(self.profile, found, "slots")
        self.assertIn("relative_to", patched["slots"]["helmet"]["proportion"])
        self.assertNotIn("relative_to", self.profile["slots"]["helmet"]["proportion"])     # the original is not touched

    def test_the_neck_through_the_collar_widens_its_gap(self):
        table = lambda data: next(item for item in data["tables"] if item["check"] == "collar")["rows"]
        found = remedies.patches(self.results(lambda d: table(d)[0].update(gap_front_cm=-1.8)), LIMITS, self.profile, self.budgets)
        self.assertEqual(found[0]["path"], ["slots", "suit", "collar", "gap_m"])
        self.assertGreater(found[0]["value"], 0.012)
        self.assertLessEqual(found[0]["value"], 0.03)

    def test_a_centre_piece_not_mirrored_joins_the_symmetrize_rule(self):
        found = remedies.patches(self.results(lambda d: d["decimate"]["parts"][0].update(symmetrized=None)), LIMITS, self.profile, self.budgets)
        self.assertEqual(found[0]["file"], "budgets")
        self.assertEqual(found[0]["value"], [{"pieces": ["Suit", "Helmet"], "keep": "R"}])

    def test_a_gate_without_remedy_gives_no_patch(self):
        found = remedies.patches(self.results(lambda d: d["fit"]["gates"]["pose_poke_through"].update({"pass": False})), LIMITS, self.profile, self.budgets)
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
