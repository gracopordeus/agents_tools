"""Import an evenly spaced JPEG spritesheet into Sprite Lab GIF artifacts."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from PIL import ImageChops, ImageDraw

import sprite_render


IMPORT_SCHEMA = "sprite_lab.jpeg_sheet_import/v1"


def _transparent_background(
    image: Image.Image,
    threshold: int,
    background_key: str = "auto",
    magenta_spill_radius: int = 8,
    *,
    edge: Image.Image | None = None,
    edge_threshold: int = 180,
    enclosed_min_area: int = 32,
) -> Image.Image:
    """Clear border-connected pixels matching the cell's background color.

    The generated sheets may use either black or chroma-magenta backgrounds.
    Estimating the color from the border handles both cases while connectivity
    prevents similarly colored details inside the character from disappearing.
    """
    rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8).copy()
    rgb = rgba[..., :3].astype(np.float32)
    border = np.concatenate(
        [rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :]], axis=0
    )
    background = np.median(border, axis=0)
    candidate = np.linalg.norm(rgb - background, axis=2) <= float(threshold)
    background_is_magenta = (
        min(background[0], background[2]) - background[1] >= 100
        and abs(background[0] - background[2]) <= 40
    )
    if background_key not in {"auto", "magenta", "none"}:
        raise ValueError("background_key deve ser auto, magenta ou none")
    if magenta_spill_radius < 0:
        raise ValueError("magenta_spill_radius deve ser não negativo")
    if background_key == "magenta" or (background_key == "auto" and background_is_magenta):
        magenta_strength = np.minimum(rgb[..., 0], rgb[..., 2]) - rgb[..., 1]
        magenta_range = (
            (np.minimum(rgb[..., 0], rgb[..., 2]) >= 32)
            & (magenta_strength >= 8)
            & (np.abs(rgb[..., 0] - rgb[..., 2]) <= 80)
        )
        strong_magenta_range = (
            (np.minimum(rgb[..., 0], rgb[..., 2]) >= 160)
            & (magenta_strength >= 64)
            & (np.abs(rgb[..., 0] - rgb[..., 2]) <= 64)
        )
    height, width = candidate.shape
    background_connected = np.zeros_like(candidate, dtype=bool)
    pending: deque[tuple[int, int]] = deque()
    for x in range(width):
        pending.extend(((x, 0), (x, height - 1)))
    for y in range(height):
        pending.extend(((0, y), (width - 1, y)))

    while pending:
        x, y = pending.popleft()
        if x < 0 or y < 0 or x >= width or y >= height:
            continue
        if background_connected[y, x] or not candidate[y, x]:
            continue
        background_connected[y, x] = True
        pending.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))

    remove = background_connected
    if edge is not None and max(background) <= 32:
        remove |= _enclosed_background_mask(
            candidate,
            background_connected,
            edge,
            edge_threshold,
            enclosed_min_area,
        )
    if background_key == "magenta" or (background_key == "auto" and background_is_magenta):
        remove |= strong_magenta_range
        # Anti-aliased magenta spill is not close enough to the exact
        # background color for the flood fill. Restrict its removal to a
        # short exterior band so enclosed magenta details survive.
        exterior_band = background_connected.copy()
        for _ in range(magenta_spill_radius):
            expanded = exterior_band.copy()
            expanded[1:] |= exterior_band[:-1]
            expanded[:-1] |= exterior_band[1:]
            expanded[:, 1:] |= exterior_band[:, :-1]
            expanded[:, :-1] |= exterior_band[:, 1:]
            exterior_band = expanded
        remove |= magenta_range & exterior_band
    rgba[..., 3] = np.where(remove, 0, rgba[..., 3])
    return Image.fromarray(rgba, "RGBA")


def _teed_crop_bounds(
    edge: Image.Image,
    threshold: int,
    padding: int,
    border_margin: int = 8,
) -> tuple[int, int, int, int] | None:
    """Return a padded principal-area crop from a TEED probability map."""
    values = np.asarray(edge.convert("L"))
    height, width = values.shape
    if border_margin * 2 >= min(width, height):
        raise ValueError("border_margin deve deixar uma área interna na célula")
    interior = values[border_margin : height - border_margin, border_margin : width - border_margin]
    ys, xs = np.where(interior >= threshold)
    if not xs.size:
        return None
    return (
        max(0, int(xs.min()) + border_margin - padding),
        max(0, int(ys.min()) + border_margin - padding),
        min(width, int(xs.max()) + border_margin + padding + 1),
        min(height, int(ys.max()) + border_margin + padding + 1),
    )


def _enclosed_background_mask(
    candidate: np.ndarray,
    background_connected: np.ndarray,
    edge: Image.Image,
    edge_threshold: int,
    min_area: int,
    min_density: float = 0.2,
) -> np.ndarray:
    """Find large dark components enclosed by a TEED-supported silhouette."""
    enclosed = candidate & ~background_connected
    edge_values = np.asarray(edge.convert("L"))
    height, width = enclosed.shape
    visited = np.zeros_like(enclosed, dtype=bool)
    remove = np.zeros_like(enclosed, dtype=bool)
    pending: deque[tuple[int, int]] = deque()

    for start_y, start_x in zip(*np.where(enclosed)):
        if visited[start_y, start_x]:
            continue
        component: list[tuple[int, int]] = []
        pending.append((int(start_x), int(start_y)))
        visited[start_y, start_x] = True
        min_x = min_y = width
        max_x = max_y = 0
        while pending:
            x, y = pending.popleft()
            component.append((x, y))
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for next_x, next_y in (
                (x - 1, y),
                (x + 1, y),
                (x, y - 1),
                (x, y + 1),
            ):
                if (
                    0 <= next_x < width
                    and 0 <= next_y < height
                    and enclosed[next_y, next_x]
                    and not visited[next_y, next_x]
                ):
                    visited[next_y, next_x] = True
                    pending.append((next_x, next_y))

        area = len(component)
        box_area = (max_x - min_x + 1) * (max_y - min_y + 1)
        if area < min_area or area / box_area < min_density:
            continue
        component_mask = np.zeros_like(enclosed, dtype=bool)
        for x, y in component:
            component_mask[y, x] = True
        boundary = component_mask.copy()
        boundary[1:] |= component_mask[:-1]
        boundary[:-1] |= component_mask[1:]
        boundary[:, 1:] |= component_mask[:, :-1]
        boundary[:, :-1] |= component_mask[:, 1:]
        boundary &= ~component_mask
        if boundary.any() and np.percentile(edge_values[boundary], 75) >= edge_threshold * 0.7:
            remove |= component_mask
    return remove


def _remove_background_with_teed(
    image: Image.Image,
    edge: Image.Image,
    *,
    background_threshold: int,
    background_key: str,
    magenta_spill_radius: int,
    edge_threshold: int,
    edge_padding: int,
    edge_border_margin: int,
    enclosed_min_area: int,
) -> Image.Image:
    """Use TEED to crop the subject, then remove border-connected background."""
    cropped = _transparent_background(
        image,
        background_threshold,
        background_key,
        magenta_spill_radius,
        edge=edge,
        edge_threshold=edge_threshold,
        enclosed_min_area=enclosed_min_area,
    )
    bounds = _teed_crop_bounds(
        edge,
        edge_threshold,
        edge_padding,
        border_margin=edge_border_margin,
    )
    if bounds is None:
        return cropped
    alpha = cropped.getchannel("A")
    crop_mask = Image.new("L", cropped.size, 0)
    ImageDraw.Draw(crop_mask).rectangle(bounds, fill=255)
    alpha = ImageChops.multiply(alpha, crop_mask)
    cropped.putalpha(alpha)
    crop_mask.close()
    alpha.close()
    return cropped


def import_sheet(
    source: Path,
    output: Path,
    rows: int = 8,
    phases: int = 8,
    fps: float = 10.0,
    background_threshold: int = 64,
    background_key: str = "auto",
    magenta_spill_radius: int = 8,
    edge_detector: str = "none",
    teed_python: Path | None = None,
    teed_repo: Path | None = None,
    teed_checkpoint: Path | None = None,
    teed_threshold: int = 180,
    teed_padding: int = 2,
    teed_border_margin: int = 8,
    teed_enclosed_min_area: int = 32,
    denoiser: str = "none",
    waifu2x_python: Path | None = None,
    waifu2x_repo: Path | None = None,
    waifu2x_noise_level: int = 1,
    waifu2x_tile_size: int = 256,
    foreground_extractor: str = "none",
    birefnet_python: Path | None = None,
    birefnet_model: str = "ZhengPeng7/BiRefNet_lite",
    birefnet_revision: str = "7838f1c3472f827cd8ce13ab5ccc2ce48077360f",
    birefnet_threshold: float = 0.5,
    birefnet_input_size: int = 1024,
    output_cell_size: int | None = None,
) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(source)
    if not 1 <= rows <= len(sprite_render.DIRECTION_ROWS):
        raise ValueError("rows deve estar entre 1 e 8")
    if phases < 1:
        raise ValueError("phases deve ser positivo")
    if not 0 <= background_threshold <= 255:
        raise ValueError("background_threshold deve estar entre 0 e 255")
    if background_key not in {"auto", "magenta", "none"}:
        raise ValueError("background_key deve ser auto, magenta ou none")
    if magenta_spill_radius < 0:
        raise ValueError("magenta_spill_radius deve ser não negativo")
    if edge_detector not in {"none", "teed"}:
        raise ValueError("edge_detector deve ser none ou teed")
    if not 0 <= teed_threshold <= 255:
        raise ValueError("teed_threshold deve estar entre 0 e 255")
    if teed_padding < 0:
        raise ValueError("teed_padding deve ser não negativo")
    if teed_border_margin < 1:
        raise ValueError("teed_border_margin deve ser positivo")
    if teed_enclosed_min_area < 1:
        raise ValueError("teed_enclosed_min_area deve ser positivo")
    if denoiser not in {"none", "waifu2x-cunet"}:
        raise ValueError("denoiser deve ser none ou waifu2x-cunet")
    if waifu2x_noise_level not in range(4):
        raise ValueError("waifu2x_noise_level deve estar entre 0 e 3")
    if waifu2x_tile_size < 16:
        raise ValueError("waifu2x_tile_size deve ser pelo menos 16")
    if denoiser == "waifu2x-cunet" and not all((waifu2x_python, waifu2x_repo)):
        raise ValueError("Waifu2x exige waifu2x_python e waifu2x_repo")
    if foreground_extractor not in {"none", "birefnet-lite"}:
        raise ValueError("foreground_extractor deve ser none ou birefnet-lite")
    if foreground_extractor == "birefnet-lite" and birefnet_python is None:
        raise ValueError("BiRefNet-Lite exige birefnet_python")
    if foreground_extractor == "birefnet-lite" and edge_detector != "none":
        raise ValueError("BiRefNet-Lite substitui o TEED; use edge_detector=none")
    if not 0.0 <= birefnet_threshold <= 1.0:
        raise ValueError("birefnet_threshold deve estar entre 0 e 1")
    if birefnet_input_size < 32:
        raise ValueError("birefnet_input_size deve ser pelo menos 32")
    if output_cell_size is not None and output_cell_size < 1:
        raise ValueError("output_cell_size deve ser positivo")
    if edge_detector == "teed" and not all((teed_python, teed_repo, teed_checkpoint)):
        raise ValueError("TEED exige teed_python, teed_repo e teed_checkpoint")

    with Image.open(source) as opened:
        sheet = opened.convert("RGB")
    if sheet.width % phases or sheet.height % rows:
        raise ValueError("as dimensões da imagem devem ser divisíveis pela grade")

    cell_width = sheet.width // phases
    cell_height = sheet.height // rows
    if cell_width != cell_height:
        raise ValueError("o contrato do Sprite Lab exige células quadradas")

    output.mkdir(parents=True, exist_ok=True)
    teed_edges: dict[str, Image.Image] = {}
    teed_report: dict[str, Any] | None = None
    waifu2x_report: dict[str, Any] | None = None
    birefnet_report: dict[str, Any] | None = None
    needs_raw_cells = (
        edge_detector == "teed"
        or denoiser == "waifu2x-cunet"
        or foreground_extractor == "birefnet-lite"
    )
    temporary_context = (
        tempfile.TemporaryDirectory(prefix="sprite-lab-import-")
        if needs_raw_cells
        else None
    )
    if temporary_context is not None:
        raw_dir = Path(temporary_context.name) / "raw"
        raw_dir.mkdir()
        for row in range(rows):
            for column in range(phases):
                box = (
                    column * cell_width,
                    row * cell_height,
                    (column + 1) * cell_width,
                    (row + 1) * cell_height,
                )
                raw_cell = sheet.crop(box)
                raw_cell.save(raw_dir / f"row{row}_col{column}.png", format="PNG")
                raw_cell.close()
        processed_dir = raw_dir
        if denoiser == "waifu2x-cunet":
            processed_dir = output / "waifu2x_denoised"
            waifu2x_command = [
                str(waifu2x_python),
                str(Path(__file__).with_name("waifu2x_cunet_denoise.py")),
                str(raw_dir),
                str(processed_dir),
                "--nunif-repo",
                str(waifu2x_repo),
                "--noise-level",
                str(waifu2x_noise_level),
                "--tile-size",
                str(waifu2x_tile_size),
            ]
            completed = subprocess.run(
                waifu2x_command, capture_output=True, text=True, check=False
            )
            if completed.returncode != 0:
                temporary_context.cleanup()
                raise RuntimeError(
                    f"inferência Waifu2x falhou:\n{completed.stdout}\n{completed.stderr}"
                )
            waifu2x_report = json.loads(completed.stdout)
        if foreground_extractor == "birefnet-lite":
            birefnet_dir = output / "birefnet_foreground"
            birefnet_mask_dir = output / "birefnet_masks"
            birefnet_command = [
                str(birefnet_python),
                str(Path(__file__).with_name("birefnet_lite_remove.py")),
                str(processed_dir),
                str(birefnet_dir),
                "--mask-output",
                str(birefnet_mask_dir),
                "--model",
                birefnet_model,
                "--revision",
                birefnet_revision,
                "--threshold",
                str(birefnet_threshold),
                "--input-size",
                str(birefnet_input_size),
            ]
            completed = subprocess.run(
                birefnet_command, capture_output=True, text=True, check=False
            )
            if completed.returncode != 0:
                temporary_context.cleanup()
                raise RuntimeError(
                    f"inferência BiRefNet falhou:\n{completed.stdout}\n{completed.stderr}"
                )
            birefnet_report = json.loads(completed.stdout)
            processed_dir = birefnet_dir
    else:
        processed_dir = None

    if edge_detector == "teed":
        assert temporary_context is not None and processed_dir is not None
        edge_dir = output / "teed_edges"
        teed_command = [
            str(teed_python),
            str(Path(__file__).with_name("teed_edge_infer.py")),
            str(processed_dir),
            str(edge_dir),
            "--teed-repo",
            str(teed_repo),
            "--checkpoint",
            str(teed_checkpoint),
        ]
        completed = subprocess.run(teed_command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            temporary_context.cleanup()
            raise RuntimeError(f"inferência TEED falhou:\n{completed.stdout}\n{completed.stderr}")
        teed_report = json.loads(completed.stdout)
        for row in range(rows):
            for column in range(phases):
                name = f"row{row}_col{column}.png"
                with Image.open(edge_dir / name) as edge:
                    teed_edges[name] = edge.convert("L")
    cells: list[str] = []
    for row in range(rows):
        for column in range(phases):
            box = (
                column * cell_width,
                row * cell_height,
                (column + 1) * cell_width,
                (row + 1) * cell_height,
            )
            name = f"row{row}_col{column}.png"
            if processed_dir is not None:
                with Image.open(processed_dir / name) as opened:
                    raw_cell = opened.convert(
                        "RGBA" if foreground_extractor == "birefnet-lite" else "RGB"
                    )
            else:
                raw_cell = sheet.crop(box)
            if foreground_extractor == "birefnet-lite":
                cell = raw_cell.copy()
            elif edge_detector == "teed":
                cell = _remove_background_with_teed(
                    raw_cell,
                    teed_edges[name],
                    background_threshold=background_threshold,
                    background_key=background_key,
                    magenta_spill_radius=magenta_spill_radius,
                    edge_threshold=teed_threshold,
                    edge_padding=teed_padding,
                    edge_border_margin=teed_border_margin,
                    enclosed_min_area=teed_enclosed_min_area,
                )
            else:
                cell = _transparent_background(
                    raw_cell,
                    background_threshold,
                    background_key,
                    magenta_spill_radius,
                )
            raw_cell.close()
            final_cell_size = output_cell_size or cell_width
            if (
                cell.size != (final_cell_size, final_cell_size)
                or foreground_extractor == "birefnet-lite"
            ):
                resized = cell.resize(
                    (final_cell_size, final_cell_size), Image.Resampling.NEAREST
                )
                cell.close()
                cell = resized
            cell.save(output / name, format="PNG")
            cell.close()
            cells.append(name)

    if temporary_context is not None:
        temporary_context.cleanup()

    final_cell_size = output_cell_size or cell_width
    sprite_render._build_sheet(output, rows, phases, final_cell_size)
    directions = sprite_render.DIRECTION_ROWS[:rows]
    gifs = sprite_render._build_gifs(output, rows, phases, fps, directions)
    legacy = sprite_render._build_gif(output, phases, fps)
    diagonal, diagonal_sequence = sprite_render._build_upscaled_diagonal_gif(
        output, rows, phases, fps
    )
    metadata: dict[str, Any] = {
        "schema": IMPORT_SCHEMA,
        "source": str(source.resolve()),
        "source_size": list(sheet.size),
        "grid": [phases, rows],
        "source_cell_size": [cell_width, cell_height],
        "cell_size": [final_cell_size, final_cell_size],
        "fps": float(fps),
        "loop": True,
        "background_threshold": background_threshold,
        "background_key": background_key,
        "magenta_spill_radius": magenta_spill_radius,
        "edge_detector": edge_detector,
        "denoiser": denoiser,
        "waifu2x": waifu2x_report,
        "foreground_extractor": foreground_extractor,
        "birefnet": birefnet_report,
        "resize_filter": "nearest-neighbor",
        "teed_threshold": teed_threshold if edge_detector == "teed" else None,
        "teed_padding": teed_padding if edge_detector == "teed" else None,
        "teed_border_margin": teed_border_margin if edge_detector == "teed" else None,
        "teed_enclosed_min_area": (
            teed_enclosed_min_area if edge_detector == "teed" else None
        ),
        "teed": teed_report,
        "cells": cells,
        "spritesheet": "spritesheet.png",
        "gifs": {direction: path.name for direction, path in gifs.items()},
        "legacy_gif": legacy.name if legacy else None,
        "diagonal_gif": diagonal.name if diagonal else None,
        "diagonal_sequence": diagonal_sequence,
    }
    (output / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rows", type=int, default=8)
    parser.add_argument("--phases", type=int, default=8)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--background-threshold", type=int, default=64)
    parser.add_argument("--background-key", choices=("auto", "magenta", "none"), default="auto")
    parser.add_argument("--magenta-spill-radius", type=int, default=8)
    parser.add_argument("--edge-detector", choices=("none", "teed"), default="none")
    parser.add_argument("--teed-python", type=Path, default=None)
    parser.add_argument("--teed-repo", type=Path, default=None)
    parser.add_argument("--teed-checkpoint", type=Path, default=None)
    parser.add_argument("--teed-threshold", type=int, default=180)
    parser.add_argument("--teed-padding", type=int, default=2)
    parser.add_argument("--teed-border-margin", type=int, default=8)
    parser.add_argument("--teed-enclosed-min-area", type=int, default=32)
    parser.add_argument("--denoiser", choices=("none", "waifu2x-cunet"), default="none")
    parser.add_argument("--waifu2x-python", type=Path, default=None)
    parser.add_argument("--waifu2x-repo", type=Path, default=None)
    parser.add_argument("--waifu2x-noise-level", type=int, choices=range(4), default=1)
    parser.add_argument("--waifu2x-tile-size", type=int, default=256)
    parser.add_argument(
        "--foreground-extractor", choices=("none", "birefnet-lite"), default="none"
    )
    parser.add_argument("--birefnet-python", type=Path, default=None)
    parser.add_argument("--birefnet-model", default="ZhengPeng7/BiRefNet_lite")
    parser.add_argument(
        "--birefnet-revision",
        default="7838f1c3472f827cd8ce13ab5ccc2ce48077360f",
    )
    parser.add_argument("--birefnet-threshold", type=float, default=0.5)
    parser.add_argument("--birefnet-input-size", type=int, default=1024)
    parser.add_argument("--output-cell-size", type=int, default=None)
    args = parser.parse_args()
    metadata = import_sheet(
        args.source,
        args.output,
        rows=args.rows,
        phases=args.phases,
        fps=args.fps,
        background_threshold=args.background_threshold,
        background_key=args.background_key,
        magenta_spill_radius=args.magenta_spill_radius,
        edge_detector=args.edge_detector,
        teed_python=args.teed_python,
        teed_repo=args.teed_repo,
        teed_checkpoint=args.teed_checkpoint,
        teed_threshold=args.teed_threshold,
        teed_padding=args.teed_padding,
        teed_border_margin=args.teed_border_margin,
        teed_enclosed_min_area=args.teed_enclosed_min_area,
        denoiser=args.denoiser,
        waifu2x_python=args.waifu2x_python,
        waifu2x_repo=args.waifu2x_repo,
        waifu2x_noise_level=args.waifu2x_noise_level,
        waifu2x_tile_size=args.waifu2x_tile_size,
        foreground_extractor=args.foreground_extractor,
        birefnet_python=args.birefnet_python,
        birefnet_model=args.birefnet_model,
        birefnet_revision=args.birefnet_revision,
        birefnet_threshold=args.birefnet_threshold,
        birefnet_input_size=args.birefnet_input_size,
        output_cell_size=args.output_cell_size,
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
