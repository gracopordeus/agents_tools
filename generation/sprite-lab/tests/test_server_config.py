import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

import server  # noqa: E402


class ServerGeminiConfigTests(unittest.TestCase):
    def test_local_key_is_private_and_never_returned_by_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "GEMINI_CONFIG_PATH", Path(temporary) / "gemini_config.json"
        ), patch.dict(os.environ, {"GOOGLE_API_KEY": "", "GEMINI_API_KEY": ""}, clear=False):
            result = server.save_gemini_api_key("test-gemini-key")

            self.assertEqual(result["source"], "local")
            self.assertTrue(result["configured"])
            self.assertNotIn("api_key", result)
            self.assertEqual(server.gemini_api_key(), "test-gemini-key")
            mode = stat.S_IMODE(server.GEMINI_CONFIG_PATH.stat().st_mode)
            self.assertEqual(mode, 0o600)

            cleared = server.save_gemini_api_key("")
            self.assertFalse(cleared["configured"])
            self.assertFalse(server.GEMINI_CONFIG_PATH.exists())

    def test_environment_key_is_detected_without_persisting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "GEMINI_CONFIG_PATH", Path(temporary) / "gemini_config.json"
        ), patch.dict(
            os.environ,
            {"GOOGLE_API_KEY": "", "GEMINI_API_KEY": "environment-key"},
            clear=False,
        ):
            result = server.gemini_config_status()

            self.assertEqual(result, {"configured": True, "source": "environment", "updated_at": None})
            self.assertEqual(server.gemini_api_key(), "environment-key")
            self.assertFalse(server.GEMINI_CONFIG_PATH.exists())


class ServerQwenConfigTests(unittest.TestCase):
    def test_local_key_is_private_and_never_returned_by_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "QWEN_CONFIG_PATH", Path(temporary) / "qwen_config.json"
        ), patch.dict(
            os.environ,
            {"DASHSCOPE_API_KEY": "", "QWEN_API_KEY": ""},
            clear=False,
        ):
            result = server.save_qwen_api_key(" test-\nqwen-key ")

            self.assertEqual(result["source"], "local")
            self.assertTrue(result["configured"])
            self.assertNotIn("api_key", result)
            self.assertEqual(server.qwen_api_key(), "test-qwen-key")
            mode = stat.S_IMODE(server.QWEN_CONFIG_PATH.stat().st_mode)
            self.assertEqual(mode, 0o600)

            cleared = server.save_qwen_api_key("")
            self.assertFalse(cleared["configured"])
            self.assertFalse(server.QWEN_CONFIG_PATH.exists())

    def test_environment_key_is_detected_without_persisting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "QWEN_CONFIG_PATH", Path(temporary) / "qwen_config.json"
        ), patch.dict(
            os.environ,
            {"DASHSCOPE_API_KEY": "environment-key", "QWEN_API_KEY": ""},
            clear=False,
        ):
            result = server.qwen_config_status()

            self.assertEqual(result, {"configured": True, "source": "environment", "updated_at": None})
            self.assertEqual(server.qwen_api_key(), "environment-key")
            self.assertFalse(server.QWEN_CONFIG_PATH.exists())


class ServerOpenAIConfigTests(unittest.TestCase):
    def test_local_key_is_private_and_never_returned_by_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "OPENAI_CONFIG_PATH", Path(temporary) / "openai_config.json"
        ), patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = server.save_openai_api_key("test-openai-key")

            self.assertEqual(result["source"], "local")
            self.assertTrue(result["configured"])
            self.assertNotIn("api_key", result)
            self.assertEqual(server.openai_api_key(), "test-openai-key")
            mode = stat.S_IMODE(server.OPENAI_CONFIG_PATH.stat().st_mode)
            self.assertEqual(mode, 0o600)

            cleared = server.save_openai_api_key("")
            self.assertFalse(cleared["configured"])
            self.assertFalse(server.OPENAI_CONFIG_PATH.exists())

    def test_environment_key_is_detected_without_persisting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server, "OPENAI_CONFIG_PATH", Path(temporary) / "openai_config.json"
        ), patch.dict(os.environ, {"OPENAI_API_KEY": "environment-key"}, clear=False):
            result = server.openai_config_status()

            self.assertEqual(result, {"configured": True, "source": "environment", "updated_at": None})
            self.assertEqual(server.openai_api_key(), "environment-key")
            self.assertFalse(server.OPENAI_CONFIG_PATH.exists())


class ServerHuggingFaceConfigTests(unittest.TestCase):
    def test_local_token_is_private_and_never_returned_by_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server.huggingface_realesrgan,
            "HF_CONFIG_PATH",
            Path(temporary) / "huggingface_config.json",
        ), patch.dict(
            os.environ,
            {"HF_TOKEN": "", "HUGGINGFACEHUB_API_TOKEN": ""},
            clear=False,
        ):
            result = server.huggingface_realesrgan.save_api_token("hf_test-token")

            self.assertEqual(result["source"], "local")
            self.assertTrue(result["configured"])
            self.assertNotIn("api_key", result)
            self.assertEqual(server.huggingface_realesrgan.api_token(), "hf_test-token")
            mode = stat.S_IMODE(server.huggingface_realesrgan.HF_CONFIG_PATH.stat().st_mode)
            self.assertEqual(mode, 0o600)

            cleared = server.huggingface_realesrgan.save_api_token("")
            self.assertFalse(cleared["configured"])
            self.assertFalse(server.huggingface_realesrgan.HF_CONFIG_PATH.exists())

    def test_environment_token_is_detected_without_persisting_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            server.huggingface_realesrgan,
            "HF_CONFIG_PATH",
            Path(temporary) / "huggingface_config.json",
        ), patch.dict(
            os.environ,
            {"HF_TOKEN": "hf_environment-token", "HUGGINGFACEHUB_API_TOKEN": ""},
            clear=False,
        ):
            result = server.huggingface_realesrgan.config_status()

            self.assertEqual(result, {"configured": True, "source": "environment", "updated_at": None})
            self.assertEqual(server.huggingface_realesrgan.api_token(), "hf_environment-token")
            self.assertFalse(server.huggingface_realesrgan.HF_CONFIG_PATH.exists())


if __name__ == "__main__":
    unittest.main()
