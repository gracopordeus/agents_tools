"""Infer Sprite Lab structural channels from Blender beauty cells.

Blender remains the source of truth for the rendered pose and composition. The
ControlNet annotators are run afterwards, once per cell, so the final structural
guides are produced by the same local models used by the AI pipeline:

* ``hed_softedge`` for the lineart channel;
* body-only ``openpose`` for the bones channel.

The worker deliberately loads both models once and writes the original row /
column grid unchanged. It does not run SD generation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import os
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from postprocess_runtime import GPULease, resolve_device


ANNOTATOR_REPO = "lllyasviel/Annotators"
OPENPOSE_REPO = "lllyasviel/ControlNet"
OPENPOSE_BODY_FILE = "annotator/ckpts/body_pose_model.pth"
CONTROLNET_REPORT = "controlnet_channels.json"


def _as_rgb_on_white(path: Path) -> Image.Image:
    """Return a detector input with transparent Blender background matted."""
    with Image.open(path) as opened:
        rgba = opened.convert("RGBA")
    background = Image.new("RGB", rgba.size, (255, 255, 255))
    background.paste(rgba, mask=rgba.getchannel("A"))
    rgba.close()
    return background


def _as_gray(value: Image.Image | np.ndarray) -> Image.Image:
    if isinstance(value, Image.Image):
        return value.convert("L")
    array = np.asarray(value)
    if array.ndim == 3:
        array = np.asarray(Image.fromarray(array.astype(np.uint8)).convert("L"))
    return Image.fromarray(array.astype(np.uint8), mode="L")


def _canonical_softedge(value: Image.Image | np.ndarray) -> Image.Image:
    """Normalize HED to the white-line convention used by Sprite Lab."""
    gray = np.asarray(_as_gray(value), dtype=np.uint8)
    # controlnet_aux returns white edges on black for hed_softedge. Keep a
    # defensive polarity normalization for local annotator revisions that
    # return a white matte instead.
    if float(gray.mean()) > 127.0:
        gray = 255 - gray
    return Image.fromarray(gray, mode="L")


def _save_lineart(value: Image.Image | np.ndarray, path: Path) -> None:
    """Save soft edges as white RGBA lines with edge strength as alpha."""
    line = _canonical_softedge(value)
    alpha = np.asarray(line, dtype=np.uint8)
    rgba = np.full((line.height, line.width, 4), 255, dtype=np.uint8)
    rgba[:, :, 3] = alpha
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path, format="PNG")
    line.close()


def _save_openpose(value: Image.Image | np.ndarray, path: Path) -> None:
    """Save the detector's original COCO18 RGB color encoding."""
    if isinstance(value, Image.Image):
        pose = value.convert("RGB")
    else:
        pose = Image.fromarray(np.asarray(value).astype(np.uint8)).convert("RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    pose.save(path, format="PNG")
    pose.close()


def _openpose_body_model(cache_dir: str | None) -> str:
    configured = os.environ.get("SPRITE_LAB_OPENPOSE_BODY_MODEL", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"modelo OpenPose não encontrado: {path}")
        return str(path)

    from huggingface_hub import hf_hub_download

    return hf_hub_download(
        OPENPOSE_REPO,
        OPENPOSE_BODY_FILE,
        cache_dir=cache_dir,
        local_files_only=True,
    )


def _load_detectors(device: str, cache_dir: str | None) -> tuple[Any, Any]:
    """Load HED softedge and body-only OpenPose without face/hand checkpoints."""
    from controlnet_aux import HEDdetector
    from controlnet_aux.open_pose import Body, OpenposeDetector

    hed = HEDdetector.from_pretrained(
        ANNOTATOR_REPO,
        cache_dir=cache_dir,
        local_files_only=True,
    ).to(device)
    body = Body(_openpose_body_model(cache_dir)).to(device)
    # OpenposeDetector.from_pretrained also requests hand and face weights even
    # when those outputs are disabled. Constructing it with Body avoids those
    # unnecessary downloads and keeps this worker strictly body-only.
    openpose = OpenposeDetector(body)
    for model in (hed.netNetwork, body.model):
        if any(parameter.device.type != "cuda" for parameter in model.parameters()):
            raise RuntimeError("Modelo ControlNet fora da GPU; inferência CPU proibida")
    return hed, openpose


def require_cuda(device: str = "cuda") -> str:
    if device not in ("cuda", "auto"):
        raise ValueError("ControlNet_aux exige CUDA; execução CPU desabilitada")
    resolved = resolve_device("cuda")
    if resolved != "cuda":
        raise RuntimeError("CUDA indisponível para ControlNet_aux")
    import torch
    # Check a real kernel, not only driver enumeration.
    torch.ones(1, device="cuda").sum().item()
    torch.cuda.synchronize()
    return resolved


def _cell_path(root: Path, channel: str, row: int, column: int) -> Path:
    return root / channel / f"row{row}_col{column}.png"


def _cuda_stream(device: str):
    """Create one CUDA stream per detector channel when running in parallel."""
    if device != "cuda":
        return None
    import torch

    return torch.cuda.Stream(device=device)


def _infer_channel(
    detector: Any,
    channel: str,
    cells: list[tuple[int, int, Path]],
    root: Path,
    size: int,
    detection_size: int,
    device: str,
    use_stream: bool = False,
) -> dict[tuple[int, int], dict[str, Any]]:
    """Run one detector channel while keeping model state resident."""
    reports: dict[tuple[int, int], dict[str, Any]] = {}
    stream = _cuda_stream(device) if use_stream else None
    torch_module = None
    if stream is not None:
        import torch as torch_module
    for row, column, beauty_path in cells:
        cell_started = time.monotonic()
        source = _as_rgb_on_white(beauty_path)
        try:
            context = (
                torch_module.cuda.stream(stream)
                if stream is not None
                else nullcontext()
            )
            with context:
                if channel == "lineart":
                    value = detector(
                        source,
                        detect_resolution=detection_size,
                        image_resolution=size,
                        safe=False,
                        scribble=False,
                        output_type="pil",
                    )
                else:
                    value = detector(
                        source,
                        detect_resolution=detection_size,
                        image_resolution=size,
                        include_body=True,
                        include_hand=False,
                        include_face=False,
                        output_type="pil",
                    )
            path = _cell_path(root, channel, row, column)
            if channel == "lineart":
                _save_lineart(value, path)
            else:
                _save_openpose(value, path)
        finally:
            source.close()
            if "value" in locals() and hasattr(value, "close"):
                value.close()
            value = None
        reports[(row, column)] = {
            "path": str(path),
            "elapsed_seconds": round(time.monotonic() - cell_started, 3),
        }
    return reports


def _infer_cell_pair(
    hed: Any,
    openpose: Any,
    cell: tuple[int, int, Path],
    root: Path,
    size: int,
    detection_size: int,
    device: str,
    stream: Any,
    torch_module: Any,
) -> dict[str, Any]:
    """Infer both channels for one cell on a worker-owned CUDA stream."""
    row, column, beauty_path = cell
    source = _as_rgb_on_white(beauty_path)
    try:
        lineart_started = time.monotonic()
        context = (
            torch_module.cuda.stream(stream)
            if stream is not None
            else nullcontext()
        )
        with context:
            softedge = hed(
                source,
                detect_resolution=detection_size,
                image_resolution=size,
                safe=False,
                scribble=False,
                output_type="pil",
            )
        lineart_path = _cell_path(root, "lineart", row, column)
        _save_lineart(softedge, lineart_path)
        if hasattr(softedge, "close"):
            softedge.close()

        bones_started = time.monotonic()
        context = (
            torch_module.cuda.stream(stream)
            if stream is not None
            else nullcontext()
        )
        with context:
            pose = openpose(
                source,
                detect_resolution=detection_size,
                image_resolution=size,
                include_body=True,
                include_hand=False,
                include_face=False,
                output_type="pil",
            )
        bones_path = _cell_path(root, "bones", row, column)
        _save_openpose(pose, bones_path)
        if hasattr(pose, "close"):
            pose.close()
    finally:
        source.close()

    return {
        "row": row,
        "column": column,
        "lineart": str(lineart_path),
        "bones": str(bones_path),
        "lineart_elapsed_seconds": round(bones_started - lineart_started, 3),
        "bones_elapsed_seconds": round(time.monotonic() - bones_started, 3),
    }


def _infer_cell_batch(
    hed: Any,
    openpose: Any,
    cells: list[tuple[int, int, Path]],
    root: Path,
    size: int,
    detection_size: int,
    device: str,
) -> list[dict[str, Any]]:
    """Process a partition of cells while sharing the resident model pair."""
    stream = _cuda_stream(device)
    torch_module = None
    if stream is not None:
        import torch as torch_module
    return [
        _infer_cell_pair(
            hed, openpose, cell, root, size, detection_size, device,
            stream, torch_module,
        )
        for cell in cells
    ]


def infer_channels(
    root: Path,
    rows: int,
    columns: int,
    size: int,
    *,
    device: str = "cuda",
    detect_resolution: int | None = None,
    cache_dir: str | None = None,
    channel_workers: int = 1,
) -> dict[str, Any]:
    if rows < 1 or columns < 1 or size < 1:
        raise ValueError("rows, columns e size devem ser positivos")
    if channel_workers not in (1, 2, 4, 8, 16):
        raise ValueError("channel_workers deve ser 1, 2, 4, 8 ou 16")
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)

    beauty_cells = []
    for row in range(rows):
        for column in range(columns):
            path = root / f"row{row}_col{column}.png"
            if not path.is_file():
                raise FileNotFoundError(f"célula beauty ausente: {path}")
            beauty_cells.append((row, column, path))

    print("CONTROLNET checking_cuda", flush=True)
    resolved_device = require_cuda(device)
    detection_size = int(detect_resolution or max(512, min(1024, size)))
    if detection_size < 64:
        raise ValueError("detect_resolution deve ser pelo menos 64")
    report_path = root / CONTROLNET_REPORT
    started = time.monotonic()
    reports: list[dict[str, Any]] = []
    lease = GPULease(resolved_device)
    try:
        print("CONTROLNET loading_models device=cuda", flush=True)
        hed, openpose = _load_detectors(resolved_device, cache_dir)
        if channel_workers == 2:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {
                    pool.submit(_infer_channel, hed, "lineart", beauty_cells,
                                root, size, detection_size, resolved_device, True): "lineart",
                pool.submit(_infer_channel, openpose, "bones", beauty_cells,
                                root, size, detection_size, resolved_device, True): "bones",
                }
                channel_reports = {futures[future]: future.result() for future in futures}
        elif channel_workers in (4, 8, 16):
            partitions = [beauty_cells[index::channel_workers] for index in range(channel_workers)]
            with ThreadPoolExecutor(max_workers=channel_workers) as pool:
                futures = [
                    pool.submit(
                        _infer_cell_batch,
                        hed,
                        openpose,
                        partition,
                        root,
                        size,
                        detection_size,
                        resolved_device,
                    )
                    for partition in partitions
                ]
                reports.extend(
                    item
                    for future in futures
                    for item in future.result()
                )
        else:
            channel_reports = {
                "lineart": _infer_channel(
                    hed, "lineart", beauty_cells, root, size, detection_size, resolved_device
                ),
                "bones": _infer_channel(
                    openpose, "bones", beauty_cells, root, size, detection_size, resolved_device
                ),
            }

        if channel_workers in (4, 8, 16):
            reports.sort(key=lambda item: (item["row"], item["column"]))
        for index, (row, column, _beauty_path) in enumerate(beauty_cells, start=1):
            if channel_workers in (4, 8, 16):
                report = reports[index - 1]
                report["elapsed_seconds"] = max(
                    report["lineart_elapsed_seconds"],
                    report["bones_elapsed_seconds"],
                )
                print(
                    f"CONTROLNET cell={index}/{len(beauty_cells)} row={row} column={column}",
                    flush=True,
                )
                continue
            lineart = channel_reports["lineart"][(row, column)]
            bones = channel_reports["bones"][(row, column)]
            reports.append(
                {
                    "row": row,
                    "column": column,
                    "lineart": lineart["path"],
                    "bones": bones["path"],
                    "lineart_elapsed_seconds": lineart["elapsed_seconds"],
                    "bones_elapsed_seconds": bones["elapsed_seconds"],
                    "elapsed_seconds": max(lineart["elapsed_seconds"], bones["elapsed_seconds"]),
                }
            )
            print(
                f"CONTROLNET cell={index}/{len(beauty_cells)} row={row} column={column}",
                flush=True,
            )
    finally:
        lease.close()

    result: dict[str, Any] = {
        "schema": "sprite_lab.controlnet_channels/v1",
        "source": "blender_beauty_cells",
        "rows": rows,
        "columns": columns,
        "cell_size": [size, size],
        "device": resolved_device,
        "detect_resolution": detection_size,
        "local_files_only": True,
        "lineart": {
            "processor": "hed_softedge",
            "detector": "controlnet_aux.HEDdetector",
            "repository": ANNOTATOR_REPO,
            "safe": False,
            "output": "rgba_white_lines_alpha_strength",
        },
        "bones": {
            "processor": "openpose",
            "detector": "controlnet_aux.OpenposeDetector",
            "repository": OPENPOSE_REPO,
            "model": OPENPOSE_BODY_FILE,
            "include_body": True,
            "include_hand": False,
            "include_face": False,
            "output": "rgb_coco18_body_map",
        },
        "channel_workers": channel_workers,
        "worker_strategy": (
            "serial_channels" if channel_workers == 1
            else "parallel_channels" if channel_workers == 2
            else "parallel_cells_shared_models"
        ),
        "cells": reports,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    report_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-cuda-report", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--rows", type=int)
    parser.add_argument("--columns", type=int)
    parser.add_argument("--size", type=int)
    parser.add_argument("--device", choices=("auto", "cuda"), default="cuda")
    parser.add_argument("--detect-resolution", type=int)
    parser.add_argument("--cache-dir")
    parser.add_argument(
        "--channel-workers", type=int,
        default=int(os.environ.get("SPRITE_LAB_CONTROLNET_WORKERS", "1")),
    )
    args = parser.parse_args()
    if args.check_cuda_report:
        device = require_cuda(args.device)
        args.check_cuda_report.write_text(json.dumps({"device": device}) + "\n")
        print("CONTROLNET cuda_ready", flush=True)
        return 0
    if any(value is None for value in (args.root, args.rows, args.columns, args.size)):
        parser.error("--root, --rows, --columns e --size são obrigatórios")
    result = infer_channels(
        args.root,
        args.rows,
        args.columns,
        args.size,
        device=args.device,
        detect_resolution=args.detect_resolution,
        cache_dir=args.cache_dir,
        channel_workers=args.channel_workers,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
