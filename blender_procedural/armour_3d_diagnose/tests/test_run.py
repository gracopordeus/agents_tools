import json
import tempfile
import unittest
from pathlib import Path

from armour_3d_diagnose import run


class Tables(unittest.TestCase):
    def test_table_aligns_columns_and_marks_missing_values(self):
        text = run.table([{"piece": "Boot_R", "min_cm": 0.1}, {"piece": "Suit", "min_cm": None, "note": "x"}])
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(len({line.index(line.split()[1]) for line in lines}), 1)      # second column starts at the same place
        self.assertIn("-", lines[2])
        self.assertEqual(run.table([]), "  (nothing to measure)")

    def test_summary_reads_the_reports_of_a_run(self):
        fit = {"gates": {"pose_poke_through": {"value": 82.4, "limit": 150.0, "pass": True}},
               "pieces": {"Boot_R": {"slot": "boot.R", "placement": {"scale": 2.06},
                                     "seat": {"translation_m": [0.0, 0.01, 0.0], "rotation_deg": 0.0, "at_limit": {"translation": False}},
                                     "fit_length": {"factor": 0.9}, "parts": [],
                                     "proportion": [{"name": "piece", "mode": "flexible", "mean_offset_m": {"min": 0.0, "median": 0.01, "max": 0.03},
                                                     "vertex_shift_m": {"median": 0.01, "max": 0.05}}]}},
               "poke": {"area_cm2_by_bone": {"shin": 30.0}, "body_area_pct": 1.9},
               "poses": {"tested": 1, "rows": [{"clip": "Jump", "fraction": 0.25, "poke_area_cm2": 82.4,
                                               "poke_area_incl_covered_cm2": 183.0, "area_cm2_by_bone": {"shin": 82.4}}]}}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "3_fit").mkdir()
            (Path(folder) / "3_fit" / "report.json").write_text(json.dumps(fit), encoding="utf-8")
            tables = run.summary(Path(folder), None)
        titles = [item["check"] for item in tables]
        self.assertIn("summary: gates", titles)
        self.assertIn("summary: worst poses", titles)
        sized = next(item for item in tables if item["check"].startswith("summary: how each piece"))
        self.assertEqual(sized["rows"][0]["offset_cm"], "+0.0 .. +3.0")
        with self.assertRaises(SystemExit):
            run.summary(Path("/nonexistent"), None)


if __name__ == "__main__":
    unittest.main()
