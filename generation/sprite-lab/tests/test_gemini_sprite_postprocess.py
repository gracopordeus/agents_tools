import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import gemini_sprite_postprocess as subject  # noqa: E402
import server  # noqa: E402


class GeminiSpritePostprocessTests(unittest.TestCase):
    def test_process_layer_bundle_uses_isolated_outputs_and_common_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "layered-output"
            layers = {
                "character": {
                    "generated_sheet": root / "character_full.png",
                    "structural_dir": root / "character-structural",
                },
                "weapon": {
                    "generated_sheet": root / "weapon_full.png",
                    "structural_dir": root / "weapon-structural",
                },
            }
            calls: list[dict] = []

            def fake_process(generated_sheet, structural_dir, layer_output, **kwargs):
                layer_output = Path(layer_output)
                layer_id = kwargs["layer_id"]
                calls.append({
                    "layer_id": layer_id,
                    "generated_sheet": Path(generated_sheet),
                    "structural_dir": Path(structural_dir),
                    "output": layer_output,
                    "profile_id": kwargs["model_profile"],
                })
                original = layer_output / "variants" / "original"
                original.mkdir(parents=True)
                Image.new("RGBA", (16, 8), (20, 40, 60, 255)).save(
                    original / "spritesheet.png", format="PNG"
                )
                metadata = {
                    "rows": 1,
                    "phases": 2,
                    "output_cell": 8,
                    "foot_anchor": [4, 6],
                    "layer_id": layer_id,
                    "profile_id": kwargs["model_profile"],
                }
                (layer_output / "render_metadata.json").write_text(
                    json.dumps(metadata), encoding="utf-8"
                )
                return metadata

            report = subject.process_layer_bundle(
                layers,
                output,
                profile_id="bicubic_local_v1",
                process_fn=fake_process,
                rows=1,
                phases=2,
                source_cell=4,
            )

            self.assertEqual([call["layer_id"] for call in calls], ["character", "weapon"])
            self.assertEqual(
                {call["profile_id"] for call in calls}, {"bicubic_local_v1"}
            )
            self.assertEqual(
                {call["output"].name for call in calls},
                {"character", "weapon"},
            )
            self.assertTrue((output / "character" / "render_metadata.json").is_file())
            self.assertTrue((output / "weapon" / "render_metadata.json").is_file())
            self.assertEqual(report["profile_id"], "bicubic_local_v1")
            self.assertEqual(report["layers"]["character"]["layer_id"], "character")
            self.assertEqual(report["layers"]["weapon"]["layer_id"], "weapon")
            self.assertEqual(report["layout"], {"rows": 1, "phases": 2, "cell_size": 8})
            self.assertEqual(report["foot_anchor"], [4, 6])
            self.assertTrue((output / "layered_postprocess.json").is_file())
            persisted = json.loads(
                (output / "character" / "render_metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["layer_id"], "character")
            self.assertEqual(persisted["profile_id"], "bicubic_local_v1")
            self.assertEqual(persisted["holdout_stage"], "pending_final_holdout")
            bundle_manifest = json.loads(
                (output / "layered_postprocess.json").read_text(encoding="utf-8")
            )
            self.assertEqual(bundle_manifest["foot_anchor"], [4, 6])
            self.assertEqual(bundle_manifest["layers"]["weapon"]["foot_anchor"], [4, 6])

    def test_process_layer_bundle_does_not_publish_partial_output_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "layered-output"
            layers = {
                "character": {
                    "generated_sheet": root / "character_full.png",
                    "structural_dir": root / "character-structural",
                },
                "weapon": {
                    "generated_sheet": root / "weapon_full.png",
                    "structural_dir": root / "weapon-structural",
                },
            }

            def fake_process(_generated_sheet, _structural_dir, layer_output, **kwargs):
                layer_output = Path(layer_output)
                (layer_output / "partial.txt").parent.mkdir(parents=True, exist_ok=True)
                (layer_output / "partial.txt").write_text(kwargs["layer_id"])
                if kwargs["layer_id"] == "weapon":
                    raise RuntimeError("weapon pipeline failed")
                original = layer_output / "variants" / "original"
                original.mkdir(parents=True, exist_ok=True)
                Image.new("RGBA", (16, 8), (20, 40, 60, 255)).save(
                    original / "spritesheet.png", format="PNG"
                )
                return {
                    "rows": 1,
                    "phases": 2,
                    "output_cell": 8,
                    "foot_anchor": [4, 6],
                }

            with self.assertRaisesRegex(RuntimeError, "weapon pipeline failed"):
                subject.process_layer_bundle(
                    layers,
                    output,
                    profile_id="bicubic_local_v1",
                    process_fn=fake_process,
                    rows=1,
                    phases=2,
                    source_cell=4,
                )

            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(f".{output.name}.staging-*")), [])

    def test_process_layer_bundle_preserves_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "layered-output"
            output.mkdir()
            sentinel = output / "existing.txt"
            sentinel.write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(FileExistsError, "já existe"):
                subject.process_layer_bundle(
                    {
                        "character": {"generated_sheet": root / "character.png", "structural_dir": root},
                        "weapon": {"generated_sheet": root / "weapon.png", "structural_dir": root},
                    },
                    output,
                    process_fn=lambda *_args, **_kwargs: {},
                )

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_process_layer_bundle_requires_matching_finite_foot_anchor(self) -> None:
        scenarios = {
            "missing": {"character": None, "weapon": None},
            "malformed": {"character": [4], "weapon": [4, 6]},
            "asymmetric": {"character": [4, 6], "weapon": [5, 6]},
        }
        for scenario, anchors in scenarios.items():
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                output = root / "layered-output"

                def fake_process(_generated_sheet, _structural_dir, layer_output, **kwargs):
                    layer_output = Path(layer_output)
                    original = layer_output / "variants" / "original"
                    original.mkdir(parents=True, exist_ok=True)
                    Image.new("RGBA", (16, 8), (20, 40, 60, 255)).save(
                        original / "spritesheet.png", format="PNG"
                    )
                    return {
                        "rows": 1,
                        "phases": 2,
                        "output_cell": 8,
                        "foot_anchor": anchors[kwargs["layer_id"]],
                    }

                with self.assertRaisesRegex(ValueError, "foot_anchor"):
                    subject.process_layer_bundle(
                        {
                            "character": {"generated_sheet": root / "character.png", "structural_dir": root},
                            "weapon": {"generated_sheet": root / "weapon.png", "structural_dir": root},
                        },
                        output,
                        profile_id="bicubic_local_v1",
                        process_fn=fake_process,
                        rows=1,
                        phases=2,
                        source_cell=4,
                    )

                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(f".{output.name}.staging-*")), [])

    def test_server_normalizes_1k_sheet_per_cell_for_512_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / "generated_1k.png"
            source = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
            for row in range(8):
                for column in range(8):
                    color = (row * 24, column * 24, 120, 255)
                    cell = Image.new("RGBA", (128, 128), color)
                    source.alpha_composite(cell, (column * 128, row * 128))
                    cell.close()
            source.save(generated, format="PNG")
            source.close()

            prepared, report = server._prepare_gemini_postprocess_sheet(
                generated,
                root / "postprocess",
            )

            self.assertTrue(report["applied"])
            self.assertEqual(report["method"], "per_cell_lanczos_2x")
            with Image.open(prepared) as output:
                self.assertEqual(output.size, (2048, 2048))
                self.assertEqual(output.getpixel((256, 256)), (24, 24, 120, 255))
                self.assertEqual(output.getpixel((512, 256)), (24, 48, 120, 255))

    def test_resumes_masks_when_chroma_cleanup_failed_after_birefnet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mask_pass = root / "mask-pass"
            mask_pass.mkdir()
            birefnet = mask_pass / "birefnet_masks"
            birefnet.mkdir()
            for row in range(1):
                for column in range(2):
                    Image.new("L", (8, 8), 255).save(birefnet / f"row{row}_col{column}.png")
                    Image.new("RGBA", (8, 8), (60, 45, 30, 0)).save(
                        mask_pass / f"row{row}_col{column}.png"
                    )

            with patch.object(subject, "MASK_CACHE_ROOT", root / "mask-cache"):
                report = subject._load_cached_mask_pass(mask_pass, "cache-key", 1, 2)

            self.assertIsNotNone(report)
            assert report is not None
            self.assertEqual(report["source_masks"], "birefnet_masks")
            self.assertEqual(len(list((mask_pass / "foreground_cleanup_masks").glob("*.png"))), 2)

    def test_process_uses_birefnet_mask_pass_before_quality_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / "generated.png"
            generated.touch()
            structural = root / "structural"
            structural.mkdir()
            output = root / "output"
            commands: list[list[str]] = []

            def fake_run(command: list[str], label: str) -> dict:
                commands.append(command)
                if "realesrgan_birefnet_pipeline.py" in command[1]:
                    masks = output / "mask_pass_realesrgan_birefnet" / "foreground_cleanup_masks"
                    masks.mkdir(parents=True)
                    for row in range(1):
                        for column in range(2):
                            (masks / f"row{row}_col{column}.png").touch()
                elif "pregan_realesrgan_reuse_mask_pipeline.py" in command[1]:
                    official = Path(command[4])
                    official.mkdir(parents=True)
                    for row in range(1):
                        for column in range(2):
                            Image.new("RGBA", (8, 8), (100, 80, 20, 255)).save(
                                official / f"row{row}_col{column}.png"
                            )
                elif "temporal_palette_refine.py" in command[1]:
                    variant = Path(command[3])
                    variant.mkdir(parents=True)
                    (variant / "animation_all_directions_1-2-5-4-3-8-7-6.gif").touch()
                return {"label": label}

            with patch.object(subject, "_run", side_effect=fake_run), patch.object(
                subject, "MASK_CACHE_ROOT", root / "mask-cache"
            ):
                report = subject.process(
                    generated,
                    structural,
                    output,
                    rows=1,
                    phases=2,
                    source_cell=256,
                    realesrgan_repo=root / "Real-ESRGAN",
                    python_executable="python",
                    lineart_mode="lineart_coarse",
                )

            self.assertIn("realesrgan_birefnet_pipeline.py", commands[0][1])
            self.assertIn("--birefnet-threshold", commands[0])
            self.assertEqual(
                commands[0][commands[0].index("--chroma-cleanup") + 1],
                "auto",
            )
            self.assertEqual(
                commands[0][commands[0].index("--model-profile") + 1],
                "anime_x4plus_6b",
            )
            self.assertIn("pregan_realesrgan_reuse_mask_pipeline.py", commands[1][1])
            self.assertEqual(
                commands[1][commands[1].index("--model-profile") + 1],
                "anime_x4plus_6b",
            )
            self.assertEqual(
                Path(commands[1][3]),
                output / "mask_pass_realesrgan_birefnet" / "foreground_cleanup_masks",
            )
            self.assertEqual(
                commands[1][commands[1].index("--lineart-mode") + 1],
                "lineart_coarse",
            )
            self.assertIn("approved_birefnet_mask_512", report["pipeline"])
            self.assertNotIn("structural_alpha_and_alignment", report["pipeline"])

    def test_builds_ordered_gif_for_original_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for row in range(8):
                for column in range(2):
                    Image.new("RGBA", (8, 8), (row * 20, column * 40, 0, 255)).save(
                        output / f"row{row}_col{column}.png"
                    )

            result = subject._build_ordered_gif(output, 8, 2, 10)

            self.assertTrue(result.is_file())


if __name__ == "__main__":
    unittest.main()
