import tempfile
import unittest
from pathlib import Path
import sys

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import sprite_render  # noqa: E402
import render_profile  # noqa: E402


class SpriteRenderTests(unittest.TestCase):
    def test_locked_render_profile_contract(self) -> None:
        manifest = render_profile.normalize_manifest(
            {
                "schema": "sprite_lab.render_profile/v1",
                "id": "hero_two_hand_sword_v1",
                "cell_size": [256, 256],
                "ortho_scale": 3.8,
                "foot_anchor": [128, 220],
                "camera_elevation": 35.264,
                "camera_azimuth": 45,
                "directions": 8,
                "phases": 8,
                "ground_z": 0.0,
            }
        )
        self.assertEqual(manifest["cell_size"], [256, 256])
        self.assertEqual(manifest["cell_size_mode"], "fixed")
        self.assertEqual(manifest["ortho_scale_mode"], "fixed")
        self.assertEqual(manifest["foot_anchor"], [128, 220])
        self.assertEqual(manifest["ortho_scale"], 3.8)

    def test_ai_render_profile_accepts_five_directions_and_nine_phases(self) -> None:
        manifest = render_profile.normalize_manifest(
            {
                "schema": "sprite_lab.render_profile/v1",
                "id": "ai_base_v1",
                "cell_size": [672, 672],
                "ortho_scale": 2.5770567,
                "foot_anchor": [336, 578],
                "camera_elevation": 35.264,
                "camera_azimuth": 45,
                "directions": 5,
                "phases": 9,
                "ground_z": 0.0,
            }
        )
        self.assertEqual(manifest["directions"], 5)
        self.assertEqual(manifest["phases"], 9)
        self.assertEqual(sprite_render.render_dimensions({"profile": "5x9"}), ("5x9", 5, 9))

    def test_locked_render_profile_rejects_invalid_anchor_and_scale(self) -> None:
        base = {
            "schema": "sprite_lab.render_profile/v1",
            "id": "hero_v1",
            "cell_size": [256, 256],
            "ortho_scale": 3.8,
            "foot_anchor": [128, 220],
            "camera_elevation": 35.264,
            "camera_azimuth": 45,
            "directions": 8,
            "phases": 8,
            "ground_z": 0.0,
        }
        with self.assertRaises(ValueError):
            render_profile.normalize_manifest({**base, "ortho_scale": 0})
        with self.assertRaises(ValueError):
            render_profile.normalize_manifest({**base, "foot_anchor": [128, 300]})

    def test_profile_values_override_per_job_camera_and_grid(self) -> None:
        profile = render_profile.normalize_manifest(
            {
                "schema": "sprite_lab.render_profile/v1",
                "id": "hero_v1",
                "cell_size": [512, 512],
                "ortho_scale": 4.2,
                "foot_anchor": [256, 440],
                "camera_elevation": 30,
                "camera_azimuth": 40,
                "directions": 8,
                "phases": 12,
                "ground_z": -0.02,
            }
        )
        settings = render_profile.apply_to_settings(
            profile,
            {
                "resolution": 128,
                "rows": 2,
                "phases": 4,
                "elevation": 10,
                "azimuth": 20,
            },
        )
        self.assertEqual(settings["resolution"], 512)
        self.assertEqual(settings["cell_size_mode"], "fixed")
        self.assertEqual(settings["ortho_scale_mode"], "fixed")
        self.assertEqual(settings["rows"], 8)
        self.assertEqual(settings["phases"], 12)
        self.assertEqual(settings["elevation"], 30.0)
        self.assertEqual(settings["azimuth"], 40.0)
        self.assertFalse(settings["dynamic_x"])
        self.assertEqual(settings["horizontal_margin_px"], 1.0)
        self.assertFalse(settings["dynamic_y"])
        self.assertEqual(settings["vertical_margin_px"], 1.0)

    def test_fit_profile_preserves_pixel_density_when_cell_grows(self) -> None:
        manifest = render_profile.normalize_manifest(
            {
                "schema": "sprite_lab.render_profile/v1",
                "id": "fit_profile",
                "cell_size": [256, 256],
                "cell_size_mode": "fit",
                "cell_size_quantum": 16,
                "ortho_scale": 2.0,
                "foot_anchor": [128, 220],
                "directions": 8,
                "phases": 8,
            }
        )
        self.assertEqual(manifest["cell_size_mode"], "fit")
        self.assertEqual(manifest["cell_size_quantum"], 16)
        cell, ortho = render_profile.fitted_cell_size(
            2.4276,
            2.5268,
            base_cell_size=256,
            ortho_scale=2.0,
            quantum=16,
            padding_px=2.0,
        )
        self.assertEqual(cell, 336)
        self.assertAlmostEqual(ortho, 2.625)

    def test_optimized_ortho_scale_keeps_cell_and_uses_minimum_fit(self) -> None:
        scale = render_profile.optimized_ortho_scale(
            3.6135492325,
            2.7844327688,
            cell_size=[256, 256],
            minimum_ortho_scale=2.0,
            horizontal_margin_px=2.0,
            vertical_margin_px=2.0,
        )
        self.assertAlmostEqual(scale, 3.670907157, places=6)
        self.assertEqual(
            render_profile.optimized_ortho_scale(
                1.0,
                1.0,
                cell_size=[256, 256],
                minimum_ortho_scale=2.0,
                horizontal_margin_px=2.0,
                vertical_margin_px=2.0,
            ),
            2.0,
        )

    def test_profile_rejects_invalid_ortho_scale_mode(self) -> None:
        with self.assertRaises(ValueError):
            render_profile.normalize_manifest(
                {
                    "schema": "sprite_lab.render_profile/v1",
                    "id": "hero_v1",
                    "cell_size": [256, 256],
                    "ortho_scale": 2.0,
                    "ortho_scale_mode": "automatic",
                    "foot_anchor": [128, 220],
                    "directions": 8,
                    "phases": 8,
                }
            )

    def test_horizontal_fit_offset_moves_only_overflow(self) -> None:
        self.assertEqual(
            render_profile.horizontal_fit_offset(
                -0.5,
                0.5,
                ortho_scale=3.0,
                cell_size=[256, 256],
                margin_px=2.0,
            ),
            0.0,
        )
        offset = render_profile.horizontal_fit_offset(
            -0.5,
            1.6,
            ortho_scale=3.0,
            cell_size=[256, 256],
            margin_px=2.0,
        )
        self.assertAlmostEqual(offset, -(1.6 - (1.5 - 3.0 * 2.0 / 256.0)))

    def test_horizontal_fit_offset_rejects_content_wider_than_view(self) -> None:
        with self.assertRaisesRegex(ValueError, "excede a área"):
            render_profile.horizontal_fit_offset(
                -1.6,
                1.6,
                ortho_scale=3.0,
                cell_size=[256, 256],
                margin_px=2.0,
            )

    def test_vertical_fit_offset_moves_overflow_and_preserves_scale(self) -> None:
        self.assertEqual(
            render_profile.vertical_fit_offset(
                -0.8,
                0.8,
                ortho_scale=3.0,
                cell_size=[256, 256],
                margin_px=2.0,
            ),
            0.0,
        )
        offset = render_profile.vertical_fit_offset(
            -1.7,
            0.9,
            ortho_scale=3.0,
            cell_size=[256, 256],
            margin_px=2.0,
        )
        self.assertAlmostEqual(offset, 1.7 + (-(1.5 - 3.0 * 2.0 / 256.0)))

    def test_profile_rejects_non_boolean_dynamic_axes(self) -> None:
        base = {
            "schema": "sprite_lab.render_profile/v1",
            "id": "hero_v1",
            "cell_size": [256, 256],
            "ortho_scale": 3.8,
            "foot_anchor": [128, 220],
            "camera_elevation": 35.264,
            "camera_azimuth": 45,
            "directions": 8,
            "phases": 8,
            "ground_z": 0.0,
        }
        with self.assertRaises(ValueError):
            render_profile.normalize_manifest({**base, "dynamic_x": "true"})
        with self.assertRaises(ValueError):
            render_profile.normalize_manifest({**base, "dynamic_y": 1})

    def test_camera_presets_are_named_and_validate(self) -> None:
        preset = render_profile.camera_preset("top_down")
        self.assertEqual(preset["label"], "Top-down")
        self.assertEqual(preset["elevation"], 80.0)
        self.assertAlmostEqual(preset["ortho_scale"], 2.6732591727815302)
        self.assertEqual(preset["profile_id"], "hero_reference_v1_top_down")
        for item in render_profile.list_camera_presets():
            self.assertGreater(item["ortho_scale"], 0.0)
            self.assertTrue(item["profile_id"])
        self.assertEqual(len(render_profile.list_camera_presets()), 6)
        with self.assertRaises(ValueError):
            render_profile.camera_preset("unknown")

    def test_camera_profiles_are_derived_from_the_isometric_manifest(self) -> None:
        profiles = {
            item["camera_preset"]: item
            for item in render_profile.list_profiles()
            if item.get("camera_preset")
        }
        self.assertEqual(
            set(profiles),
            {"isometric", "platform", "frontal", "three_quarter", "diagonal", "top_down"},
        )
        for camera_id, preset in render_profile.CAMERA_PRESETS.items():
            profile = profiles[camera_id]
            self.assertEqual(profile["camera_elevation"], preset["elevation"])
            self.assertEqual(profile["camera_azimuth"], preset["azimuth"])
            self.assertAlmostEqual(profile["ortho_scale"], preset["ortho_scale"])

    def test_profile_round_trip_is_persistent_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for profile_id in ("z_profile", "a_profile"):
                manifest = {
                    "schema": "sprite_lab.render_profile/v1",
                    "id": profile_id,
                    "cell_size": [256, 256],
                    "ortho_scale": 3.8,
                    "foot_anchor": [128, 220],
                    "camera_elevation": 35.264,
                    "camera_azimuth": 45,
                    "directions": 8,
                    "phases": 8,
                    "ground_z": 0.0,
                }
                render_profile.write(render_profile.profile_path(profile_id, root), manifest)
            self.assertEqual(
                [item["id"] for item in render_profile.list_profiles(root)],
                ["a_profile", "z_profile"],
            )

    def test_profiles_and_sheet_contract(self) -> None:
        self.assertEqual(sprite_render.render_dimensions({"profile": "8x8"}), ("8x8", 8, 8))
        self.assertEqual(sprite_render.render_dimensions({"profile": "8x12"}), ("8x12", 8, 12))
        with self.assertRaises(ValueError):
            sprite_render.render_dimensions({"profile": "4x4"})

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for row in range(2):
                for column in range(3):
                    Image.new("RGBA", (8, 8), (row * 80, column * 60, 0, 255)).save(
                        root / f"row{row}_col{column}.png"
                    )
            sheet = sprite_render._build_sheet(root, 2, 3, 8)
            with Image.open(sheet) as image:
                self.assertEqual(image.size, (24, 16))
            self.assertTrue((root / "spritesheet.png").is_file())

    def test_ai_base_pages_use_three_by_three_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for row in range(5):
                for column in range(9):
                    image = Image.new("RGBA", (672, 672), (row * 30, column * 20, 10, 255))
                    image.save(root / f"row{row}_col{column}.png")
            pages = sprite_render._build_ai_base_pages(
                root,
                sprite_render.AI_DIRECTION_ROWS,
                9,
                672,
            )
            self.assertEqual(list(pages), ["r1", "r2", "r5", "r6", "r7"])
            for path in pages.values():
                with Image.open(path) as image:
                    self.assertEqual(image.size, (2048, 2048))
                    self.assertEqual(image.mode, "RGBA")
                    self.assertEqual(image.getpixel((16, 16))[3], 255)
                    self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_directional_gifs_follow_render_rows(self) -> None:
        self.assertEqual(list(sprite_render.DIRECTION_ROWS), [f"r{index}" for index in range(1, 9)])
        self.assertEqual(
            [row["label"] for row in sprite_render.DIRECTION_CONTRACT["rows"]],
            ["south", "south_east", "east", "north_east", "north", "north_west", "west", "south_west"],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for row in range(8):
                for column in range(3):
                    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
                    image.putpixel((1 + column, 1 + row % 3), (0, 0, 0, 255))
                    image.putpixel((3, 3), (row * 20, column * 30, 0, 255))
                    image.save(root / f"row{row}_col{column}.png")
            gifs = sprite_render._build_gifs(root, 8, 3, 10)
            self.assertEqual(list(gifs), list(sprite_render.DIRECTION_ROWS))
            for direction, path in gifs.items():
                self.assertEqual(path.name, f"animation_{direction}.gif")
                with Image.open(path) as image:
                    self.assertEqual(image.size, (8, 8))
                    self.assertEqual(image.n_frames, 3)
                    for frame in range(image.n_frames):
                        image.seek(frame)
                        decoded = image.convert("RGBA")
                        self.assertEqual(decoded.getpixel((0, 0))[3], 0)
                        opaque_black = sum(
                            1
                            for red, green, blue, alpha in decoded.getdata()
                            if alpha > 0 and red == green == blue == 0
                        )
                        self.assertLess(opaque_black, 20)

    def test_upscaled_rotation_gif_uses_canonical_direction_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for row in range(8):
                for column in range(8):
                    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
                    image.putpixel((1, 1), (row * 20, column * 20, 0, 255))
                    image.save(root / f"row{row}_col{column}.png")
            path, sequence = sprite_render._build_upscaled_diagonal_gif(root, 8, 8, 10)
            self.assertIsNotNone(path)
            self.assertEqual(sequence[0]["source"], "row0_col0.png")
            self.assertEqual(sequence[-1]["source"], "row7_col7.png")
            with Image.open(path) as image:
                self.assertEqual(image.size, (16, 16))
                self.assertEqual(image.n_frames, 8)

    def test_locked_profile_rejects_clipped_cells(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clipped = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            for y in range(2, 6):
                clipped.putpixel((0, y), (255, 255, 255, 255))
            clipped.save(root / "row0_col0.png")
            with self.assertRaisesRegex(RuntimeError, "aumente ortho_scale"):
                sprite_render._validate_cells_not_clipped(root, 1, 1)

    def test_locked_profile_accepts_content_inside_cell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
            for x in range(2, 6):
                for y in range(2, 6):
                    image.putpixel((x, y), (255, 255, 255, 255))
            image.save(root / "row0_col0.png")
            sprite_render._validate_cells_not_clipped(root, 1, 1)


if __name__ == "__main__":
    unittest.main()
