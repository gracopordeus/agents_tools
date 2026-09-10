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


def add_runtime_arguments(parser):
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"),
                        default=os.environ.get("SPRITE_LAB_POSTPROCESS_DEVICE", "auto"))
    parser.add_argument("--precision", choices=("fp32", "fp16"), default="fp32")


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
