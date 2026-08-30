"""Orchestrate deterministic sprite-sheet renders for Sprite Lab."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import relationship_catalog as rel
import semantic_preview
import composition_schema
import model_cache
import render_profile
from direction_contract import (
    DIRECTION_CONTRACT,
    DIRECTION_LABELS,
    DIRECTION_ROWS,
    ROTATION_SEQUENCE,
)


SPRITE_SCHEMA = "sprite_lab.sprite_render/v1"
PROFILES: dict[str, tuple[int, int]] = {
    "8x8": (8, 8),
    "8x12": (8, 12),
    "8x16": (8, 16),
    "5x9": (5, 9),
}
# The renderer samples rows in this order.  Rows are deliberately named r1…r8
# in generated files and metadata: the row index is the stable contract while
# the camera yaw remains an implementation detail of the render worker.
GIF_DIRECTION_ORDER = DIRECTION_ROWS
AI_DIRECTION_ROWS = ("r1", "r2", "r5", "r6", "r7")
AI_BASE_PAGE_SIZE = 2048
AI_BASE_CELL_SIZE = 672
AI_BASE_GRID = 3
DEFAULT_FPS = 10
DEFAULT_CELL = 256
DEFAULT_UPSCALE = 2
SPRITE_WORK = Path(__file__).resolve().parent / "work" / "sprite-renders"
BLENDER_WORKER = Path(__file__).resolve().with_name("blender_sprite_render.py")


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _worker_error(completed: subprocess.CompletedProcess[str], output: Path) -> str:
    """Extract a useful Blender/Python error when the result file is missing."""
    chunks = [completed.stdout or "", completed.stderr or ""]
    worker_log = output / "worker.log"
    if worker_log.is_file():
        chunks.append(worker_log.read_text(encoding="utf-8", errors="replace"))
    lines = "\n".join(chunks).strip().splitlines()
    error_prefixes = (
        "RuntimeError:",
        "ValueError:",
        "FileNotFoundError:",
        "KeyError:",
        "TypeError:",
    )
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith(error_prefixes):
            return stripped
    return lines[-1].strip() if lines else "worker encerrou sem produzir metadados"


def render_dimensions(payload: dict[str, Any]) -> tuple[str, int, int]:
    profile = str(payload.get("profile", "8x8"))
    if profile not in PROFILES:
        raise ValueError(f"perfil de sprite inválido: {profile}")
    default_rows, default_phases = PROFILES[profile]
    rows = int(payload.get("rows", default_rows))
    phases = int(payload.get("phases", default_phases))
    if not 1 <= rows <= 8:
        raise ValueError("rows deve estar entre 1 e 8")
    if not 1 <= phases <= 32:
        raise ValueError("phases deve estar entre 1 e 32")
    return profile, rows, phases


def _relationships() -> list[dict[str, Any]]:
    return [
        item
        for item in rel.load_relationship_state(rel.DEFAULT_OUTPUT).get("relationships", [])
        if isinstance(item, dict)
    ]


def _relationship(payload: dict[str, Any]) -> dict[str, Any]:
    relationship_id = str(payload.get("relationship_id") or "")
    if not relationship_id:
        raise ValueError("relationship_id é obrigatório")
    relationship = next(
        (item for item in _relationships() if str(item.get("id")) == relationship_id),
        None,
    )
    if relationship is None:
        raise KeyError(f"composição não encontrada: {relationship_id}")
    return relationship


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row.get("id")): row for row in rows if row.get("id")}


def _build_sheet(output: Path, rows: int, phases: int, resolution: int) -> Path:
    from PIL import Image

    sheet = Image.new("RGBA", (phases * resolution, rows * resolution), (0, 0, 0, 0))
    for row in range(rows):
        for column in range(phases):
            cell_path = output / f"row{row}_col{column}.png"
            if not cell_path.is_file():
                raise RuntimeError(f"célula ausente após renderização: {cell_path.name}")
            with Image.open(cell_path) as cell:
                sheet.paste(cell.convert("RGBA"), (column * resolution, row * resolution))
    path = output / "spritesheet.png"
    sheet.save(path)
    return path


def _gif_frame(image: Any) -> Any:
    """Quantize RGBA to P while reserving palette index 0 for transparency."""
    from PIL import Image

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    # A sentinel keeps clear pixels from consuming the black palette entry;
    # their final indices are forced to zero below because GIF has binary
    # transparency and cannot represent the original partial alpha channel.
    rgb.paste(
        (255, 0, 255),
        mask=alpha.point(lambda value: 255 if value == 0 else 0),
    )
    quantized = rgb.quantize(
        colors=255,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    pixel_data = getattr(quantized, "get_flattened_data", quantized.getdata)
    alpha_data = getattr(alpha, "get_flattened_data", alpha.getdata)
    quantized_indices = list(pixel_data())
    alpha_values = list(alpha_data())
    indices = [
        0 if alpha_value == 0 else palette_index + 1
        for palette_index, alpha_value in zip(quantized_indices, alpha_values)
    ]
    indexed = Image.new("P", rgba.size)
    indexed.putdata(indices)
    palette = (quantized.getpalette() or [])[: 255 * 3]
    palette.extend([0, 0, 0] * (255 - len(palette) // 3))
    indexed.putpalette([0, 0, 0] + palette[: 255 * 3])
    indexed.info["transparency"] = 0
    indexed.info["disposal"] = 2
    return indexed


def _write_gif(
    frames: list[Path], path: Path, fps: float, upscale: int = 1
) -> Path | None:
    if not frames or not all(frame.is_file() for frame in frames):
        return None
    from PIL import Image

    images = []
    for frame in frames:
        with Image.open(frame) as source:
            image = source.convert("RGBA")
            if upscale != 1:
                image = image.resize(
                    (image.width * upscale, image.height * upscale),
                    Image.Resampling.NEAREST,
                )
            images.append(_gif_frame(image))
            image.close()
    images[0].save(
        path,
        save_all=True,
        append_images=images[1:],
        duration=max(20, round(1000 / max(float(fps), 1.0))),
        loop=0,
        disposal=2,
        transparency=0,
        optimize=False,
    )
    for image in images:
        image.close()
    return path


def _build_gifs(
    output: Path,
    rows: int,
    phases: int,
    fps: float,
    directions: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Path]:
    """Build one inspection GIF per direction, retaining the row order."""
    gifs: dict[str, Path] = {}
    direction_rows = tuple(directions or DIRECTION_ROWS[:rows])
    for row, direction in enumerate(direction_rows[:rows]):
        frames = [output / f"row{row}_col{column}.png" for column in range(phases)]
        path = _write_gif(frames, output / f"animation_{direction}.gif", fps)
        if path:
            gifs[direction] = path
    return gifs


def _build_ai_base_pages(
    output: Path,
    directions: tuple[str, ...] | list[str],
    phases: int,
    resolution: int,
) -> dict[str, Path]:
    """Build one 2048px 3x3 AI reference page per unique direction."""
    from PIL import Image

    if tuple(directions) != AI_DIRECTION_ROWS:
        raise ValueError("a base IA exige as cinco direções canônicas")
    if phases != 9 or resolution != AI_BASE_CELL_SIZE:
        raise ValueError("a base IA exige 9 fases em células de 672px")
    occupied = AI_BASE_GRID * resolution
    margin = (AI_BASE_PAGE_SIZE - occupied) // 2
    pages: dict[str, Path] = {}
    for row, direction in enumerate(directions):
        page = Image.new("RGBA", (AI_BASE_PAGE_SIZE, AI_BASE_PAGE_SIZE), (0, 0, 0, 0))
        for column in range(phases):
            cell_path = output / f"row{row}_col{column}.png"
            if not cell_path.is_file():
                raise RuntimeError(f"célula ausente para base IA: {cell_path.name}")
            with Image.open(cell_path) as cell:
                frame = cell.convert("RGBA")
                if frame.size != (resolution, resolution):
                    raise RuntimeError(
                        f"célula {cell_path.name} tem dimensão inválida: {frame.size}"
                    )
                x = margin + (column % AI_BASE_GRID) * resolution
                y = margin + (column // AI_BASE_GRID) * resolution
                page.paste(frame, (x, y), frame)
        path = output / f"ai_base_{direction}.png"
        page.save(path, format="PNG")
        pages[direction] = path
    return pages


def _build_upscaled_diagonal_gif(
    output: Path,
    rows: int,
    phases: int,
    fps: float,
    upscale: int = DEFAULT_UPSCALE,
) -> tuple[Path | None, list[dict[str, Any]]]:
    """Build a clockwise camera-rotation preview using one phase per row."""
    sequence = [
        {"column": phase + 1, "row": row_index + 1, "source": f"row{row_index}_col{phase}.png"}
        for row_index, (row, phase) in enumerate(ROTATION_SEQUENCE)
        if row_index < rows and phase < phases
    ]
    frames = [output / item["source"] for item in sequence]
    path = _write_gif(
        frames,
        output / "animation_diagonal_upscaled.gif",
        fps,
        upscale=max(1, int(upscale)),
    )
    return path, sequence


def _build_gif(output: Path, phases: int, fps: float) -> Path | None:
    """Backward-compatible alias for the first (west) direction GIF."""
    return _write_gif(
        [output / f"row0_col{column}.png" for column in range(phases)],
        output / "animation.gif",
        fps,
    )


def _validate_cells_not_clipped(output: Path, rows: int, phases: int) -> None:
    """Reject locked-profile renders touching a cell edge instead of auto-zooming."""
    from PIL import Image

    clipped: list[str] = []
    for row in range(rows):
        for column in range(phases):
            path = output / f"row{row}_col{column}.png"
            with Image.open(path) as image:
                alpha = image.convert("RGBA").getchannel("A")
                bounds = alpha.getbbox()
                if bounds is None:
                    continue
                left, top, right, bottom = bounds
                if left == 0 or top == 0 or right == image.width or bottom == image.height:
                    clipped.append(path.name)
    if clipped:
        sample = ", ".join(clipped[:5])
        raise RuntimeError(
            "render profile fixo recortou conteúdo na borda "
            f"({sample}); aumente ortho_scale no manifesto"
        )


def generate_sprite_render(
    payload: dict[str, Any],
    job_id: str,
    output_root: Path | None = None,
    blender: str | None = None,
    timeout: float = 3600.0,
) -> dict[str, Any]:
    render_mode = str(payload.get("render_mode", "runtime") or "runtime").strip().casefold()
    if render_mode not in {"runtime", "ai_base"}:
        raise ValueError("render_mode deve ser 'runtime' ou 'ai_base'")
    profile, rows, phases = render_dimensions(payload)
    if render_mode == "ai_base" and (rows, phases) != (5, 9):
        raise ValueError("a Base IA exige o perfil 5x9")
    resolution = max(128, min(2048, int(payload.get("resolution", DEFAULT_CELL))))
    fps = max(1.0, min(60.0, float(payload.get("fps", DEFAULT_FPS))))
    locked_profile = None
    locked_profile_id = str(payload.get("render_profile_id") or "").strip()
    camera_preset_id = str(payload.get("camera_preset") or "").strip().casefold()
    camera_preset = render_profile.camera_preset(camera_preset_id) if camera_preset_id else None
    optimize_ortho_scale = payload.get("optimize_ortho_scale")
    if optimize_ortho_scale is not None and not isinstance(optimize_ortho_scale, bool):
        raise ValueError("optimize_ortho_scale deve ser booleano")
    light_preset = str(payload.get("light_preset", "default") or "default").strip().casefold()
    if light_preset not in {"default", "custom"}:
        raise ValueError("light_preset deve ser 'default' ou 'custom'")
    light_origin = payload.get("light_origin", [4.0, -4.0, 6.0])
    if not isinstance(light_origin, (list, tuple)) or len(light_origin) != 3:
        raise ValueError("light_origin deve conter três números")
    try:
        light_origin = [float(value) for value in light_origin]
    except (TypeError, ValueError) as exc:
        raise ValueError("light_origin deve conter três números") from exc
    if not all(math.isfinite(value) for value in light_origin):
        raise ValueError("light_origin deve conter números finitos")
    try:
        light_intensity = float(payload.get("light_intensity", 3.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("light_intensity deve ser um número") from exc
    if not math.isfinite(light_intensity) or light_intensity <= 0.0:
        raise ValueError("light_intensity deve ser maior que zero")
    if locked_profile_id:
        locked_profile = render_profile.load(locked_profile_id)
        if camera_preset:
            locked_profile = {
                **locked_profile,
                "camera_preset": camera_preset["id"],
                "camera_elevation": camera_preset["elevation"],
                "camera_azimuth": camera_preset["azimuth"],
                "ortho_scale": camera_preset["ortho_scale"],
            }
        if render_mode == "ai_base":
            base_resolution = float(locked_profile["cell_size"][0])
            scale = AI_BASE_CELL_SIZE / max(base_resolution, 1.0)
            locked_profile = {
                **locked_profile,
                "id": f"{locked_profile['id']}_ai_base",
                "cell_size": [AI_BASE_CELL_SIZE, AI_BASE_CELL_SIZE],
                "cell_size_mode": "fixed",
                "ortho_scale_mode": "fixed",
                "directions": 5,
                "phases": 9,
                "foot_anchor": [
                    round(float(locked_profile["foot_anchor"][0]) * scale),
                    round(float(locked_profile["foot_anchor"][1]) * scale),
                ],
                "horizontal_margin_px": float(locked_profile.get("horizontal_margin_px", 1.0)) * scale,
                "vertical_margin_px": float(locked_profile.get("vertical_margin_px", 1.0)) * scale,
            }
        if optimize_ortho_scale is not None:
            locked_profile = {
                **locked_profile,
                "ortho_scale_mode": "fit" if optimize_ortho_scale else "fixed",
            }
        settings = render_profile.apply_to_settings(
            locked_profile,
            {
                "resolution": resolution,
                "rows": rows,
                "phases": phases,
                "elevation": float(payload.get("elevation", 35.264)),
                "azimuth": float(payload.get("azimuth", 45.0)),
            },
        )
        resolution = settings["resolution"]
        rows = settings["rows"]
        phases = settings["phases"]
        profile = next(
            (
                name
                for name, dimensions in PROFILES.items()
                if dimensions == (rows, phases)
            ),
            f"{rows}x{phases}",
        )
    relationship = _relationship(payload)
    assets_catalog, animations_catalog = semantic_preview.load_manifests()
    assets = _by_id(assets_catalog.get("assets", []))
    animations = _by_id(animations_catalog.get("animations", []))

    character_id = str(relationship.get("character_asset_id") or "")
    animation_id = str(relationship.get("animation_id") or "")
    character = assets.get(character_id)
    animation = animations.get(animation_id)
    if character is None or animation is None:
        raise KeyError("composição aponta para um mesh ou Action inexistente")
    animation_asset = assets.get(str(animation.get("asset_id")))
    if animation_asset is None:
        raise KeyError("Action aponta para um asset inexistente")
    components = composition_schema.normalize_components(relationship)
    component_requests = []
    for component in components:
        component_asset = assets.get(str(component["asset_id"]))
        if component_asset is None:
            raise KeyError(
                f"composição aponta para um componente inexistente: {component['id']}"
            )
        # The browser preview resolves the canonical model through model_cache
        # (an existing GLB variant when available, otherwise a normalized FBX
        # conversion). Blender must consume the same artifact; importing the
        # raw FBX here can change the authored coordinate basis of hand props.
        component_path = model_cache.model_path(str(component_asset["id"]))
        component_requests.append(
            {
                **component,
                "path": str(component_path),
            }
        )

    character_path = model_cache.model_path(character_id)
    animation_path = model_cache.model_path(str(animation_asset["id"]))
    output = (output_root or SPRITE_WORK / job_id).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    request = {
        "character_path": str(character_path),
        "animation_path": str(animation_path),
        "action_name": animation.get("action_name") or animation.get("clip_name"),
        "components": component_requests,
        "weapon_hand": payload.get("weapon_hand", "right"),
        "weapon_height_ratio": float(payload.get("weapon_height_ratio", 0.8)),
        "weapon_rotation": payload.get("weapon_rotation", [0.0, 0.0, 0.0]),
        "weapon_offset": payload.get("weapon_offset"),
        "resolution": resolution,
        "rows": rows,
        "phases": phases,
        "render_mode": render_mode,
        "direction_rows": list(AI_DIRECTION_ROWS if render_mode == "ai_base" else DIRECTION_ROWS[:rows]),
        "direction_contract": DIRECTION_CONTRACT,
        "fps": fps,
        "camera_preset": camera_preset["id"] if camera_preset else None,
        "light_preset": light_preset,
        "light_origin": light_origin,
        "light_origin_mode": "camera",
        "light_intensity": light_intensity,
        "elevation": (
            camera_preset["elevation"]
            if camera_preset
            else locked_profile["camera_elevation"]
            if locked_profile
            else float(payload.get("elevation", 35.264))
        ),
        "azimuth": (
            camera_preset["azimuth"]
            if camera_preset
            else locked_profile["camera_azimuth"]
            if locked_profile
            else float(payload.get("azimuth", 45.0))
        ),
        "render_profile": locked_profile,
        "output": str(output),
    }
    request_path = output / "request.json"
    write_json_atomic(request_path, request)
    blender_command = blender or os.environ.get("SPRITE_LAB_BLENDER", "blender")
    executable = shutil.which(blender_command) or blender_command
    command = [
        executable,
        "--background",
        "--factory-startup",
        "--python",
        str(BLENDER_WORKER),
        "--",
        "--request",
        str(request_path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    (output / "worker.log").write_text(
        (completed.stdout or "") + "\n" + (completed.stderr or ""), encoding="utf-8"
    )
    if completed.returncode != 0:
        raise RuntimeError(_worker_error(completed, output))
    result_path = Path(str(request_path) + ".result.json")
    if not result_path.is_file():
        raise RuntimeError(_worker_error(completed, output))
    worker_report = json.loads(result_path.read_text(encoding="utf-8"))
    worker_cell = worker_report.get("cell")
    if (
        isinstance(worker_cell, list)
        and len(worker_cell) == 2
        and worker_cell[0] == worker_cell[1]
    ):
        resolution = int(worker_cell[0])
    if locked_profile:
        _validate_cells_not_clipped(output, rows, phases)
    sheet = _build_sheet(output, rows, phases, resolution)
    direction_rows = tuple(
        AI_DIRECTION_ROWS if render_mode == "ai_base" else DIRECTION_ROWS[:rows]
    )
    direction_gifs = _build_gifs(output, rows, phases, fps, directions=direction_rows)
    ai_base_pages = (
        _build_ai_base_pages(output, direction_rows, phases, resolution)
        if render_mode == "ai_base"
        else {}
    )
    upscaled_gif, upscaled_sequence = (
        (None, [])
        if render_mode == "ai_base"
        else _build_upscaled_diagonal_gif(output, rows, phases, fps)
    )
    # Keep the legacy path for existing clients; new clients should consume
    # `gifs`, which contains every rendered direction.
    gif = direction_gifs.get(DIRECTION_ROWS[0])
    if gif:
        legacy_gif = output / "animation.gif"
        if legacy_gif != gif:
            legacy_gif.write_bytes(gif.read_bytes())
        gif = legacy_gif
    report = {
        "schema": SPRITE_SCHEMA,
        "job_id": job_id,
        "relationship_id": relationship["id"],
        "semantic_name": relationship.get("semantic_name", ""),
        "profile": profile,
        "rows": rows,
        "phases": phases,
        "resolution": resolution,
        "fps": fps,
        "render_mode": render_mode,
        "render_profile": locked_profile,
        "spritesheet_generated": True,
        "worker": worker_report,
        "spritesheet": str(sheet),
        "gif": str(gif) if gif else None,
        "gifs": {direction: str(path) for direction, path in direction_gifs.items()},
        "ai_base_pages": {direction: str(path) for direction, path in ai_base_pages.items()},
        "ai_base_contract": (
            {
                "page_size": [AI_BASE_PAGE_SIZE, AI_BASE_PAGE_SIZE],
                "cell_size": [AI_BASE_CELL_SIZE, AI_BASE_CELL_SIZE],
                "grid": [AI_BASE_GRID, AI_BASE_GRID],
                "directions": list(AI_DIRECTION_ROWS),
                "phases": 9,
                "mirrors": {"r3": "r1", "r4": "r2", "r8": "r6"},
            }
            if render_mode == "ai_base"
            else None
        ),
        "upscaled_gif": str(upscaled_gif) if upscaled_gif else None,
        "upscaled_gif_scale": DEFAULT_UPSCALE,
        "upscaled_gif_sequence": upscaled_sequence,
        "metadata": str(output / "render_metadata.json"),
    }
    write_json_atomic(output / "render.json", report)
    return report
