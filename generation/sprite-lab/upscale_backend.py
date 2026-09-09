"""Shared RGB super-resolution backend; alpha is always handled by the caller.

Spandrel loads the checkpoint architecture, without the obsolete BasicSR import
stack. The legacy enhance(BGR, outscale) interface and Lanczos4 x4->x2 reduction
are retained. Tiling uses padded context and crops only the central region.
"""
from __future__ import annotations

import hashlib

import cv2
import numpy as np
import torch

import huggingface_realesrgan
from postprocess_runtime import GPULease, resolve_device, resolve_dtype


class Upscaler:
    def __init__(self, profile_id, tile_size=256, tile_pad=32, device="auto", precision="fp32"):
        from spandrel import ModelLoader, ImageModelDescriptor
        self.device = resolve_device(device)
        self.dtype = resolve_dtype(self.device, precision)
        self.precision = precision
        self.tile_size, self.tile_pad = tile_size, tile_pad
        if tile_size < 32 or tile_pad < 0:
            raise ValueError("Invalid tile size/padding")
        self.path = huggingface_realesrgan.download_weight(profile_id)
        self.sha256 = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.lease = GPULease(self.device)
        self.model = ModelLoader().load_from_file(str(self.path))
        if not isinstance(self.model, ImageModelDescriptor):
            raise ValueError("Checkpoint is not an image model")
        if precision == "fp16" and not self.model.supports_half:
            raise ValueError(f"{profile_id} does not support FP16")
        self.model.eval().to(device=self.device, dtype=self.dtype)
        self.scale = self.model.scale
        if self.scale != huggingface_realesrgan.profile(profile_id)["network_scale"]:
            raise ValueError("Checkpoint scale differs from model profile")

    @torch.inference_mode()
    def enhance(self, bgr, outscale=2.0):
        if bgr.ndim != 3 or bgr.shape[2] != 3 or bgr.dtype != np.uint8:
            raise ValueError("Expected uint8 HWC BGR")
        h, w = bgr.shape[:2]
        rgb = np.ascontiguousarray(bgr[:, :, ::-1])
        source = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).to(self.device, self.dtype) / 255
        output = np.empty((h*self.scale, w*self.scale, 3), dtype=np.uint8)
        # CUGAN's squeeze/excitation statistics are global: do not independently
        # tile small sprite cells and pretend equivalence to full-frame inference.
        global_context = str(self.model.architecture.id) == "RealCUGAN"
        tile = max(h, w) if global_context else self.tile_size
        for y in range(0, h, tile):
            for x in range(0, w, tile):
                y1, x1 = min(y+tile, h), min(x+tile, w)
                py, px = max(0, y-self.tile_pad), max(0, x-self.tile_pad)
                ey, ex = min(h, y1+self.tile_pad), min(w, x1+self.tile_pad)
                predicted = self.model(source[:, :, py:ey, px:ex])
                if not torch.isfinite(predicted).all():
                    raise RuntimeError("Non-finite super-resolution output")
                s = self.scale
                cropped = predicted[0, :, (y-py)*s:(y1-py)*s, (x-px)*s:(x1-px)*s]
                output[y*s:y1*s, x*s:x1*s] = cropped.clamp(0, 1).mul(255).round().byte().permute(1, 2, 0).cpu().numpy()
                del predicted, cropped
        output = output[:, :, ::-1].copy()
        if outscale != self.scale:
            output = cv2.resize(output, (round(w*outscale), round(h*outscale)), interpolation=cv2.INTER_LANCZOS4)
        return output, "RGB"
