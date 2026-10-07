"""Generate a ControlNet line/edge guide for an identity reference."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from postprocess_runtime import add_runtime_arguments, resolve_device


IDENTITY_GUIDE_MODES = ("lineart_standard", "canny_edges")
CANNY_LOW_THRESHOLD = 100
CANNY_HIGH_THRESHOLD = 200


def _as_image(value: Image.Image | np.ndarray) -> Image.Image:
    if isinstance(value, Image.Image):
        return value.convert("RGB")
    array = np.asarray(value)
    if array.ndim == 2:
        return Image.fromarray(array.astype(np.uint8), mode="L").convert("RGB")
    return Image.fromarray(array.astype(np.uint8), mode="RGB")


def build_identity_lineart(
    source: Path,
    output: Path,
    *,
    device: str = "auto",
    mode: str = "lineart_standard",
) -> dict[str, object]:
    """Run the selected detector and save a bright-on-black L guide."""
    if mode not in IDENTITY_GUIDE_MODES:
        raise ValueError(
            f"modo de guia de identidade inválido: {mode!r}; "
            f"use um de {', '.join(IDENTITY_GUIDE_MODES)}"
        )
    if not source.is_file():
        raise FileNotFoundError(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    if mode == "lineart_standard":
        from controlnet_aux import LineartDetector
    else:
        from controlnet_aux import CannyDetector

    with Image.open(source) as opened:
        source_size = opened.size
        rgba = opened.convert("RGBA")
    # A transparent concept must still provide a stable matte to the
    # annotator. The output remains a conventional grayscale ControlNet guide.
    matte = Image.new("RGBA", source_size, (255, 255, 255, 255))
    matte.alpha_composite(rgba)
    rgba.close()
    image = matte.convert("RGB")
    matte.close()
    if not source_size[0] or not source_size[1]:
        image.close()
        raise ValueError("a referência de identidade precisa ter dimensões válidas")

    detector_resolution = min(max(source_size), 1024)
    if mode == "lineart_standard":
        resolved_device = resolve_device(device)
        detector = LineartDetector.from_pretrained("lllyasviel/Annotators").to(resolved_device)
        detected = detector(
            image,
            detect_resolution=detector_resolution,
            image_resolution=detector_resolution,
            output_type="pil",
        )
        detector_name = "controlnet_aux.LineartDetector"
    else:
        # Keep this identical to the concept benchmark: Canny is a CPU
        # baseline and does not require an annotator checkpoint.
        resolved_device = "cpu"
        detector = CannyDetector()
        detected = detector(
            image,
            low_threshold=CANNY_LOW_THRESHOLD,
            high_threshold=CANNY_HIGH_THRESHOLD,
            detect_resolution=detector_resolution,
            image_resolution=detector_resolution,
            output_type="pil",
        )
        detector_name = "controlnet_aux.CannyDetector"
    image.close()
    try:
        detected_image = _as_image(detected)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{detector_name} não retornou uma imagem") from error

    grayscale = np.asarray(detected_image.convert("L"), dtype=np.uint8)
    detected_image.close()
    # Normalize the detector polarity to the same bright-line convention used
    # by the structural lineart channel in Sprite Lab.
    if float(grayscale.mean()) > 127.0:
        grayscale = 255 - grayscale
    lineart = Image.fromarray(grayscale, mode="L")
    if lineart.size != source_size:
        resized = lineart.resize(source_size, Image.Resampling.LANCZOS)
        lineart.close()
        lineart = resized
    lineart.save(output, format="PNG")
    lineart.close()
    report: dict[str, object] = {
        "mode": mode,
        "detector": detector_name,
        "device": resolved_device,
        "source_size": list(source_size),
        "detector_resolution": detector_resolution,
        "output_size": list(source_size),
        "output_mode": "L",
        "polarity": "bright_lines_on_black",
    }
    if mode == "lineart_standard":
        report["annotator"] = "lllyasviel/Annotators"
    else:
        report["low_threshold"] = CANNY_LOW_THRESHOLD
        report["high_threshold"] = CANNY_HIGH_THRESHOLD
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(parser)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mode", choices=IDENTITY_GUIDE_MODES, default="lineart_standard")
    args = parser.parse_args()
    report = build_identity_lineart(args.source, args.output, device=args.device, mode=args.mode)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
