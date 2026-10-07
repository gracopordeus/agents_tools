import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chatgpt_bridge
from chatgpt_bridge import ChatGPTBridgeProvider, LocalAppBridge, _bridge_artifact, _find_generated_image
from image_generation_provider import GenerationRequest, create_provider
from st08_local_broker import BrokerState, serve


class BridgeTests(unittest.TestCase):
    def request(self, root):
        reference = root / "reference.png"
        Image.new("RGBA", (32, 32), (1, 2, 3, 0)).save(reference)
        return GenerationRequest("test", "Keep identity", (reference,), root / "output.png", chatgpt_bridge.CHATGPT_TASK_MODEL, {"output_size": [1024, 1024]})

    def test_factory(self):
        self.assertIsInstance(create_provider("chatgpt-local"), ChatGPTBridgeProvider)

    def test_finds_image_generation_payload(self):
        self.assertEqual(
            _find_generated_image({"turns": [{"items": [{"type": "imageGeneration", "result": "YWJj"}]}]}),
            "YWJj",
        )

    def test_original_preserved_and_size_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            bridge = LocalAppBridge("test", "caller")
            def dispatch(tool, args):
                self.assertEqual(tool, "create_thread")
                self.assertIn(str(request.input_images[0]), args["prompt"])
                self.assertEqual(args["model"], chatgpt_bridge.CHATGPT_TASK_MODEL)
                self.assertEqual(args["thinking"], chatgpt_bridge.CHATGPT_TASK_THINKING)
                return {"threadId": "test-child"}
            def poll(tool, args):
                self.assertEqual(tool, "read_thread")
                source = root / "source.png"
                Image.new("RGBA", (128, 128), (1, 2, 3, 0)).save(source)
                return {"thread": {"status": {"type": "idle"}}, "turns": [{"items": [{"type": "imageGeneration", "savedPath": str(source)}]}]}
            def fake_swinir(source, destination):
                with Image.open(source) as resized:
                    self.assertEqual(resized.size, (1024, 1024))
                Image.new("RGB", (2048, 2048), (4, 5, 6)).save(destination)
                return {"backend": "SwinIR", "profile": "test"}
            with patch.object(LocalAppBridge, "discover", return_value=bridge), patch.object(bridge, "call", side_effect=[dispatch("create_thread", {"prompt": str(request.input_images[0]), "model": chatgpt_bridge.CHATGPT_TASK_MODEL, "thinking": chatgpt_bridge.CHATGPT_TASK_THINKING}), poll("read_thread", {})]), patch.object(chatgpt_bridge, "_run_swinir", side_effect=fake_swinir):
                result = ChatGPTBridgeProvider().generate(request)
            self.assertTrue(result.response_metadata["resized"])
            with Image.open(result.output_path) as image:
                self.assertEqual(image.size, (1024, 1024))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getchannel("A").getextrema(), (0, 0))
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
            with Image.open(root / "chatgpt_original.png") as image:
                self.assertEqual(image.size, (128, 128))
            self.assertEqual(json.loads((root / "chatgpt_bridge.json").read_text())["status"], "done")
            self.assertEqual(result.model, chatgpt_bridge.CHATGPT_TASK_MODEL)
            self.assertEqual(result.response_metadata["task_thinking"], chatgpt_bridge.CHATGPT_TASK_THINKING)

    def test_uncertain_dispatch_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = LocalAppBridge("test", "caller")
            request = self.request(root)
            with patch.object(LocalAppBridge, "discover", return_value=bridge), patch.object(bridge, "call", side_effect=TimeoutError("timeout")) as call:
                with self.assertRaisesRegex(RuntimeError, "não confirmado"):
                    ChatGPTBridgeProvider().generate(request)
                self.assertEqual(call.call_count, 1)
            self.assertEqual(json.loads((root / "chatgpt_bridge.json").read_text())["status"], "dispatch_uncertain")

    def test_non_square_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = self.request(root)
            bridge = LocalAppBridge("test", "caller")
            def dispatch(*args):
                return {"threadId": "child"}
            def poll(*args):
                source = root / "source.png"
                Image.new("RGB", (128, 64)).save(source)
                return {"thread": {"status": {"type": "idle"}}, "turns": [{"items": [{"type": "imageGeneration", "savedPath": str(source)}]}]}
            with patch.object(LocalAppBridge, "discover", return_value=bridge), patch.object(bridge, "call", side_effect=[dispatch(), poll()]):
                with self.assertRaisesRegex(RuntimeError, "quadrada"):
                    ChatGPTBridgeProvider().generate(request)
            self.assertFalse(request.output_path.exists())

    def test_local_broker_serves_generated_layers_and_replays_create_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            socket_path = root / "bridge.sock"
            character = root / "character.png"
            weapon = root / "weapon.png"
            Image.new("RGB", (64, 64), (10, 20, 30)).save(character)
            Image.new("RGB", (64, 64), (40, 50, 60)).save(weapon)
            state = BrokerState(root / "broker_receipts.json", character, weapon)
            ready = threading.Event()
            thread = threading.Thread(
                target=serve,
                args=(socket_path, state),
                kwargs={"ready": ready, "max_connections": 5},
                daemon=True,
            )
            thread.start()
            self.assertTrue(ready.wait(2))
            bridge = LocalAppBridge(str(socket_path), "caller")
            catalog = bridge.rpc("tools/list", {"threadStartKind": "all"})
            self.assertEqual({tool["name"] for tool in catalog["tools"]}, {"create_thread", "read_thread"})
            arguments = {
                "title": "Generation AI Render job-character",
                "prompt": "CHARACTER LAYER CONTRACT — REQUIRED. Preserve identity.",
            }
            first = bridge.call("create_thread", arguments)
            replay = bridge.call("create_thread", arguments)
            self.assertEqual(first, replay)
            snapshot = bridge.call("read_thread", {"threadId": first["threadId"]})
            self.assertEqual(_find_generated_image(snapshot), str(character.resolve()))
            persisted = json.loads((root / "broker_receipts.json").read_text())
            self.assertEqual(len(persisted["threads"]), 1)

    def test_local_broker_prefers_explicit_character_contract_over_weapon_mentions(self):
        arguments = {
            "prompt": (
                "CHARACTER LAYER CONTRACT — REQUIRED\n"
                "The source contract includes a weapon component and weapon layer holdout."
            )
        }
        self.assertEqual(BrokerState._role(arguments), "character")

    def test_layered_requests_use_distinct_bridge_receipts(self):
        output = Path("/tmp/job/.character_full.provider.png")
        self.assertEqual(
            _bridge_artifact(output, "character", "bridge.json").name,
            "character_full.chatgpt_bridge.json",
        )
        self.assertEqual(
            _bridge_artifact(output, "weapon", "bridge.json").name,
            "weapon_full.chatgpt_bridge.json",
        )


if __name__ == "__main__":
    unittest.main()
