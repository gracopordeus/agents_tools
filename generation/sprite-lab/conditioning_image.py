"""Small dependency-light image helpers shared by the PoC stages."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image


def load_rgba(path: Path) -> Image.Image:
    if not path.is_file():
        raise FileNotFoundError(path)
    with Image.open(path) as source:
        return source.convert("RGBA")


def _border_pixels(rgb: np.ndarray) -> np.ndarray:
    return np.concatenate(
        [rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]], axis=0
    )


def foreground_mask(
    image: Image.Image,
    *,
    alpha_threshold: int = 16,
    background_threshold: float = 32.0,
) -> np.ndarray:
    """Return a foreground mask while preserving dark internal details.

    Existing alpha wins when the image has meaningful transparency. For opaque
    provider outputs, only background-colored pixels connected to the border are
    removed, so a similarly colored detail inside the character survives.
    """
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[..., 3]
    visible_alpha = alpha > alpha_threshold
    if int((alpha < 250).sum()) > max(8, int(alpha.size * 0.01)):
        return visible_alpha

    rgb = rgba[..., :3].astype(np.float32)
    border = _border_pixels(rgb)
    background = np.median(border, axis=0)
    distance = np.linalg.norm(rgb - background, axis=2)
    candidate = distance <= float(background_threshold)
    height, width = candidate.shape
    background_connected = np.zeros_like(candidate, dtype=bool)
    pending: deque[tuple[int, int]] = deque()
    for x in range(width):
        pending.append((x, 0))
        pending.append((x, height - 1))
    for y in range(height):
        pending.append((0, y))
        pending.append((width - 1, y))
    while pending:
        x, y = pending.popleft()
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        if background_connected[y, x] or not candidate[y, x]:
            continue
        background_connected[y, x] = True
        pending.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
    return ~background_connected


def remove_background(image: Image.Image, *, threshold: float = 32.0) -> Image.Image:
    """Return RGBA with only border-connected background made transparent."""
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    mask = foreground_mask(image, background_threshold=threshold)
    rgba[..., 3] = np.where(mask, rgba[..., 3], 0)
    return Image.fromarray(rgba, "RGBA")


def alpha_bbox(image: Image.Image, threshold: int = 16) -> tuple[int, int, int, int] | None:
    alpha = np.asarray(image.convert("RGBA"), dtype=np.uint8)[..., 3]
    ys, xs = np.where(alpha > threshold)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
