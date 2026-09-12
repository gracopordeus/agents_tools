import io
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("FBX/Sword.fbx", b"sword")
    return buffer.getvalue()


def _handler(payload: bytes):
    return SimpleNamespace(
        headers={"Content-Length": str(len(payload))},
        rfile=io.BytesIO(payload),
    )


class ServerUploadTests(unittest.TestCase):
    def _patch_root(self, root: Path) -> None:
        original = server.catalog_upload_root
        server.catalog_upload_root = lambda: root
        self.addCleanup(setattr, server, "catalog_upload_root", original)

    def test_accepts_simple_zip_name(self) -> None:
        self.assertEqual(
            server.sanitize_upload_filename("Pack Test [1].zip"),
            "Pack Test [1].zip",
        )

    def test_neutralizes_traversal_to_basename(self) -> None:
        self.assertEqual(server.sanitize_upload_filename("../evil.zip"), "evil.zip")
        self.assertEqual(server.sanitize_upload_filename("..\\evil.zip"), "evil.zip")
        self.assertEqual(server.sanitize_upload_filename("/tmp/pack.zip"), "pack.zip")

    def test_rejects_hidden_and_non_zip_names(self) -> None:
        for name in (".hidden.zip", "pack.rar", "noextension", ""):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    server.sanitize_upload_filename(name)

    def test_rejects_non_zip_payload(self) -> None:
        with TemporaryDirectory() as temporary:
            self._patch_root(Path(temporary))
            with self.assertRaises(ValueError):
                server.save_catalog_upload(_handler(b"not a zip"), "pack.zip")

    def test_roundtrip_zip_with_dedup(self) -> None:
        payload = _zip_bytes()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._patch_root(root)
            first = server.save_catalog_upload(_handler(payload), "pack.zip")
            self.assertEqual(first["file"], "pack.zip")
            self.assertEqual(first["source_id_hint"], "incoming__pack")
            self.assertTrue((root / "pack.zip").is_file())
            second = server.save_catalog_upload(_handler(payload), "pack.zip")
            self.assertEqual(second["file"], "pack_2.zip")

    def test_lists_uploads(self) -> None:
        payload = _zip_bytes()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._patch_root(root)
            server.save_catalog_upload(_handler(payload), "pack.zip")
            (root / "notes.txt").write_text("ignorado", encoding="utf-8")
            rows = server.list_catalog_uploads()
            self.assertEqual([row["file"] for row in rows], ["pack.zip"])
            self.assertEqual(rows[0]["bytes"], len(payload))


if __name__ == "__main__":
    unittest.main()
