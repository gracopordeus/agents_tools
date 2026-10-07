"""Asset-file validation (system Python, no Blender)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run


class AssetFile(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        for name in ("armour.glb", "rig.json", "body.json", "slots.json"):
            (self.root / name).write_text("{}")
        self.write("plan.json", {"parts": [{"name": "Helmet"}, {"name": "Chest"}]})
        self.write("budgets.json", {"budgets": {"Helmet": 6000, "Chest": 10000}})
        self.asset = {"name": "test", "source": "armour.glb", "split": {"plan": "plan.json"},
                      "decimate": {"budgets": "budgets.json"},
                      "fit": {"pieces": {"Helmet": "helmet", "Chest": "chest"}, "body": {"rig": "rig.json"},
                              "body_profile": "body.json", "slot_profile": "slots.json"}}

    def tearDown(self):
        self.directory.cleanup()

    def write(self, name: str, value) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value))
        return path

    def load(self):
        return run.load_asset(self.write("asset.json", self.asset))

    def test_valid_asset_gets_absolute_paths_and_defaults(self):
        asset = self.load()
        self.assertTrue(Path(asset["source"]).is_absolute())
        self.assertEqual((asset["split"]["normal_policy"], asset["fit"]["style"], asset["fit"]["config"]),
                         ("strict", "plate", {}))

    def test_pieces_must_match_across_steps(self):
        self.asset["fit"]["pieces"] = {"Helmet": "helmet"}
        with self.assertRaisesRegex(run.AssetError, "fit pieces and the split plan"):
            self.load()
        self.asset["fit"]["pieces"] = {"Helmet": "helmet", "Chest": "chest"}
        self.write("budgets.json", {"budgets": {"Helmet": 6000, "Chest": 10000, "Legs": 8000}})
        with self.assertRaisesRegex(run.AssetError, "decimate budgets and the split plan"):
            self.load()

    def test_rejects_unknown_fields_styles_and_missing_files(self):
        for change, message in ((lambda a: a.update(extra=1), "exactly the fields"),
                                (lambda a: a["fit"].update(colour="red"), "Unknown fields in fit"),
                                (lambda a: a["fit"].update(style="robe"), "fit.style"),
                                (lambda a: a["split"].update(normal_policy="loose"), "normal_policy"),
                                (lambda a: a.update(source="missing.glb"), "Missing files"),
                                (lambda a: a["fit"].update(body={"poses": "rig.json"}), "fit.body")):
            asset = json.loads(json.dumps(self.asset))
            change(asset)
            self.asset, saved = asset, self.asset
            with self.assertRaisesRegex(run.AssetError, message):
                self.load()
            self.asset = saved

    def test_fit_job_points_at_the_decimated_blend(self):
        asset = self.load()
        run.write_fit_job(asset, self.root)
        job = json.loads((self.root / "fit.job.json").read_text())
        self.assertEqual(job["armour"], str(self.root / "2_decimate" / "armour_decimated.blend"))
        self.assertEqual(job["config"], {"style": "plate"})

    def test_new_run_refuses_a_used_directory(self):
        self.write("asset.json", self.asset)
        (self.root / "used").mkdir()
        (self.root / "used" / "file").write_text("x")
        self.assertEqual(run.main(["--asset", str(self.root / "asset.json"), "--out", str(self.root / "used")]), 2)


if __name__ == "__main__":
    unittest.main()
