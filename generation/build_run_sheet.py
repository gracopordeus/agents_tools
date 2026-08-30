#!/usr/bin/env python3
"""Compoe as 64 células do run em sheets 8x8 e artefatos de revisão.

Lê artifacts/run_template_cells/row{r}_col{c}.png e gera:
  references/run_template_8x8.png          (1024^2, fundo preto, grid, silhueta)
  references/run_template_8x8_2048.png     (2048^2)
  references/run_area_map_1024.png         (área ocupada por célula em magenta)
  artifacts/run_template_preview/run_<dir>.gif  (8 GIFs de revisão)
  artifacts/run_template_frames/<dir>_NN.png    (frames normalizados, transparentes)
  references/run_template_report.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from path_config import PROJECT_ROOT
import sprite_manifest as manifest_lib

ROOT = PROJECT_ROOT
CELLS = ROOT / "artifacts/run_template_cells"
ROWS = ["w", "nw", "e", "ne", "n", "sw", "s", "se"]
CELL = 256               # célula no sheet (base) — aproveita render de alta resolução
FILL_FRAC = 0.95         # conteúdo ocupa 95% da altura da célula (chão quase na borda)
SIL = (0.82, 0.82, 0.82)
GRID = (120, 120, 120)
MAGENTA = (246, 4, 242)
FPS = 10


def parse_args():
    p = argparse.ArgumentParser(description="Compoe células em sheets/artefatos de revisão.")
    p.add_argument("--cells", default=str(CELLS), help="Dir com as células row{r}_col{c}.png")
    p.add_argument("--tag", default="", help="Sufixo de saída (ex.: 45 => *_45, preview_45)")
    p.add_argument("--bg", choices=["black", "transparent"], default="transparent",
                   help="Fundo do sheet (padrão transparente com canal alfa)")
    p.add_argument("--grid", action="store_true", help="Desenha linhas de grid no sheet")
    p.add_argument("--gif-res", type=int, default=1024,
                   help="Resolução (altura) das células dos GIFs (0 = usa as frames base 256)")
    p.add_argument("--gif-fill", type=float, default=0.92,
                   help="Preenchimento do personagem no frame do GIF (0-1)")
    p.add_argument("--no-sharpen", action="store_true",
                   help="Desliga o afiamento de contorno (binarização do alpha)")
    p.add_argument("--out-base", default=str(ROOT),
                   help="Raiz de saída (default: raiz do projeto)")
    p.add_argument("--fit", choices=["reference_fit", "runtime_fit"],
                   default="reference_fit",
                   help="Política de enquadramento da célula")
    p.add_argument("--runtime-fill", type=float, default=0.82,
                   help="Fill vertical do runtime_fit (0-1)")
    p.add_argument("--animation", default="run", help="Nome lógico da animação")
    p.add_argument("--fps", type=float, default=FPS, help="FPS da animação")
    p.add_argument("--asset-id", default="", help="ID lógico do asset no catálogo")
    p.add_argument("--asset-source", default="", help="Origem declarativa do asset")
    p.add_argument("--asset-license", default="", help="Licença declarativa do asset")
    p.add_argument("--manifest", default=None,
                   help="Caminho do manifest v1 (padrão: out-base/sprite_manifest_v1.json)")
    return p.parse_args()


def content_bbox(frame: Image.Image):
    a = np.asarray(frame)
    ys, xs = np.where(a[..., 3] > 10)
    if xs.size == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def normalize_window(frame: Image.Image, window, scale: float,
                     canvas_w: int = CELL, canvas_h: int | None = None) -> Image.Image:
    """Recorta uma JANELA FIXA (união das bboxes), aplica escala comum e cola no topo.

    Preserva a posição vertical real do personagem (bounce da corrida): janela e
    alinhamento idênticos em todos os frames, em vez de bottom-align por frame
    (que colava o pé no fundo da célula e apagava o salto).
    """
    if canvas_h is None:
        canvas_h = canvas_w
    x0, y0, x1, y1 = window
    crop = frame.crop((x0, y0, x1 + 1, y1 + 1))
    nw = max(1, round(crop.width * scale))
    nh = max(1, round(crop.height * scale))
    crop = crop.resize((nw, nh), Image.LANCZOS)
    canvas_img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    px = (canvas_w - nw) // 2          # centraliza horizontalmente (espelhamento correto)
    canvas_img.paste(crop, (px, 0), crop)
    return canvas_img


def fit_dimensions(window: tuple[int, int, int, int], fit: str,
                   runtime_fill: float) -> tuple[int, int, float]:
    """Calcula escala e envelope da célula sem misturar referência com runtime.

    ``reference_fit`` mantém o contrato histórico de uma célula quadrada de
    256 px. ``runtime_fit`` fixa a altura para preservar a leitura do corpo e
    amplia a largura quando uma arma longa exige espaço adicional.
    """
    x0, y0, x1, y1 = window
    union_w, union_h = x1 - x0 + 1, y1 - y0 + 1
    if not union_w or not union_h:
        return CELL, CELL, 1.0
    if fit == "reference_fit":
        scale = min((FILL_FRAC * CELL) / union_h, CELL / union_w)
        return CELL, CELL, scale
    if not 0 < runtime_fill <= 1:
        raise ValueError("runtime_fill deve estar entre 0 e 1")
    scale = (runtime_fill * CELL) / union_h
    frame_w = max(CELL, math.ceil(union_w * scale))
    return frame_w, CELL, scale


def sheet_canvas(width: int, height: int, bg: tuple, alpha: int = 255) -> Image.Image:
    return Image.new("RGBA", (width, height), bg + (alpha,))


def draw_grid(draw, width, height, rows, cols, color, width_px=1):
    for k in range(cols + 1):
        x = round(k * width / cols)
        draw.line([(x, 0), (x, height)], fill=color, width=width_px)
    for k in range(rows + 1):
        y = round(k * height / rows)
        draw.line([(0, y), (width, y)], fill=color, width=width_px)


def detect_cols(cells_root: Path) -> int:
    cols = 0
    for p in cells_root.glob("row0_col*.png"):
        try:
            cols = max(cols, int(p.stem.split("_col")[1]) + 1)
        except (ValueError, IndexError):
            continue
    return cols or 8


def detect_rows(cells_root: Path) -> int:
    rows = 0
    for p in cells_root.glob("row*_col*.png"):
        try:
            rows = max(rows, int(p.stem.split("_col")[0].removeprefix("row")) + 1)
        except (ValueError, IndexError):
            continue
    return rows or len(ROWS)


def _gif_palette(frames_rgba: list[Image.Image]) -> list[int]:
    """Paleta 256 cores compartilhada a partir dos PIXELS VISÍVEIS (personagem).

    Índice 0 reservado p/ transparente; 1..255 = cores do personagem. Evita que o
    fundo transparente (preto após flatten) domine a paleta.
    """
    pix = []
    for fr in frames_rgba:
        a = np.asarray(fr)
        vis = a[..., 3] > 128
        if vis.sum() > 0:
            pix.append(a[..., :3][vis])
    if not pix:
        return [0, 0, 0] * 256
    px = np.concatenate(pix)
    if len(px) > 8192:
        rng = np.random.default_rng(0)
        px = px[rng.choice(len(px), 8192, replace=False)]
    n = len(px)
    side = int(np.ceil(np.sqrt(n)))
    img = np.zeros((side, side, 3), dtype=np.uint8)
    img.flat[: n * 3] = px.ravel()
    base = Image.fromarray(img, "RGB").quantize(colors=255, method=Image.MEDIANCUT)
    return [255, 0, 255] + list(base.getpalette())   # índice 0 = sentinela p/ transparente


def _to_palette(rgba: Image.Image, palette: list[int]) -> Image.Image:
    """Converte RGBA -> P via quantize nativo (paleta fixa); transparentes -> índice 0."""
    pal_img = Image.new("P", (1, 1))
    pal_img.putpalette(palette)
    p = rgba.convert("RGB").quantize(palette=pal_img, dither=Image.Dither.NONE)
    idx = np.asarray(p).copy()
    a = np.asarray(rgba.convert("RGBA"))[..., 3]
    idx[a < 128] = 0
    im = Image.fromarray(idx, "P")
    im.putpalette(palette)
    return im


def sharpen_contour(frame: Image.Image, speckle_max: int = 6) -> Image.Image:
    """Binariza o alpha (>=128) na alta resolução e remove especks.

    Elimina a borda AA difusa do EEVEE: o contorno fica binário (nítido). O
    downscale LANCZOS posterior suaviza o degrau sem reembaçar. Ideal para
    referência de geradores de imagem (silhueta precisa).
    """
    from scipy import ndimage

    a = np.asarray(frame)
    mask = a[..., 3] >= 128
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    if n > 0:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        small = (sizes < speckle_max)[lab - 1]
        mask = mask & ~small
    out = a.copy()
    out[..., 3] = np.where(mask, 255, 0)
    return Image.fromarray(out)


def main() -> int:
    args = parse_args()
    cells_root = Path(args.cells)
    REF_DIR = Path(args.out_base) / "references"
    ART_DIR = Path(args.out_base) / "artifacts"
    REF_DIR.mkdir(parents=True, exist_ok=True)
    ART_DIR.mkdir(parents=True, exist_ok=True)
    tag = args.tag
    suffix = f"_{tag}" if tag else ""
    if not cells_root.exists():
        print("células não encontradas; rode blender_render_run.py primeiro", file=sys.stderr)
        return 1

    n_rows = detect_rows(cells_root)
    n_cols = detect_cols(cells_root)
    directions = ROWS[:n_rows]

    raw = []
    for r in range(n_rows):
        row = []
        for c in range(n_cols):
            cell = load_cell(cells_root, r, c)
            if not args.no_sharpen:
                cell = sharpen_contour(cell)
            row.append(cell)
        raw.append(row)

    # janela FIXA = união das bboxes de conteúdo de todos os frames
    boxes = {}
    for r in range(n_rows):
        for c in range(n_cols):
            bb = content_bbox(raw[r][c])
            if bb:
                boxes[(r, c)] = bb
    valid = list(boxes.values())
    x0 = min(b[0] for b in valid); y0 = min(b[1] for b in valid)
    x1 = max(b[2] for b in valid); y1 = max(b[3] for b in valid)
    window = (x0, y0, x1, y1)
    union_w, union_h = x1 - x0 + 1, y1 - y0 + 1
    frame_w, frame_h, common_scale = fit_dimensions(
        window, args.fit, args.runtime_fill)

    frames: dict[str, list[Image.Image]] = {}
    bboxes: dict[str, list] = {}
    foot_anchor = [frame_w // 2, round(union_h * common_scale)]
    report = {"source": f"Mixamo run (tag '{tag}' ou padrão)", "rows": directions,
              "columns": n_cols, "cell_size": CELL, "frame_size": [frame_w, frame_h],
              "fit_policy": args.fit,
              "fill_frac": FILL_FRAC if args.fit == "reference_fit" else args.runtime_fill,
              "window": list(window), "common_scale": round(common_scale, 4),
              "foot_anchor": foot_anchor, "fps": args.fps,
              "animation": args.animation, "cells": {}}

    for r, d in enumerate(directions):
        frames[d], bboxes[d] = [], []
        for c in range(n_cols):
            norm = normalize_window(raw[r][c], window, common_scale,
                                    frame_w, frame_h)
            frames[d].append(norm)
            bb = content_bbox(norm)
            bb = bb or (0, 0, 0, 0)
            bboxes[d].append(list(bb))
            report["cells"][f"{d}_{c}"] = list(bb)

    bg_alpha = 0 if args.bg == "transparent" else 255
    base_w, base_h = n_cols * frame_w, n_rows * frame_h
    sheet_stem = "run_template_8x8" if n_cols == 8 else f"run_template_8x{n_cols}"
    if args.fit == "runtime_fit":
        sheet_stem = f"{sheet_stem}_runtime"
    if n_cols == 8:
        sheet_names = [(1, f"{sheet_stem}{suffix}.png"),
                       (2, f"{sheet_stem}{suffix}_2048.png")]
    else:
        sheet_names = [(1, f"{sheet_stem}{suffix}.png"),
                       (2, f"{sheet_stem}{suffix}_2x.png")]
    for scale, name in sheet_names:
        sheet = sheet_canvas(base_w * scale, base_h * scale, (0, 0, 0), bg_alpha)
        for r, d in enumerate(directions):
            for c in range(n_cols):
                f = frames[d][c]
                if scale > 1:
                    f = f.resize((frame_w * scale, frame_h * scale), Image.LANCZOS)
                sheet.paste(f, (c * frame_w * scale, r * frame_h * scale), f)
        if args.grid:
            draw_grid(ImageDraw.Draw(sheet), sheet.width, sheet.height,
                      n_rows, n_cols, GRID, width_px=scale)
        sheet.save(REF_DIR / name)
        print("  gerou references/" + name)

    # área map (preto + retângulos magenta por célula + grid preto)
    area = sheet_canvas(base_w, base_h, (0, 0, 0), 255)
    ad = ImageDraw.Draw(area)
    for r, d in enumerate(directions):
        for c in range(n_cols):
            x0, y0, x1, y1 = bboxes[d][c]
            ox, oy = c * frame_w, r * frame_h
            ad.rectangle([ox + x0, oy + y0, ox + x1, oy + y1], fill=MAGENTA + (255,))
    draw_grid(ad, base_w, base_h, n_rows, n_cols, (0, 0, 0), width_px=1)
    area.save(REF_DIR / f"run_area_map{suffix}.png")
    print("  gerou references/run_area_map" + suffix + ".png")

    # GIFs de preview + frames normalizados
    preview = ART_DIR / f"run_template_preview{suffix}"
    frames_dir = ART_DIR / f"run_template_frames{suffix}"
    preview.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    gif_h = args.gif_res or CELL
    gif_w = max(1, round(gif_h * union_w / union_h))
    gif_scale = min((args.gif_fill * gif_h) / union_h, gif_w / union_w) if union_h and union_w else 1.0
    for r, d in enumerate(directions):
        gif_frames = []
        for i, f in enumerate(frames[d]):
            f.save(frames_dir / f"{d}_{i:02d}.png")
            if args.gif_res:
                gf = normalize_window(raw[r][i], window, gif_scale, gif_w, gif_h)
            else:
                gf = f
            gif_frames.append(gf.convert("RGBA"))
        palette = _gif_palette(gif_frames)
        p_frames = [_to_palette(fr, palette) for fr in gif_frames]
        p_frames[0].save(preview / f"run_{r + 1}.gif", format="GIF", save_all=True,
                         append_images=p_frames[1:],
                         duration=max(1, round(1000 / args.fps)),
                         loop=0, disposal=2, transparency=0)

    report_path = REF_DIR / f"run_template_report{suffix}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    manifest_path = Path(args.manifest) if args.manifest else (
        Path(args.out_base) / f"sprite_manifest_v1{suffix}.json")
    manifest = {
        "schema_id": manifest_lib.SCHEMA_ID,
        "manifest_version": manifest_lib.MANIFEST_VERSION,
        "asset": {
            "id": args.asset_id,
            "animation": args.animation,
            "source": args.asset_source,
            "license": args.asset_license,
        },
        "toolchain": {
            "renderer": "blender_render_catalog",
            "compositor": "build_run_sheet",
            "compositor_version": manifest_lib.MANIFEST_VERSION,
        },
        "layout": {
            "fit_policy": args.fit,
            "directions": directions,
            "columns": n_cols,
            "frame_size": [frame_w, frame_h],
            "sheet_size": [base_w, base_h],
            "fps": args.fps,
            "loop": True,
            "foot_anchor": foot_anchor,
            "transparent": args.bg == "transparent",
        },
        "fit": {
            "window": list(window),
            "scale": round(common_scale, 4),
            "fill": report["fill_frac"],
        },
        "frames": [
            {
                "direction": d,
                "index": c,
                "path": manifest_lib.relative_path(
                    frames_dir / f"{d}_{c:02d}.png", Path(args.out_base)),
                "rect": [c * frame_w, r * frame_h, frame_w, frame_h],
                "bbox": bboxes[d][c],
            }
            for r, d in enumerate(directions)
            for c in range(n_cols)
        ],
        "artifacts": {
            "sheet": manifest_lib.relative_path(REF_DIR / sheet_names[0][1], Path(args.out_base)),
            "sheet_2x": manifest_lib.relative_path(REF_DIR / sheet_names[1][1], Path(args.out_base)),
            "area_map": manifest_lib.relative_path(
                REF_DIR / f"run_area_map{suffix}.png", Path(args.out_base)),
            "report": manifest_lib.relative_path(report_path, Path(args.out_base)),
            "frames_dir": manifest_lib.relative_path(frames_dir, Path(args.out_base)),
        },
    }
    manifest_lib.write_manifest(manifest_path, manifest)
    print(f"  manifest: {manifest_path}")
    print(f"  gerou {n_rows * n_cols} frames normalizados + {n_rows} GIFs + report{suffix}.json")
    return 0
def load_cell(root: Path, r: int, c: int) -> Image.Image:
    return Image.open(root / f"row{r}_col{c}.png").convert("RGBA")
if __name__ == "__main__":
    sys.exit(main())
