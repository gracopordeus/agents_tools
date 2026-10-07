import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import re

from PIL import Image

SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import server  # noqa: E402


class WeaponReferenceTests(unittest.TestCase):
    def _data_url(self) -> str:
        buffer = io.BytesIO()
        Image.new("RGBA", (12, 18), (1, 2, 3, 255)).save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    def test_weapon_reference_has_separate_cache_hash_and_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                mock.patch.object(server, "WEAPON_REFERENCES_PATH", root / "weapon.json"),
                mock.patch.object(server, "WEAPON_REFERENCES_WORK", root / "weapon"),
                mock.patch.object(server, "GEMINI_REFERENCES_PATH", root / "identity.json"),
            ):
                saved = server.save_weapon_reference(self._data_url(), "Sword")

                self.assertTrue(saved["id"].startswith("weapon_reference_"))
                self.assertEqual(saved["name"], "Sword")
                self.assertEqual(saved["size"], [12, 18])
                self.assertEqual(len(saved["sha256"]), 64)
                self.assertEqual(server.get_weapon_reference(saved["id"]), saved)
                self.assertTrue(server.weapon_reference_path(saved["id"]).is_file())
                self.assertFalse((root / "identity.json").exists())

    def test_invalid_image_and_holdout_without_reference_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "imagem"):
            server.save_weapon_reference("data:image/png;base64,bm90LWltYWdl", "bad")
        server.require_weapon_reference(
            {"generation_mode": "single_sheet"}, None
        )
        with self.assertRaisesRegex(ValueError, "referência.*arma"):
            server.require_weapon_reference(
                {"generation_mode": "character_weapon_holdout"}, None
            )

    def test_render_request_resolves_weapon_reference_for_each_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                mock.patch.object(server, "WEAPON_REFERENCES_PATH", root / "weapon.json"),
                mock.patch.object(server, "WEAPON_REFERENCES_WORK", root / "weapon"),
            ):
                saved = server.save_weapon_reference(self._data_url(), "Sword")

                self.assertEqual(
                    server.resolve_weapon_reference_for_render(
                        {"generation_mode": "single_sheet"}, None
                    ),
                    "",
                )
                with self.assertRaisesRegex(ValueError, "referência.*arma"):
                    server.resolve_weapon_reference_for_render(
                        {"generation_mode": "character_weapon_holdout"}, None
                    )
                self.assertEqual(
                    server.resolve_weapon_reference_for_render(
                        {"generation_mode": "character_weapon_holdout"}, saved["id"]
                    ),
                    saved["id"],
                )

    def test_initialize_gemini_renders_saved_weapon_references(self) -> None:
        source = (SPRITE_LAB / "web" / "app.js").read_text(encoding="utf-8")
        match = re.search(
            r"function initializeGemini\(\) \{(?P<body>.*?)\n\}", source, re.DOTALL
        )
        self.assertIsNotNone(match)
        self.assertIn("renderWeaponReferences();", match.group("body"))


if __name__ == "__main__":
    unittest.main()
