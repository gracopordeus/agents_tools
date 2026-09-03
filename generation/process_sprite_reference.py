#!/usr/bin/env python3
"""Processa uma sprite sheet de referência em frames RGBA e exporta GIFs de
preview (uma animação por direção) antes da importação no Godot.

Suporta fundo branco (removido com despill) ou PNG já com canal alpha.
A grade é detectada: linhas pelos separadores/gutters (regiões altas que
escondem duas linhas são divididas no vale de conteúdo) e colunas por linha
via gutters. Cada frame é recortado pela bbox do conteúdo e alinhado
bottom-center num canvas comum.

Direções por linha (padrão do contrato Blender):
    R1 North, R2 North-East, R3 East, R4 South-East,
    R5 South, R6 South-West, R7 West, R8 North-West
Use --mapping somente quando a imagem externa tiver outra ordem
(ex.: "r1,r2,r3,r4,r5,r6,r7,r8").

Uso:
    process_sprite_reference.py IN.png --out OUT_DIR --name nome [--white-bg]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent / "sprite-lab"))
from direction_contract import DIRECTION_ROWS, direction_contract_for

DEFAULT_MAPPING = list(DIRECTION_ROWS)
WHITE_HI, WHITE_LO = 250.0, 235.0
SEP_THRESH_FRAC = 0.01
FPS = 10


def load_rgba(path: Path, white_bg: bool, magenta_bg: bool) -> np.ndarray:
    im = Image.open(path)
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    if not white_bg and not magenta_bg:
        return np.asarray(im).astype(np.uint8)
    if magenta_bg:
        rgb = np.asarray(im.convert("RGB")).astype(np.float32)
        # Chroma-key the vivid pink background while preserving anti-aliased edges.
        distance = np.linalg.norm(rgb - np.array([255.0, 0.0, 255.0]), axis=2)
        alpha = np.clip((distance - 18.0) / 42.0, 0.0, 1.0)
        fg = rgb
        return np.concatenate([fg, alpha[..., None] * 255.0], axis=2).astype(np.uint8)
    rgb = np.asarray(im.convert("RGB")).astype(np.float32)
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input", type=Path, help="Imagem de entrada (PNG/JPEG)")
    p.add_argument("--out", type=Path, required=True, help="Diretório de saída")
    p.add_argument("--name", default="sprite", help="Prefixo dos GIFs/frames")
    p.add_argument("--white-bg", action="store_true", help="Remove fundo branco (senão usa alpha existente)")
    p.add_argument("--magenta-bg", action="store_true", help="Remove fundo magenta/chroma key")
    p.add_argument("--mapping", default=",".join(DEFAULT_MAPPING),
                   help="Ordem das direções por linha, separadas por vírgula")
    p.add_argument("--fps", type=int, default=FPS)
    args = p.parse_args(argv)

    if not args.input.exists():
        print(f"entrada não encontrada: {args.input}", file=sys.stderr)
        return 1
    mapping = [d.strip() for d in args.mapping.split(",") if d.strip()]

    rgba = load_rgba(args.input, args.white_bg, args.magenta_bg)
    h, w = rgba.shape[:2]
    content = rgba[..., 3] > 128

    rows = find_rows(content)
    if len(rows) != len(mapping):
        print(f"detectadas {len(rows)} linhas (esperado {len(mapping)}): {rows}", file=sys.stderr)
        return 1

    try:
        direction_contract = direction_contract_for(mapping)
    except ValueError:
        direction_contract = None
    report: dict = {
        "source": str(args.input),
        "size": [w, h],
        "rows": {},
        "cells": {},
        "direction_contract": direction_contract,
    }
    frames_by_dir: dict[str, list[Image.Image]] = {}
    all_sizes = []

    for ri, (y0, y1) in enumerate(rows):
        d = mapping[ri]
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

    expected = len(mapping) * 8
    if len(all_sizes) != expected:
        print(f"faltando frames: {len(all_sizes)}/{expected}", file=sys.stderr)
        return 1

    mw = max(s[0] for s in all_sizes)
    mh = max(s[1] for s in all_sizes)
    pad = 16
    cw, ch = mw + 2 * pad, mh + 2 * pad
    report["canvas"] = [cw, ch]

    args.out.mkdir(parents=True, exist_ok=True)
    frames_dir = args.out / "frames"
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
            gif.save(args.out / f"{args.name}_{d}.gif", format="GIF", save_all=True,
                     append_images=[f.convert("RGBA") for f in composed[1:]],
                     duration=1000 // args.fps, loop=0, disposal=2, transparency=0)
        except Exception:
            gif.save(args.out / f"{args.name}_{d}.gif", format="GIF", save_all=True,
                     append_images=[f.convert("RGBA") for f in composed[1:]],
                     duration=1000 // args.fps, loop=0, disposal=2)

    (args.out / "report.json").write_text(json.dumps(report, indent=2))
    print(f"ok: {len(rows)} linhas, {expected} frames, canvas {cw}x{ch}")
    for d in mapping:
        print(f"  {args.out}/{args.name}_{d}.gif")
    return 0


if __name__ == "__main__":
    sys.exit(main())
