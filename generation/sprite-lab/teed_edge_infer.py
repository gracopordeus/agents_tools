"""Run the official TEED model on a directory of image cells, on CPU."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Keep this module CPU-only even when the installed PyTorch wheel contains CUDA.
os.environ["CUDA_VISIBLE_DEVICES"] = ""

import numpy as np
from PIL import Image


def infer_directory(
    input_dir: Path,
    output_dir: Path,
    *,
    teed_repo: Path,
    checkpoint: Path,
    batch_size: int = 8,
    threads: int = 0,
) -> dict[str, object]:
    import torch

    if threads > 0:
        torch.set_num_threads(threads)
    sys.path.insert(0, str(teed_repo.resolve()))
    from ted import TED

    device = torch.device("cpu")
    model = TED().to(device)
    checkpoint_data = torch.load(checkpoint, map_location=device)
    state = (
        checkpoint_data.get("state_dict", checkpoint_data)
        if isinstance(checkpoint_data, dict)
        else checkpoint_data
    )
    model.load_state_dict(state)
    model.eval()
    paths = sorted(input_dir.glob("*.png"))
    if not paths:
        raise ValueError(f"nenhuma célula PNG em {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    mean_bgr = np.asarray([103.939, 116.779, 123.68], dtype=np.float32)
    start = time.perf_counter()
    with torch.inference_mode():
        for offset in range(0, len(paths), max(1, batch_size)):
            batch_paths = paths[offset : offset + max(1, batch_size)]
            tensors = []
            sizes = []
            for path in batch_paths:
                with Image.open(path) as opened:
                    rgb = np.asarray(opened.convert("RGB"), dtype=np.float32)
                sizes.append((rgb.shape[1], rgb.shape[0]))
                bgr = rgb[:, :, ::-1].copy()
                bgr -= mean_bgr
                tensors.append(torch.from_numpy(bgr.transpose(2, 0, 1)).float())
            tensor = torch.stack(tensors, dim=0).to(device)
            prediction = model(tensor)[-1]
            probabilities = torch.sigmoid(prediction).cpu().numpy()[:, 0]
            for path, probability, size in zip(batch_paths, probabilities, sizes):
                edge = np.clip(np.rint(probability * 255.0), 0, 255).astype(np.uint8)
                edge_image = Image.fromarray(edge, "L")
                if edge_image.size != size:
                    edge_image = edge_image.resize(size, Image.Resampling.BILINEAR)
                edge_image.save(output_dir / path.name, format="PNG")
                edge_image.close()
    return {
        "schema": "generation.teed_inference/v1",
        "device": str(device),
        "cuda_available": bool(torch.cuda.is_available()),
        "torch_version": torch.__version__,
        "parameter_count": sum(parameter.numel() for parameter in model.parameters()),
        "checkpoint": str(checkpoint.resolve()),
        "input_count": len(paths),
        "batch_size": max(1, batch_size),
        "threads": threads if threads > 0 else "torch-default",
        "elapsed_seconds": round(time.perf_counter() - start, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--teed-repo", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--threads", type=int, default=0)
    args = parser.parse_args(argv)
    report = infer_directory(
        args.input_dir,
        args.output_dir,
        teed_repo=args.teed_repo,
        checkpoint=args.checkpoint,
        batch_size=args.batch_size,
        threads=args.threads,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
