import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))
sys.path.insert(0, str(SPRITE_LAB / "tests" / "fixtures"))

import ai_render_spec  # noqa: E402
import layered_bundle  # noqa: E402
import layered_compositor  # noqa: E402
import server  # noqa: E402
import weapon_layer_persistence  # noqa: E402
import weapon_layer_worker  # noqa: E402
from image_generation_provider import DryRunProvider  # noqa: E402
from st08_contract_fixture import create_st08_fixture  # noqa: E402
import character_layer_worker  # noqa: E402


class CountingDryRunProvider(DryRunProvider):
    def __init__(self) -> None:
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return super().generate(request)


class St08DryRunTests(unittest.TestCase):
    def _spec(self):
        spec = ai_render_spec.default_render_spec(name="st08-dry-run")
        spec.update({
            "generation_mode": "character_weapon_holdout",
            "output": {**spec["output"], "width": 1024, "height": 1024},
            "layer_contract": {
                "weapon_component_id": "weapon_1",
                "generation_order": ["character", "weapon"],
                "composition_order": ["weapon", "character_holdout"],
                "layers": [
                    {"id": "weapon", "z": 0},
                    {"id": "character_holdout", "z": 1},
                ],
            },
            "source_contract": {"components": [{"id": "weapon_1", "role": "weapon"}]},
        })
        return spec

    def _inputs(self, root: Path):
        paths = {name: root / f"{name}.png" for name in ("identity", "beauty", "weapon", "guide")}
        for path in paths.values():
            Image.new("RGBA", (1024, 1024), (0, 0, 0, 0)).save(path)
        return paths

    @staticmethod
    def _character_manifest():
        return [
            {"index": 1, "type": "identity"},
            {"index": 2, "type": "beauty"},
        ]

    @staticmethod
    def _weapon_manifest():
        return [
            {"index": 1, "type": "weapon_reference"},
            {"index": 2, "type": "character_full"},
            {"index": 3, "type": "weapon_guide"},
        ]

    def test_out_of_order_weapon_call_is_blocked_before_provider_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._inputs(root)
            provider = CountingDryRunProvider()
            states = []

            with self.assertRaises(weapon_layer_persistence.WeaponPrerequisiteError):
                weapon_layer_worker.run_weapon_layer(
                    job_id="st08-order",
                    render_spec=self._spec(),
                    reference_manifest=self._weapon_manifest(),
                    input_images=[paths["weapon"], root / "character_full.png", paths["guide"]],
                    output_dir=root,
                    model="dry-run-model",
                    provider=provider,
                    update_state=states.append,
                )

            self.assertEqual(provider.calls, [])
            self.assertEqual(states[-1]["stage"], "weapon_blocked")
            response = json.loads((root / "weapon_response.json").read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "weapon_blocked")
            self.assertIn("character_full", response["error"])

    def test_missing_weapon_reference_is_failed_with_durable_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._inputs(root)
            provider = CountingDryRunProvider()
            character_layer_worker.run_character_layer(
                job_id="st08-missing-reference",
                render_spec=self._spec(),
                reference_manifest=self._character_manifest(),
                input_images=[paths["identity"], paths["beauty"]],
                output_dir=root,
                model="dry-run-model",
                provider=provider,
                update_state=lambda _state: None,
            )
            states = []
            missing_weapon = root / "missing-weapon-reference.png"

            with self.assertRaises(FileNotFoundError):
                weapon_layer_worker.run_weapon_layer(
                    job_id="st08-missing-reference",
                    render_spec=self._spec(),
                    reference_manifest=self._weapon_manifest(),
                    input_images=[missing_weapon, root / "character_full.png", paths["guide"]],
                    output_dir=root,
                    model="dry-run-model",
                    provider=provider,
                    update_state=states.append,
                )

            self.assertEqual(states[-1]["stage"], "weapon_failed")
            response = json.loads((root / "weapon_response.json").read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "weapon_failed")
            self.assertIn("missing-weapon-reference", response["error"])
            self.assertEqual(len(provider.calls), 1)

    def test_missing_character_intermediate_is_blocked_and_does_not_call_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._inputs(root)
            provider = CountingDryRunProvider()
            character_layer_worker.run_character_layer(
                job_id="st08-missing-intermediate",
                render_spec=self._spec(),
                reference_manifest=self._character_manifest(),
                input_images=[paths["identity"], paths["beauty"]],
                output_dir=root,
                model="dry-run-model",
                provider=provider,
                update_state=lambda _state: None,
            )
            (root / "character_full.png").unlink()
            states = []

            with self.assertRaises(weapon_layer_persistence.WeaponPrerequisiteError):
                weapon_layer_worker.run_weapon_layer(
                    job_id="st08-missing-intermediate",
                    render_spec=self._spec(),
                    reference_manifest=self._weapon_manifest(),
                    input_images=[paths["weapon"], root / "character_full.png", paths["guide"]],
                    output_dir=root,
                    model="dry-run-model",
                    provider=provider,
                    update_state=states.append,
                )

            self.assertEqual(states[-1]["stage"], "weapon_blocked")
            response = json.loads((root / "weapon_response.json").read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "weapon_blocked")
            self.assertIn("character_full.png", response["error"])
            self.assertEqual(len(provider.calls), 1)

    def test_complete_dry_run_is_ordered_published_and_idempotently_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self._inputs(root)
            provider = CountingDryRunProvider()
            states = []
            character_args = dict(
                job_id="st08-complete",
                render_spec=self._spec(),
                reference_manifest=self._character_manifest(),
                input_images=[paths["identity"], paths["beauty"]],
                output_dir=root,
                model="dry-run-model",
                provider=provider,
                update_state=states.append,
            )
            weapon_args = dict(
                job_id="st08-complete",
                render_spec=self._spec(),
                reference_manifest=self._weapon_manifest(),
                input_images=[paths["weapon"], root / "character_full.png", paths["guide"]],
                output_dir=root,
                model="dry-run-model",
                provider=provider,
                update_state=states.append,
            )
            first_character = character_layer_worker.run_character_layer(**character_args)
            first_weapon = weapon_layer_worker.run_weapon_layer(**weapon_args)
            second_character = character_layer_worker.run_character_layer(**character_args)
            second_weapon = weapon_layer_worker.run_weapon_layer(**weapon_args)

            self.assertEqual(
                [state["stage"] for state in states[:6]],
                [
                    "generating_character",
                    "validating_character",
                    "character_complete",
                    "generating_weapon",
                    "validating_weapon",
                    "weapon_complete",
                ],
            )
            self.assertFalse(first_character["resumed"])
            self.assertFalse(first_weapon["resumed"])
            self.assertTrue(second_character["resumed"])
            self.assertTrue(second_weapon["resumed"])
            self.assertEqual([request.generation_role for request in provider.calls], ["character", "weapon"])
            weapon_request = json.loads((root / "weapon.request.json").read_text(encoding="utf-8"))
            character_response = json.loads((root / "character_response.json").read_text(encoding="utf-8"))
            self.assertEqual(character_response["provider"], "dry-run")
            self.assertEqual(weapon_request["generation_role"], "weapon")
            self.assertEqual(weapon_request["metadata"]["character_dependency"]["status"], "character_complete")
            self.assertEqual(character_response["status"], "character_complete")
            self.assertEqual(json.loads((root / "weapon_response.json").read_text())["status"], "weapon_complete")

            fixture = create_st08_fixture(root / "fixture")
            composed = layered_compositor.compose_layered_spritesheets(
                fixture["cells"], root / "composed", grid=(8, 8)
            )
            manifest = layered_bundle.build_layered_bundle(
                root / "composed",
                character_holdout=composed["character_holdout_spritesheet"],
                weapon=composed["weapon_spritesheet"],
                holdout_source=composed["holdout_cut_mask"],
                preview=composed["composite_preview"],
                source={
                    "job_id": "st08-complete",
                    "render_id": "st08-dry-run",
                    "provider": "dry-run",
                    "model": "dry-run-model",
                },
            )
            layered_bundle.validate_layered_bundle(manifest, root / "composed")
            with mock.patch.object(server, "LAYERED_PUBLISHED_ROOT", root / "published"):
                publication = server.publish_layered_job(
                    "st08-complete", root / "composed", source=manifest["source"]
                )
            self.assertEqual(publication["manifest_url"], "/api/layered-bundles/st08-complete")
            self.assertEqual(len(provider.calls), 2)


if __name__ == "__main__":
    unittest.main()
