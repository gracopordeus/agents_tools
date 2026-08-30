#!/usr/bin/env python3
"""Processa a referência orc_reference.jpeg (fundo branco, grid 8x8) em frames RGBA
e exporta GIFs de preview (uma animação por direção) antes da importação no Godot.

A grade do arquivo é irregular (gerada por IA): o script detecta as linhas pelos
separadores brancos, divide regiões altas (que escondem duas linhas) pelo vale
de conteúdo, e acha as 8 colunas por linha via gutters. Cada frame é recortado
pela bbox do conteúdo, alinhado bottom-center (baseline nos pés) num canvas
comum.

Direções por linha (ordem do usuário):
    A, WA, D, WD, W, SA, S, SD  ->  w, nw, e, ne, n, sw, s, se

Saídas (tudo determinístico, sem redimensionar a arte):
    artifacts/orc_reference_preview/orc_walk_<dir>.gif   (8 GIFs, loop infinito)
    artifacts/orc_reference_preview/frames/<dir>_NN.png  (frames individuais)
    artifacts/orc_reference_preview/report.json          (bbox de cada frame)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REF = Path("/home/ggnp/simple-arpg/references/orc_reference.jpeg")
OUT = Path("/home/ggnp/simple-arpg/artifacts/orc_reference_preview")
ROWS_TO_DIR = ["w", "nw", "e", "ne", "n", "sw", "s", "se"]

WHITE_HI, WHITE_LO = 250.0, 235.0
SEP_THRESH_FRAC = 0.01
SPRITE_FPS = 10


def load_rgba(path: Path) -> np.ndarray:
    """Remove o fundo branco com despill (elimina halo branco nas bordas)."""
    rgb = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    minc = rgb.min(axis=2)
    a = np.clip((WHITE_HI - minc) / (WHITE_HI - WHITE_LO), 0.0, 1.0).astype(np.float32)
    fg = (rgb - (1.0 - a[..., None]) * 255.0) / np.maximum(a[..., None], 1e-3)
    fg = np.clip(fg, 0.0, 255.0)
    return np.concatenate([fg, a[..., None] * 255.0], axis=2).astype(np.uint8)


def split_runs(mask: np.ndarray, axis: int) -> list[tuple[int, int]]:
    prof = mask.sum(axis=axis)
    n = prof.shape[0]
    thr = prof.max() * 0.02 if axis == 1 else n * SEP_THRESH_FRAC
    out: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if prof[i] <= thr:
            s = i
            while i + 1 < n and prof[i + 1] <= thr:
                i += 1
            out.append((s, i))
        i += 1
    return out


def find_rows(content: np.ndarray) -> list[tuple[int, int]]:
    """Regiões de conteúdo entre gutters, quebrando regiões altas no vale.

    Cada região vai do fim de uma gutter até o início da próxima. Regiões altas
    (que escondem duas linhas) são divididas no vale de conteúdo interno.
    """
    h, w = content.shape
    prof = content.sum(axis=1)
    sep = split_runs(content, 1)
    regions = []
    for k in range(len(sep) - 1):
        y0 = sep[k][1] + 1
        y1 = sep[k + 1][0] - 1
        if y1 - y0 < 10:
            continue
        if prof[y0 : y1 + 1].max() <= w * SEP_THRESH_FRAC:
            continue
        rows_idx = np.where(prof[y0 : y1 + 1] > w * 0.02)[0]
        if rows_idx.size == 0:
            continue
        regions.append((y0 + int(rows_idx.min()), y0 + int(rows_idx.max())))
    med = float(np.median([y1 - y0 for y0, y1 in regions]))
    final = []
    for y0, y1 in regions:
        if y1 - y0 > med * 1.35:
            inner = prof[y0 : y1 + 1].copy()
            inner[0] = inner[-1] = inner.max()
            split = y0 + int(np.argmin(inner))
            final.append((y0, split - 1))
            final.append((split + 1, y1))
        else:
            final.append((y0, y1))
    return sorted(final)


def find_cells(content: np.ndarray, y0: int, y1: int, n_cells: int = 8) -> list[tuple[int, int]]:
    """Divide a linha em n_cells células usando gutters de coluna; fallback equidistante."""
    sub = content[y0 : y1 + 1]
    colprof = sub.sum(axis=0)
    thr = (y1 - y0 + 1) * 0.02
    gut = []
    i = 0
    while i < colprof.shape[0]:
        if colprof[i] <= thr:
            s = i
            while i + 1 < colprof.shape[0] and colprof[i + 1] <= thr:
                i += 1
            gut.append((s + i) // 2)
        i += 1
    internal = gut if len(gut) < 9 else gut[1:-1]
    if len(internal) >= n_cells - 1:
        cuts = internal[: n_cells - 1]
    else:
        xs = np.where(colprof > 0)[0]
        x0, x1 = xs.min(), xs.max()
        cuts = [int(x0 + (x1 - x0) * (k + 1) / n_cells) for k in range(n_cells - 1)]
    edges = [0] + cuts + [colprof.shape[0]]
    cells = []
    for k in range(n_cells):
        c0 = edges[k] + (1 if k == 0 else 1)
        c1 = edges[k + 1] - (1 if k == n_cells else 0)
        if c1 > c0:
            cells.append((c0, c1))
    return cells


def content_bbox(content: np.ndarray, y0: int, y1: int, x0: int, x1: int):
    sub = content[y0 : y1 + 1, x0 : x1 + 1]
    ys, xs = np.where(sub)
    if ys.size == 0:
        return None
    return (x0 + xs.min(), y0 + ys.min(), x0 + xs.max(), y0 + ys.max())


def place(canvas: Image.Image, frame: Image.Image, pad: int) -> None:
    cw, ch = canvas.size
    fw, fh = frame.size
    canvas.paste(frame, ((cw - fw) // 2, ch - pad - fh), frame)


def main() -> int:
    if not REF.exists():
        print(f"referência não encontrada: {REF}", file=sys.stderr)
        return 1
    rgba = load_rgba(REF)
    h, w = rgba.shape[:2]
    content = rgba[..., 3] > 128

    rows = find_rows(content)
    if len(rows) != 8:
        print(f"detectadas {len(rows)} linhas (esperado 8): {rows}", file=sys.stderr)
        return 1

    report: dict = {"source": str(REF), "size": [w, h], "rows": {}, "cells": {}}
    frames_by_dir: dict[str, list[Image.Image]] = {}
    all_sizes = []

    for ri, (y0, y1) in enumerate(rows):
        d = ROWS_TO_DIR[ri]
        cells = find_cells(content, y0, y1)
        if len(cells) != 8:
            print(f"linha {ri} ({d}): {len(cells)} células (esperado 8)", file=sys.stderr)
            return 1
        report["rows"][d] = [int(y0), int(y1)]
        imgs = []
        for ci, (x0, x1) in enumerate(cells):
            bb = content_bbox(content, y0, y1, x0, x1)
            if bb is None:
                report["cells"][f"{d}_{ci}"] = None
                continue
            bx0, by0, bx1, by1 = bb
            crop = rgba[by0 : by1 + 1, bx0 : bx1 + 1]
            frame = Image.fromarray(crop, "RGBA")
            imgs.append(frame)
            all_sizes.append((frame.size[0], frame.size[1]))
            report["cells"][f"{d}_{ci}"] = [int(bx0), int(by0), int(bx1), int(by1)]
        frames_by_dir[d] = imgs

    if len(all_sizes) != 64:
        print(f"faltando frames: {len(all_sizes)}/64", file=sys.stderr)
        return 1

    mw = max(s[0] for s in all_sizes)
    mh = max(s[1] for s in all_sizes)
    pad = 16
    cw, ch = mw + 2 * pad, mh + 2 * pad
    report["canvas"] = [cw, ch]

    OUT.mkdir(parents=True, exist_ok=True)
    frames_dir = OUT / "frames"
    frames_dir.mkdir(exist_ok=True)

    for d, imgs in frames_by_dir.items():
        composed = []
        for i, frame in enumerate(imgs):
            canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            place(canvas, frame, pad)
            canvas.save(frames_dir / f"{d}_{i:02d}.png")
            composed.append(canvas)
        gif = composed[0].convert("RGBA")
        try:
            gif.save(OUT / f"orc_walk_{d}.gif", format="GIF", save_all=True,
                     append_images=[f.convert("RGBA") for f in composed[1:]],
                     duration=1000 // SPRITE_FPS, loop=0, disposal=2, transparency=0)
        except Exception:
            gif.save(OUT / f"orc_walk_{d}.gif", format="GIF", save_all=True,
                     append_images=[f.convert("RGBA") for f in composed[1:]],
                     duration=1000 // SPRITE_FPS, loop=0, disposal=2)

    (OUT / "report.json").write_text(json.dumps(report, indent=2))
    print(f"ok: {len(rows)} linhas, {len(all_sizes)} frames, canvas {cw}x{ch}")
    for d in ROWS_TO_DIR:
        print(f"  artifacts/orc_reference_preview/orc_walk_{d}.gif")
    return 0


if __name__ == "__main__":
    sys.exit(main())
