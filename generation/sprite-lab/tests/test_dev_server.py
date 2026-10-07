import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dev_server


class _FakeProcess:
    def __init__(self, exit_after_terminate: bool = True) -> None:
        self.terminated = False
        self.killed = False
        self._alive = True
        self._exit_after_terminate = exit_after_terminate

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        if self._exit_after_terminate:
            self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        if self._alive:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0


class DevServerTests(unittest.TestCase):
    def test_stop_server_uses_grace_period_then_returns(self) -> None:
        process = _FakeProcess(exit_after_terminate=True)
        dev_server.stop_server(process)
        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)

    def test_stop_server_kills_after_drain_timeout(self) -> None:
        process = _FakeProcess(exit_after_terminate=False)
        with mock.patch.object(dev_server, "drain_timeout", return_value=0.01):
            dev_server.stop_server(process)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_stop_server_ignores_exited_process(self) -> None:
        process = _FakeProcess()
        process._alive = False
        dev_server.stop_server(process)
        self.assertFalse(process.terminated)

    def test_drain_timeout_defaults_and_env_override(self) -> None:
        import os

        os.environ.pop("SPRITE_LAB_DRAIN_TIMEOUT", None)
        self.assertEqual(dev_server.drain_timeout(), 15.0)
        os.environ["SPRITE_LAB_DRAIN_TIMEOUT"] = "5"
        try:
            self.assertEqual(dev_server.drain_timeout(), 5.0)
        finally:
            os.environ.pop("SPRITE_LAB_DRAIN_TIMEOUT", None)


if __name__ == "__main__":
    unittest.main()
