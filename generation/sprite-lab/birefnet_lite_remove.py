"""Remove backgrounds with official BiRefNet-Lite using binary alpha masks."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torchvision import transforms
from transformers import AutoModelForImageSegmentation


def _decontaminate(
    rgb: np.ndarray,
    soft_alpha: np.ndarray,
    binary_alpha: np.ndarray,
) -> np.ndarray:
    """Undo estimated background blending while preserving binary alpha."""
    border = np.concatenate(
        [rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]],
        axis=0,
    ).astype(np.float32)
    background = np.median(border, axis=0)
    alpha = np.clip(soft_alpha.astype(np.float32), 0.08, 1.0)[..., None]
    reconstructed = (rgb.astype(np.float32) - (1.0 - alpha) * background) / alpha
    edge_band = binary_alpha & (soft_alpha < 0.98)
    result = rgb.copy()
    result[edge_band] = np.clip(reconstructed[edge_band], 0, 255).astype(np.uint8)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mask-output", type=Path, required=True)
    parser.add_argument("--model", default="ZhengPeng7/BiRefNet_lite")
    parser.add_argument(
        "--revision",
        default="7838f1c3472f827cd8ce13ab5ccc2ce48077360f",
    )
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--input-size", type=int, default=1024)
    args = parser.parse_args()

    if not args.source.is_dir():
        raise FileNotFoundError(args.source)
    if not 0.0 <= args.threshold <= 1.0:
        raise ValueError("threshold deve estar entre 0 e 1")

    torch.set_float32_matmul_precision("high")
    model = AutoModelForImageSegmentation.from_pretrained(
        args.model,
        trust_remote_code=True,
        revision=args.revision,
    ).to("cpu")
    model.eval()
    transform = transforms.Compose(
        [
            transforms.Resize((args.input_size, args.input_size)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )
    args.output.mkdir(parents=True, exist_ok=True)
    args.mask_output.mkdir(parents=True, exist_ok=True)
    inputs = sorted(args.source.glob("row*_col*.png"))
    started = time.monotonic()

    for source in inputs:
        with Image.open(source) as opened:
            image = opened.convert("RGB")
        tensor = transform(image).unsqueeze(0).to("cpu", dtype=torch.float32)
        with torch.inference_mode():
            prediction = model(tensor)[-1].sigmoid()[0].squeeze().cpu().numpy()
        soft_mask = Image.fromarray(
            np.round(prediction * 255).astype(np.uint8), mode="L"
        ).resize(image.size, Image.Resampling.NEAREST)
        soft_alpha = np.asarray(soft_mask, dtype=np.float32) / 255.0
        binary_alpha = soft_alpha >= args.threshold
        rgb = np.asarray(image, dtype=np.uint8)
        cleaned_rgb = _decontaminate(rgb, soft_alpha, binary_alpha)
        rgba = np.dstack(
            [cleaned_rgb, np.where(binary_alpha, 255, 0).astype(np.uint8)]
        )
        Image.fromarray(rgba, mode="RGBA").save(args.output / source.name)
        Image.fromarray(
            np.where(binary_alpha, 255, 0).astype(np.uint8), mode="L"
        ).save(args.mask_output / source.name)
        image.close()
        soft_mask.close()

    print(
        json.dumps(
            {
                "implementation": "ZhengPeng7/BiRefNet",
                "model": args.model,
                "revision": args.revision,
                "device": "cpu",
                "precision": "fp32",
                "input_size": [args.input_size, args.input_size],
                "mask_resize": "nearest-neighbor",
                "alpha": "binary",
                "threshold": args.threshold,
                "color_decontamination": True,
                "images": len(inputs),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
