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
            self.assertEqual(fake_clients[0].images.kwargs["input_fidelity"], "high")

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
            )

            result = GoogleImageProvider(api_key="test-key").generate(request)

            self.assertEqual(result.status, "ok")
            self.assertTrue(output.is_file())
            self.assertEqual(captured["api_key"], "test-key")
            self.assertEqual(captured["config"]["response_modalities"], ["IMAGE"])  # type: ignore[index]
            self.assertIsInstance(captured["config"]["image_config"], FakeImageConfig)  # type: ignore[index]
            self.assertEqual(captured["image_config"], {"aspect_ratio": "1:1", "image_size": "2K"})
            self.assertEqual(captured["request"]["model"], "gemini-3.1-flash-image")  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
