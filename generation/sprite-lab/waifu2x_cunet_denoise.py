"""Run the official Waifu2x CUNet denoiser at 1x on a folder of PNGs."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--nunif-repo", type=Path, required=True)
    parser.add_argument("--noise-level", type=int, choices=range(4), default=1)
    parser.add_argument("--tile-size", type=int, default=256)
    args = parser.parse_args()

    if not args.source.is_dir():
        raise FileNotFoundError(args.source)
    if not (args.nunif_repo / "waifu2x" / "hub.py").is_file():
        raise FileNotFoundError(f"repositório nunif inválido: {args.nunif_repo}")

    sys.path.insert(0, str(args.nunif_repo.resolve()))
    from waifu2x.hub import waifu2x  # noqa: PLC0415

    model = waifu2x(
        model_type="cunet/art",
        method="noise",
        noise_level=args.noise_level,
        device_ids=[-1],
        tile_size=args.tile_size,
        batch_size=1,
        amp=False,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = sorted(args.source.glob("*.png"))
    started = time.monotonic()
    for source in inputs:
        with Image.open(source) as opened:
            original_size = opened.size
            result = model.infer(opened.convert("RGB"))
        if result.size != original_size:
            raise RuntimeError(
                f"Waifu2x alterou {source.name} de {original_size} para {result.size}"
            )
        result.save(args.output / source.name, format="PNG")
        result.close()

    report = {
        "implementation": "nagadomi/nunif",
        "model": "cunet/art",
        "method": "noise",
        "noise_level": args.noise_level,
        "scale": 1,
        "device": "cpu",
        "precision": "fp32",
        "tile_size": args.tile_size,
        "images": len(inputs),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
