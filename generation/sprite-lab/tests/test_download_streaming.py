import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server


class _FakeHandler:
    def __init__(self) -> None:
        self.headers_sent: dict[str, str] = {}
        self.status: int | None = None
        self.wfile = io.BytesIO()

    def send_response(self, code: int, message=None) -> None:
        self.status = code

    def send_header(self, key: str, value: str) -> None:
        self.headers_sent[key] = value

    def end_headers(self) -> None:
        pass


class StreamingTests(unittest.TestCase):
    def test_file_streams_without_reading_whole_body(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "model.glb"
            target.write_bytes(b"x" * (3 * 1024 * 1024 + 17))
            handler = _FakeHandler()
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("must stream")):
                server._file(handler, target)
        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.headers_sent["Content-Length"], str(3 * 1024 * 1024 + 17))
        self.assertEqual(len(handler.wfile.getvalue()), 3 * 1024 * 1024 + 17)

    def test_file_404_for_missing(self) -> None:
        handler = _FakeHandler()
        server._file(handler, Path("/tmp/does-not-exist-sprite-lab.bin"))
        self.assertEqual(handler.status, 404)

    def _make_job(self, root: Path, job_id: str = "job_test") -> Path:
        work = root / "work"
        (work / job_id).mkdir(parents=True)
        (work / job_id / "spritesheet.png").write_bytes(b"png")
        (work / job_id / "render.json").write_bytes(b"{}")
        jobs = [{"id": job_id, "status": "done"}]
        (root / "sprite_jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
        return work

    def test_disk_and_memory_archives_match(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = self._make_job(root)
            with mock.patch.object(server, "SPRITE_WORK", work), \
                mock.patch.object(server, "SPRITE_JOBS_PATH", root / "sprite_jobs.json"):
                data, filename = server.build_sprite_download("job_test")
                path, filename2 = server.build_sprite_download_file("job_test", directory=root)
                self.assertEqual(filename, filename2)
                self.assertEqual(path.read_bytes(), data)
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    self.assertIn("spritesheet.png", archive.namelist())
                    self.assertIn("metadata/render.json", archive.namelist())

    def test_download_path_streams_attachment(self) -> None:
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "sprites_job.zip"
            target.write_bytes(b"z" * 1024)
            handler = _FakeHandler()
            server._download_path(handler, target, "sprites_job.zip", "application/zip")
        self.assertEqual(handler.status, 200)
        self.assertIn("attachment", handler.headers_sent["Content-Disposition"])
        self.assertEqual(handler.headers_sent["Content-Length"], "1024")
        self.assertEqual(handler.wfile.getvalue(), b"z" * 1024)


if __name__ == "__main__":
    unittest.main()
