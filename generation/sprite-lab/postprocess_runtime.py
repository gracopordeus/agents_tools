"""Explicit runtime selection for local post-processing inference."""
from __future__ import annotations

import os
from pathlib import Path


class GPULease:
    """Serialize post-processing CUDA subprocesses across server workers (Linux)."""
    def __init__(self, device):
        self.handle = None
        if device == "cuda":
            import fcntl
            path = Path(__file__).resolve().parent / "work" / "postprocess-gpu.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = path.open("a")
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def __del__(self):
        self.close()


def _positive_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "") or default))
    except (TypeError, ValueError):
        return default


def runtime_batch_size() -> int:
    # Batch 2 is the calibrated default for the local 8 GB GTX 1070 Ti:
    # it raises occupancy without the memory pressure observed at batch 4.
    return _positive_env("SPRITE_LAB_POSTPROCESS_BATCH_SIZE", 2)


def runtime_cpu_workers() -> int:
    return _positive_env("SPRITE_LAB_POSTPROCESS_CPU_WORKERS", 4)


def validate_parallelism(batch_size: int, cpu_workers: int) -> None:
    if batch_size < 1 or cpu_workers < 1:
        raise ValueError("batch-size e cpu-workers devem ser positivos")


def add_runtime_arguments(parser):
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"),
                        default=os.environ.get("SPRITE_LAB_POSTPROCESS_DEVICE", "auto"))
    parser.add_argument(
        "--precision",
        choices=("fp32", "fp16"),
        default=os.environ.get("SPRITE_LAB_POSTPROCESS_PRECISION", "fp32"),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=runtime_batch_size(),
        help="quantidade de células por inferência GPU",
    )
    parser.add_argument(
        "--cpu-workers",
        type=int,
        default=runtime_cpu_workers(),
        help="workers para etapas independentes por célula na CPU",
    )


def resolve_device(requested="auto"):
    if requested not in ("auto", "cpu", "cuda"):
        raise ValueError(f"Invalid device: {requested}")
    # An explicit CPU request must not initialize or probe the CUDA driver.
    # This is important for recovery after a failed GPU worker and makes the
    # local annotator fallback deterministic.
    if requested == "cpu":
        return "cpu"
    import torch
    available = torch.cuda.is_available()
    if requested == "cuda" and not available:
        raise RuntimeError("CUDA solicitada, mas indisponível neste Python; verifique SPRITE_LAB_PYTHON")
    return "cuda" if available else "cpu"


def resolve_dtype(device, precision):
    import torch
    if precision not in ("fp32", "fp16"):
        raise ValueError(f"Invalid precision: {precision}")
    if device == "cpu" and precision == "fp16":
        raise ValueError("FP16 requer CUDA; use FP32 na CPU")
    return torch.float16 if precision == "fp16" else torch.float32
