import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import server  # noqa: E402


class St07UiStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = (SPRITE_LAB / "web" / "app.js").read_text(encoding="utf-8")

    def test_dom_fixture_covers_each_layered_state_and_keeps_layers_separate(self) -> None:
        fixture = json.loads(
            (SPRITE_LAB / "tests" / "fixtures" / "st07_job_states.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(len(fixture), 6)
        self.assertEqual(
            {row["progress"]["stage"] for row in fixture},
            {
                "generating_character",
                "character_complete",
                "generating_weapon",
                "layered_mask_pass_realesrgan",
                "weapon_failed",
                "completed",
            },
        )
        for state in (
            "generating_character",
            "validating_character",
            "character_complete",
            "generating_weapon",
            "weapon_complete",
            "building_holdout",
            "completed",
            "failed",
        ):
            with self.subTest(state=state):
                self.assertIn(state + ":", self.app)
        self.assertIn("function normalizedLayerProgress(job)", self.app)
        self.assertIn('data-layer-progress="true"', self.app)
        self.assertIn('data-layer-id="${esc(layer.id)}"', self.app)
        self.assertIn('data-result-layer="${esc(id)}"', self.app)

    def test_failed_dom_fixture_exposes_layer_error_and_retry_action(self) -> None:
        fixture = json.loads(
            (SPRITE_LAB / "tests" / "fixtures" / "st07_job_states.json").read_text(
                encoding="utf-8"
            )
        )
        failed = next(row for row in fixture if row["status"] == "error")
        self.assertEqual(failed["progress"]["layers"]["weapon"]["status"], "error")
        self.assertIn("function failedGeminiLayer(job)", self.app)
        self.assertIn("data-retry-ai-render", self.app)
        self.assertIn("async function retryGeminiJob(job)", self.app)
        self.assertIn("duplicateAiRenderJob(job, { announce: false })", self.app)
        self.assertIn("await generateGemini()", self.app)

    def test_progress_update_persists_layer_map_without_breaking_legacy_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "jobs.json"
            path.write_text(
                json.dumps([{"id": "job", "status": "running"}]), encoding="utf-8"
            )
            layers = {
                "character": {"status": "done", "percent": 100},
                "weapon": {"status": "running", "percent": 65},
                "composition": {"status": "queued", "percent": 0},
            }
            server.update_pipeline_progress(
                path,
                "job",
                stage="generating_weapon",
                percent=65,
                eta_seconds=300,
                layers=layers,
            )
            job = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(job["progress"]["stage"], "generating_weapon")
            self.assertEqual(job["progress"]["percent"], 65)
            self.assertEqual(job["progress"]["layers"], layers)

    def test_single_sheet_does_not_render_layered_result_or_progress(self) -> None:
        self.assertIn(
            'if (!isLayeredGeminiJob(job)) return "";',
            self.app,
        )
        self.assertIn(
            'if (!isLayeredGeminiJob(job)) return [];',
            self.app,
        )
        self.assertIn('layered ? "2 passes + holdout" : aiRenderOutputLabel(job)', self.app)
        self.assertIn(
            'const progressMarkup = failed && !isLayeredGeminiJob(job) ? "" : pipelineProgressMarkup(job);',
            self.app,
        )

    def test_dom_js_behavior_harness_updates_poll_card_and_coalesces_retry(self) -> None:
        harness = SPRITE_LAB / "tests" / "fixtures" / "st07_ui_behavior_harness.js"
        result = subprocess.run(
            ["node", str(harness)],
            cwd=SPRITE_LAB,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("ST07_UI_BEHAVIOR_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
