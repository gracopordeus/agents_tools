import sys
import tempfile
import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import server  # noqa: E402


class AiRenderReferenceTests(unittest.TestCase):
    def test_identity_lineart_cache_is_reused_by_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "identity.png"
            output = root / "job" / "identity_lineart.png"
            cache = root / "cache"
            Image.new("RGB", (32, 32), (255, 255, 255)).save(source)
            source_hash = server.sha256_file(source)
            cached = cache / f"{source_hash}_lineart_standard.png"
            cached.parent.mkdir(parents=True, exist_ok=True)
            Image.new("L", (32, 32), 0).save(cached)

            with patch.object(server, "GEMINI_IDENTITY_LINEART_CACHE", cache), patch.object(
                server.subprocess, "run"
            ) as run:
                report = server._prepare_identity_lineart(source, output)

            self.assertTrue(report["cache_hit"])
            self.assertEqual(report["mode"], "lineart_standard")
            self.assertEqual(output.read_bytes(), cached.read_bytes())
            run.assert_not_called()

    def test_canny_identity_guide_uses_a_separate_cache_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "identity.png"
            output = root / "job" / "identity_lineart.png"
            cache = root / "cache"
            Image.new("RGB", (32, 32), (255, 255, 255)).save(source)
            source_hash = server.sha256_file(source)
            cached = cache / f"{source_hash}_canny_edges.png"
            cached.parent.mkdir(parents=True, exist_ok=True)
            Image.new("L", (32, 32), 128).save(cached)

            with patch.object(server, "GEMINI_IDENTITY_LINEART_CACHE", cache), patch.object(
                server.subprocess, "run"
            ) as run:
                report = server._prepare_identity_lineart(
                    source, output, mode="canny_edges"
                )

            self.assertTrue(report["cache_hit"])
            self.assertEqual(report["mode"], "canny_edges")
            self.assertEqual(report["detector"], "controlnet_aux.CannyDetector")
            self.assertEqual(output.read_bytes(), cached.read_bytes())
            run.assert_not_called()

    def test_identity_guide_mode_is_forwarded_to_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "identity.png"
            output = root / "job" / "identity_lineart.png"
            cache = root / "cache"
            Image.new("RGB", (32, 32), (255, 255, 255)).save(source)

            def fake_run(command, **kwargs):
                self.assertEqual(command[-4:], ["--mode", "canny_edges", "--device", "auto"])
                Image.new("L", (32, 32), 0).save(output)
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"mode": "canny_edges"}),
                    stderr="",
                )

            with patch.object(server, "GEMINI_IDENTITY_LINEART_CACHE", cache), patch.object(
                server.subprocess, "run", side_effect=fake_run
            ):
                report = server._prepare_identity_lineart(
                    source, output, mode="canny_edges"
                )

            self.assertFalse(report["cache_hit"])
            self.assertEqual(report["mode"], "canny_edges")
            self.assertTrue((cache / f"{server.sha256_file(source)}_canny_edges.png").is_file())

    def test_identity_guide_rejects_unknown_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "identity_lineart_mode inválido"):
            server.normalize_identity_guide_mode("unknown")

    def test_frame_control_is_a_selectable_reference(self) -> None:
        channels = server.normalize_gemini_channels(
            ["beauty", "bones", "lineart", "frame_control"]
        )

        self.assertEqual(
            channels,
            ["beauty", "bones", "lineart", "frame_control"],
        )

    def test_frame_control_does_not_resolve_as_a_blender_file(self) -> None:
        self.assertNotIn("frame_control", server.GEMINI_CHANNEL_FILES)
        self.assertIn("frame_control", server.AI_RENDER_REFERENCE_CHANNELS)

    def test_frame_control_uses_two_pixel_black_boundary_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "frame_control.png"
            server.build_ai_render_frame_control(output)

            with Image.open(output) as image:
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getpixel((256, 128)), (0, 0, 0, 255))
                self.assertEqual(image.getpixel((258, 128)), (0, 0, 0, 0))
                self.assertEqual(image.getpixel((128, 128)), (0, 0, 0, 0))

    def test_source_contract_inherits_component_direction_camera_and_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / "request.json").write_text(
                json.dumps(
                    {
                        "action_name": "Armature|Sprint_Loop",
                        "components": [
                            {
                                "id": "axe",
                                "role": "weapon",
                                "asset_id": "unknown_axe",
                                "attach_to": "hand_r",
                                "visible": True,
                            }
                        ],
                        "direction_contract": {
                            "rows": [
                                {"row": index, "id": f"direction_{index}", "vector": [index, 0]}
                                for index in range(1, 9)
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            (source / "render_metadata.json").write_text(
                json.dumps(
                    {
                        "camera": {"type": "ORTHO", "preset": "isometric", "elevation": 30.0, "azimuth": 45},
                        "render_profile": {"foot_anchor": [128, 220], "cell_size": [256, 256]},
                    }
                ),
                encoding="utf-8",
            )

            contract = server.ai_render_source_contract(source)

            self.assertEqual(contract["components"][0]["role"], "weapon")
            self.assertEqual(contract["components"][0]["hand"], "right")
            self.assertEqual(contract["directions"][0]["id"], "direction_1")
            self.assertEqual(contract["camera"]["type"], "ORTHO")
            self.assertEqual(contract["framing"]["foot_anchor"], [128, 220])

    def test_legacy_compass_labels_are_normalized_by_physical_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            legacy_rows = [
                ("south", [0, -1]),
                ("south_east", [1, -1]),
                ("east", [1, 0]),
                ("north_east", [1, 1]),
                ("north", [0, 1]),
                ("north_west", [-1, 1]),
                ("west", [-1, 0]),
                ("south_west", [-1, -1]),
            ]
            (source / "request.json").write_text(
                json.dumps(
                    {
                        "direction_contract": {
                            "rows": [
                                {"row": f"r{index}", "label": label, "target": target}
                                for index, (label, target) in enumerate(legacy_rows, start=1)
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            contract = server.ai_render_source_contract(source)

            self.assertEqual(
                [row["id"] for row in contract["directions"]],
                [
                    "north", "north_east", "east", "south_east",
                    "south", "south_west", "west", "north_west",
                ],
            )
            self.assertEqual(contract["directions"][0]["vector"], [0, 1])
            self.assertEqual(contract["directions"][4]["vector"], [0, -1])
            self.assertEqual(contract["directions"][0]["target"], [0, -1])
            self.assertEqual(contract["directions"][4]["target"], [0, 1])


if __name__ == "__main__":
    unittest.main()
