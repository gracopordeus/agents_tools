import base64
import sys
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SPRITE_LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SPRITE_LAB))

from image_generation_provider import (  # noqa: E402
    GenerationRequest,
    GoogleImageProvider,
    OpenAIImageProvider,
    QwenImageProvider,
    _create_openai_cell_mask,
    _qwen_api_base_url,
    create_provider,
)


class FakeImages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.kwargs: dict[str, object] | None = None

    def edit(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return self.response


class FakeOpenAIClient:
    images: FakeImages

    def __init__(self, response: object, api_key: str) -> None:
        self.api_key = api_key
        self.images = FakeImages(response)


class ImageGenerationProviderTests(unittest.TestCase):
    def test_openai_cell_mask_uses_black_guard_bands_and_transparent_cells(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reference = root / "reference.png"
            mask = root / "mask.png"
            Image.new("RGB", (256, 256), (20, 40, 60)).save(reference, format="PNG")

            _create_openai_cell_mask(reference, mask)

            with Image.open(mask) as generated:
                self.assertEqual(generated.mode, "RGBA")
                self.assertEqual(generated.size, (256, 256))
                self.assertEqual(generated.getpixel((0, 0)), (0, 0, 0, 255))
                self.assertEqual(generated.getpixel((16, 16)), (0, 0, 0, 0))

    def test_qwen_token_plan_key_uses_token_plan_endpoint(self) -> None:
        self.assertEqual(
            _qwen_api_base_url("sk-sp-token-plan-key"),
            "https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1",
        )
        self.assertEqual(
            _qwen_api_base_url("sk-ws-payg-key"),
            "https://dashscope-intl.aliyuncs.com/api/v1",
        )

    def test_openai_adapter_sends_identity_and_structural_images(self) -> None:
        image_bytes = BytesIO()
        Image.new("RGBA", (4, 4), (20, 40, 60, 255)).save(image_bytes, format="PNG")
        response = types.SimpleNamespace(
            data=[
                types.SimpleNamespace(
                    b64_json=base64.b64encode(image_bytes.getvalue()).decode()
                )
            ]
        )
        fake_clients: list[FakeOpenAIClient] = []

        def fake_constructor(*, api_key: str) -> FakeOpenAIClient:
            client = FakeOpenAIClient(response, api_key)
            fake_clients.append(client)
            return client

        fake_openai = types.ModuleType("openai")
        fake_openai.OpenAI = fake_constructor  # type: ignore[attr-defined]
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            sys.modules, {"openai": fake_openai}
        ):
            root = Path(temporary)
            refs = []
            for index in range(2):
                path = root / f"ref{index}.png"
                path.write_bytes(image_bytes.getvalue())
                refs.append(path)
            output = root / "f00.png"
            request = GenerationRequest(
                job_id="job-f00",
                prompt="Transform the character.",
                input_images=tuple(refs),
                output_path=output,
                model="gpt-image-2",
            )

            result = OpenAIImageProvider(api_key="test-key").generate(request)

            self.assertEqual(result.status, "ok")
            self.assertTrue(output.is_file())
            self.assertEqual(len(fake_clients), 1)
            assert fake_clients[0].images.kwargs is not None
            self.assertIsInstance(fake_clients[0].images.kwargs["image"], list)
            self.assertEqual(fake_clients[0].images.kwargs["model"], "gpt-image-2")
            self.assertEqual(fake_clients[0].images.kwargs["background"], "transparent")
            self.assertEqual(fake_clients[0].images.kwargs["output_format"], "png")
            self.assertEqual(fake_clients[0].images.kwargs["size"], "2048x2048")
            self.assertFalse((root / "openai_cell_mask.png").exists())
            self.assertNotIn("mask", fake_clients[0].images.kwargs)
            self.assertNotIn("input_fidelity", fake_clients[0].images.kwargs)

    def test_google_adapter_requests_a_2k_square_image(self) -> None:
        image_bytes = BytesIO()
        Image.new("RGB", (4, 4), (20, 40, 60)).save(image_bytes, format="PNG")
        captured: dict[str, object] = {}

        class FakePart:
            @staticmethod
            def from_bytes(*, data: bytes, mime_type: str) -> object:
                captured.setdefault("parts", []).append((data, mime_type))  # type: ignore[union-attr]
                return types.SimpleNamespace(inline_data=None)

        class FakeImageConfig:
            def __init__(self, **kwargs: object) -> None:
                captured["image_config"] = kwargs

        class FakeGenerateContentConfig:
            def __init__(self, **kwargs: object) -> None:
                captured["config"] = kwargs

        class FakeModels:
            def generate_content(self, **kwargs: object) -> object:
                captured["request"] = kwargs
                return types.SimpleNamespace(
                    candidates=[
                        types.SimpleNamespace(
                            content=types.SimpleNamespace(
                                parts=[
                                    types.SimpleNamespace(
                                        inline_data=types.SimpleNamespace(data=image_bytes.getvalue())
                                    )
                                ]
                            )
                        )
                    ]
                )

        class FakeClient:
            def __init__(self, *, api_key: str) -> None:
                captured["api_key"] = api_key
                self.models = FakeModels()

        fake_types = types.ModuleType("google.genai.types")
        fake_types.Part = FakePart  # type: ignore[attr-defined]
        fake_types.ImageConfig = FakeImageConfig  # type: ignore[attr-defined]
        fake_types.GenerateContentConfig = FakeGenerateContentConfig  # type: ignore[attr-defined]
        fake_genai = types.ModuleType("google.genai")
        fake_genai.Client = FakeClient  # type: ignore[attr-defined]
        fake_genai.types = fake_types  # type: ignore[attr-defined]
        fake_google = types.ModuleType("google")
        fake_google.genai = fake_genai  # type: ignore[attr-defined]

        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            sys.modules,
            {"google": fake_google, "google.genai": fake_genai, "google.genai.types": fake_types},
        ):
            root = Path(temporary)
            reference = root / "reference.png"
            reference.write_bytes(image_bytes.getvalue())
            output = root / "generated.png"
            request = GenerationRequest(
                job_id="job-google",
                prompt="Generate the sprite sheet.",
                input_images=(reference,),
                output_path=output,
                model="gemini-3.1-flash-image",
                metadata={"gemini_temperature": 0.7, "gemini_top_k": 32},
            )

            result = GoogleImageProvider(api_key="test-key").generate(request)

            self.assertEqual(result.status, "ok")
            self.assertTrue(output.is_file())
            self.assertEqual(captured["api_key"], "test-key")
            self.assertEqual(captured["config"]["response_modalities"], ["IMAGE"])  # type: ignore[index]
            self.assertEqual(captured["config"]["temperature"], 0.7)  # type: ignore[index]
            self.assertEqual(captured["config"]["top_k"], 32)  # type: ignore[index]
            self.assertIsInstance(captured["config"]["image_config"], FakeImageConfig)  # type: ignore[index]
            self.assertEqual(captured["image_config"], {"aspect_ratio": "1:1", "image_size": "2K"})
            self.assertEqual(captured["request"]["model"], "gemini-3.1-flash-image")  # type: ignore[index]

    def test_qwen_adapter_sends_selected_api_inputs_and_downloads_output(self) -> None:
        image_bytes = BytesIO()
        Image.new("RGB", (8, 8), (20, 40, 60)).save(image_bytes, format="PNG")
        output_bytes = image_bytes.getvalue()
        captured: dict[str, object] = {}

        class FakeConversation:
            @staticmethod
            def call(**kwargs: object) -> object:
                captured["request"] = kwargs
                return types.SimpleNamespace(
                    status_code=200,
                    request_id="qwen-request",
                    usage={"input_image_count": 3, "output_width": 2048},
                    output=types.SimpleNamespace(
                        choices=[
                            types.SimpleNamespace(
                                message=types.SimpleNamespace(
                                    content=[{"image": "https://qwen.test/output.png"}]
                                )
                            )
                        ]
                    ),
                )

        fake_dashscope = types.ModuleType("dashscope")
        fake_dashscope.MultiModalConversation = FakeConversation  # type: ignore[attr-defined]
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            sys.modules, {"dashscope": fake_dashscope}
        ), patch(
            "image_generation_provider._download_image", return_value=output_bytes
        ):
            root = Path(temporary)
            refs = []
            for index in range(3):
                path = root / f"ref{index}.png"
                path.write_bytes(output_bytes)
                refs.append(path)
            output = root / "generated.png"
            request = GenerationRequest(
                job_id="job-qwen",
                prompt="Generate the sprite sheet.",
                input_images=tuple(refs),
                output_path=output,
                model="qwen-image-3.0-pro",
                metadata={"output_size": [2048, 2048]},
            )

            result = QwenImageProvider(api_key="test-qwen-key").generate(request)

            self.assertEqual(result.status, "ok")
            self.assertTrue(output.is_file())
            self.assertEqual(result.response_metadata["input_image_count"], 3)
            self.assertEqual(result.response_metadata["original_input_image_count"], 3)
            call = captured["request"]
            self.assertEqual(call["api_key"], "test-qwen-key")  # type: ignore[index]
            self.assertEqual(call["model"], "qwen-image-3.0-pro")  # type: ignore[index]
            self.assertEqual(call["size"], "2048*2048")  # type: ignore[index]
            self.assertFalse(call["prompt_extend"])  # type: ignore[index]
            self.assertFalse(call["watermark"])  # type: ignore[index]
            content = call["messages"][0]["content"]  # type: ignore[index]
            self.assertEqual(len(content), 4)
            self.assertTrue(all(item["image"].startswith("data:image/png;base64,") for item in content[:3]))
            self.assertEqual(content[-1]["text"], "Generate the sprite sheet.")

    def test_qwen_adapter_rejects_more_than_three_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for index in range(4):
                path = root / f"ref{index}.png"
                path.write_bytes(b"not-used")
                paths.append(path)
            request = GenerationRequest(
                job_id="job-qwen-too-many",
                prompt="Generate the sprite sheet.",
                input_images=tuple(paths),
                output_path=root / "generated.png",
                model="qwen-image-3.0-pro",
            )
            with self.assertRaisesRegex(ValueError, "no máximo três imagens"):
                QwenImageProvider(api_key="test-qwen-key")._input_paths(request)

    def test_qwen_factory_does_not_need_to_import_the_sdk(self) -> None:
        with patch.dict(sys.modules, {"dashscope": None}), patch.dict(
            "os.environ", {"DASHSCOPE_API_KEY": "test-qwen-key"}
        ):
            provider = create_provider("qwen")
        self.assertIsInstance(provider, QwenImageProvider)


if __name__ == "__main__":
    unittest.main()
