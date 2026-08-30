"""Persistent camera and framing contract for consistent Sprite Lab renders."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


RENDER_PROFILE_SCHEMA = "sprite_lab.render_profile/v1"
PROFILE_ROOT = Path(__file__).resolve().parent / "state" / "render-profiles"
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")

CAMERA_PRESETS: dict[str, dict[str, Any]] = {
    # `ortho_scale` is calibrated from the complete 8-direction/8-phase
    # envelope of the reference composition.  The values are filled after
    # running the Blender calibration job; they are absolute world spans,
    # never percentages of the isometric camera.
    "isometric": {
        "label": "Isométrico", "elevation": 35.264, "azimuth": 45.0,
        "ortho_scale": 2.57705670238966,
        "profile_id": "hero_reference_v1",
    },
    "platform": {
        "label": "Plataforma", "elevation": 0.0, "azimuth": 0.0,
        "ortho_scale": 2.4759974992607696,
        "profile_id": "hero_reference_v1_platform",
    },
    "frontal": {
        "label": "Frontal", "elevation": 0.0, "azimuth": 90.0,
        "ortho_scale": 2.4759971345088396,
        "profile_id": "hero_reference_v1_frontal",
    },
    "three_quarter": {
        "label": "3/4", "elevation": 20.0, "azimuth": 45.0,
        "ortho_scale": 2.5102688002871325,
        "profile_id": "hero_reference_v1_three_quarter",
    },
    "diagonal": {
        "label": "Diagonal 45°", "elevation": 0.0, "azimuth": 45.0,
        "ortho_scale": 2.4759972560928163,
        "profile_id": "hero_reference_v1_diagonal",
    },
    "top_down": {
        "label": "Top-down", "elevation": 80.0, "azimuth": 45.0,
        "ortho_scale": 2.6732591727815302,
        "profile_id": "hero_reference_v1_top_down",
    },
}


def _finite_number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} deve ser um número finito") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} deve ser um número finito")
    return result


def _integer_pair(value: Any, field: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{field} deve conter dois inteiros")
    if any(isinstance(item, bool) for item in value):
        raise ValueError(f"{field} deve conter dois inteiros")
    try:
        result = [int(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} deve conter dois inteiros") from exc
    if any(float(original) != converted for original, converted in zip(value, result)):
        raise ValueError(f"{field} deve conter dois inteiros")
    return result


def normalize_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("render_profile deve ser um objeto")
    if value.get("schema") != RENDER_PROFILE_SCHEMA:
        raise ValueError(f"schema de render profile inválido: {value.get('schema')!r}")
    profile_id = str(value.get("id") or "").strip()
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError("render_profile.id deve usar lowercase, números, _ ou -")
    cell_size = _integer_pair(value.get("cell_size"), "cell_size")
    if cell_size[0] != cell_size[1] or not 128 <= cell_size[0] <= 2048:
        raise ValueError("cell_size deve ser quadrado e ficar entre 128 e 2048")
    cell_size_mode = str(value.get("cell_size_mode", "fixed")).strip().casefold()
    if cell_size_mode not in {"fixed", "fit"}:
        raise ValueError("cell_size_mode deve ser 'fixed' ou 'fit'")
    ortho_scale_mode = str(value.get("ortho_scale_mode", "fixed")).strip().casefold()
    if ortho_scale_mode not in {"fixed", "fit"}:
        raise ValueError("ortho_scale_mode deve ser 'fixed' ou 'fit'")
    cell_size_quantum = value.get("cell_size_quantum", 16)
    if isinstance(cell_size_quantum, bool):
        raise ValueError("cell_size_quantum deve ser um inteiro")
    try:
        cell_size_quantum = int(cell_size_quantum)
    except (TypeError, ValueError) as exc:
        raise ValueError("cell_size_quantum deve ser um inteiro") from exc
    if cell_size_quantum <= 0 or cell_size_quantum > 2048:
        raise ValueError("cell_size_quantum deve ficar entre 1 e 2048")
    foot_anchor = _integer_pair(value.get("foot_anchor"), "foot_anchor")
    if not 0 <= foot_anchor[0] < cell_size[0] or not 0 <= foot_anchor[1] < cell_size[1]:
        raise ValueError("foot_anchor deve ficar dentro da célula")
    ortho_scale = _finite_number(value.get("ortho_scale"), "ortho_scale")
    if ortho_scale <= 0.0:
        raise ValueError("ortho_scale deve ser maior que zero")
    dynamic_x = value.get("dynamic_x", False)
    if not isinstance(dynamic_x, bool):
        raise ValueError("dynamic_x deve ser booleano")
    horizontal_margin_px = _finite_number(
        value.get("horizontal_margin_px", 1.0), "horizontal_margin_px"
    )
    if horizontal_margin_px < 0.0 or horizontal_margin_px >= cell_size[0] / 2.0:
        raise ValueError("horizontal_margin_px deve ficar dentro da célula")
    dynamic_y = value.get("dynamic_y", False)
    if not isinstance(dynamic_y, bool):
        raise ValueError("dynamic_y deve ser booleano")
    vertical_margin_px = _finite_number(
        value.get("vertical_margin_px", 1.0), "vertical_margin_px"
    )
    if vertical_margin_px < 0.0 or vertical_margin_px >= cell_size[1] / 2.0:
        raise ValueError("vertical_margin_px deve ficar dentro da célula")
    directions = int(value.get("directions", 8))
    phases = int(value.get("phases", 8))
    if directions not in {5, 8}:
        raise ValueError("directions deve ser 5 ou 8 no contrato atual")
    if not 1 <= phases <= 32:
        raise ValueError("phases deve ficar entre 1 e 32")
    camera_preset = str(value.get("camera_preset") or "").strip().casefold() or None
    if camera_preset is not None and camera_preset not in CAMERA_PRESETS:
        raise ValueError(f"camera_preset inválido: {camera_preset!r}")
    return {
        "schema": RENDER_PROFILE_SCHEMA,
        "id": profile_id,
        "cell_size": cell_size,
        "cell_size_mode": cell_size_mode,
        "ortho_scale_mode": ortho_scale_mode,
        "cell_size_quantum": cell_size_quantum,
        "ortho_scale": ortho_scale,
        "dynamic_x": dynamic_x,
        "horizontal_margin_px": horizontal_margin_px,
        "dynamic_y": dynamic_y,
        "vertical_margin_px": vertical_margin_px,
        "foot_anchor": foot_anchor,
        "camera_elevation": _finite_number(
            value.get("camera_elevation", 35.264), "camera_elevation"
        ),
        "camera_azimuth": _finite_number(
            value.get("camera_azimuth", 45.0), "camera_azimuth"
        ),
        "camera_preset": camera_preset,
        "directions": directions,
        "phases": phases,
        "ground_z": _finite_number(value.get("ground_z", 0.0), "ground_z"),
    }


def camera_preset(preset_id: str) -> dict[str, Any]:
    """Return a copy of a supported 2D camera preset."""
    key = str(preset_id or "").strip().casefold()
    preset = CAMERA_PRESETS.get(key)
    if preset is None:
        raise ValueError(f"camera_preset inválido: {preset_id!r}")
    return {"id": key, **preset}


def list_camera_presets() -> list[dict[str, Any]]:
    return [{"id": key, **value} for key, value in CAMERA_PRESETS.items()]


def _fit_axis_offset(
    minimum_x: float,
    maximum_x: float,
    *,
    view_size: float,
    pixel_size: int,
    margin_px: float = 1.0,
) -> float:
    minimum_x = _finite_number(minimum_x, "minimum_x")
    maximum_x = _finite_number(maximum_x, "maximum_x")
    if maximum_x < minimum_x:
        raise ValueError("maximum_x deve ser maior ou igual a minimum_x")
    view_size = _finite_number(view_size, "view_size")
    if view_size <= 0.0:
        raise ValueError("view_size deve ser maior que zero")
    if not isinstance(pixel_size, int) or isinstance(pixel_size, bool) or pixel_size <= 0:
        raise ValueError("pixel_size deve ser um inteiro positivo")
    margin_px = _finite_number(margin_px, "margin_px")
    if margin_px < 0.0 or margin_px >= pixel_size / 2.0:
        raise ValueError("margin_px deve ficar dentro da célula")
    margin_world = view_size * margin_px / pixel_size
    safe_minimum = -view_size / 2.0 + margin_world
    safe_maximum = view_size / 2.0 - margin_world
    safe_width = safe_maximum - safe_minimum
    if maximum_x - minimum_x > safe_width + 1e-9:
        raise ValueError(
            "conteúdo projetado excede a área disponível"
        )
    if minimum_x < safe_minimum:
        return safe_minimum - minimum_x
    if maximum_x > safe_maximum:
        return safe_maximum - maximum_x
    return 0.0


def horizontal_fit_offset(
    minimum_x: float,
    maximum_x: float,
    *,
    ortho_scale: float,
    cell_size: list[int] | tuple[int, int],
    margin_px: float = 1.0,
) -> float:
    """Return the smallest horizontal translation that fits a projected box.

    The orthographic scale controls the vertical view. For a square cell the
    horizontal view has the same world width; the aspect-ratio calculation is
    kept here so a non-square profile remains deterministic. The function only
    compensates overflow and raises when the projected content is wider than
    the available view, preserving the locked scale instead of shrinking it.
    """
    dimensions = _integer_pair(cell_size, "cell_size")
    if any(item <= 0 for item in dimensions):
        raise ValueError("cell_size deve conter valores positivos")
    ortho_scale = _finite_number(ortho_scale, "ortho_scale")
    if ortho_scale <= 0.0:
        raise ValueError("ortho_scale deve ser maior que zero")
    return _fit_axis_offset(
        minimum_x,
        maximum_x,
        view_size=ortho_scale * dimensions[0] / dimensions[1],
        pixel_size=dimensions[0],
        margin_px=margin_px,
    )


def vertical_fit_offset(
    minimum_y: float,
    maximum_y: float,
    *,
    ortho_scale: float,
    cell_size: list[int] | tuple[int, int],
    margin_px: float = 1.0,
) -> float:
    """Return the smallest vertical translation that fits a projected box."""
    dimensions = _integer_pair(cell_size, "cell_size")
    if any(item <= 0 for item in dimensions):
        raise ValueError("cell_size deve conter valores positivos")
    ortho_scale = _finite_number(ortho_scale, "ortho_scale")
    if ortho_scale <= 0.0:
        raise ValueError("ortho_scale deve ser maior que zero")
    return _fit_axis_offset(
        minimum_y,
        maximum_y,
        view_size=ortho_scale,
        pixel_size=dimensions[1],
        margin_px=margin_px,
    )


def fitted_cell_size(
    maximum_width_world: float,
    maximum_height_world: float,
    *,
    base_cell_size: int,
    ortho_scale: float,
    quantum: int = 16,
    padding_px: float = 2.0,
) -> tuple[int, float]:
    """Choose a uniform cell and matching ortho scale at fixed pixel density."""
    maximum_width_world = _finite_number(maximum_width_world, "maximum_width_world")
    maximum_height_world = _finite_number(maximum_height_world, "maximum_height_world")
    if maximum_width_world < 0.0 or maximum_height_world < 0.0:
        raise ValueError("as extensões projetadas não podem ser negativas")
    if isinstance(base_cell_size, bool) or not isinstance(base_cell_size, int):
        raise ValueError("base_cell_size deve ser um inteiro")
    if base_cell_size <= 0:
        raise ValueError("base_cell_size deve ser positivo")
    ortho_scale = _finite_number(ortho_scale, "ortho_scale")
    if ortho_scale <= 0.0:
        raise ValueError("ortho_scale deve ser maior que zero")
    if isinstance(quantum, bool) or not isinstance(quantum, int) or quantum <= 0:
        raise ValueError("quantum deve ser um inteiro positivo")
    padding_px = _finite_number(padding_px, "padding_px")
    if padding_px < 0.0:
        raise ValueError("padding_px não pode ser negativo")
    pixels_per_world = base_cell_size / ortho_scale
    required = max(maximum_width_world, maximum_height_world) * pixels_per_world
    required += 2.0 * padding_px
    cell_size = max(base_cell_size, math.ceil(required / quantum) * quantum)
    effective_ortho_scale = cell_size / pixels_per_world
    return cell_size, effective_ortho_scale


def optimized_ortho_scale(
    maximum_width_world: float,
    maximum_height_world: float,
    *,
    cell_size: list[int] | tuple[int, int],
    minimum_ortho_scale: float,
    horizontal_margin_px: float = 1.0,
    vertical_margin_px: float = 1.0,
    safety_px: float = 0.0,
) -> float:
    """Return the smallest orthographic scale that fits a projected envelope."""
    maximum_width_world = _finite_number(maximum_width_world, "maximum_width_world")
    maximum_height_world = _finite_number(maximum_height_world, "maximum_height_world")
    if maximum_width_world < 0.0 or maximum_height_world < 0.0:
        raise ValueError("as extensões projetadas não podem ser negativas")
    dimensions = _integer_pair(cell_size, "cell_size")
    if any(item <= 0 for item in dimensions):
        raise ValueError("cell_size deve conter valores positivos")
    minimum_ortho_scale = _finite_number(minimum_ortho_scale, "minimum_ortho_scale")
    if minimum_ortho_scale <= 0.0:
        raise ValueError("minimum_ortho_scale deve ser maior que zero")
    horizontal_margin_px = _finite_number(horizontal_margin_px, "horizontal_margin_px")
    vertical_margin_px = _finite_number(vertical_margin_px, "vertical_margin_px")
    safety_px = _finite_number(safety_px, "safety_px")
    if safety_px < 0.0:
        raise ValueError("safety_px não pode ser negativo")
    horizontal_margin_px += safety_px
    vertical_margin_px += safety_px
    if not 0.0 <= horizontal_margin_px < dimensions[0] / 2.0:
        raise ValueError("horizontal_margin_px deve ficar dentro da célula")
    if not 0.0 <= vertical_margin_px < dimensions[1] / 2.0:
        raise ValueError("vertical_margin_px deve ficar dentro da célula")

    # `ortho_scale` is the vertical world span. Convert the safe pixel spans
    # back into world units and keep the largest requirement for both axes.
    required_horizontal = (
        maximum_width_world * dimensions[1]
        / max(dimensions[0] - 2.0 * horizontal_margin_px, 1e-9)
    )
    required_vertical = (
        maximum_height_world * dimensions[1]
        / max(dimensions[1] - 2.0 * vertical_margin_px, 1e-9)
    )
    return max(minimum_ortho_scale, required_horizontal, required_vertical)


def apply_to_settings(profile: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    profile = normalize_manifest(profile)
    return {
        **settings,
        "resolution": profile["cell_size"][0],
        "cell_size_mode": profile["cell_size_mode"],
        "ortho_scale_mode": profile["ortho_scale_mode"],
        "cell_size_quantum": profile["cell_size_quantum"],
        "rows": profile["directions"],
        "phases": profile["phases"],
        "elevation": profile["camera_elevation"],
        "azimuth": profile["camera_azimuth"],
        "ortho_scale": profile["ortho_scale"],
        "dynamic_x": profile["dynamic_x"],
        "horizontal_margin_px": profile["horizontal_margin_px"],
        "dynamic_y": profile["dynamic_y"],
        "vertical_margin_px": profile["vertical_margin_px"],
        "foot_anchor": profile["foot_anchor"],
        "ground_z": profile["ground_z"],
    }


def profile_path(profile_id: str, root: Path = PROFILE_ROOT) -> Path:
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError("render_profile_id inválido")
    return root / f"{profile_id}.json"


def load(profile_id: str, root: Path = PROFILE_ROOT) -> dict[str, Any]:
    path = profile_path(profile_id, root)
    if not path.is_file():
        raise FileNotFoundError(f"render profile não encontrado: {profile_id}")
    return normalize_manifest(json.loads(path.read_text(encoding="utf-8")))


def list_profiles(root: Path = PROFILE_ROOT) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    profiles = [
        normalize_manifest(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(root.glob("*.json"))
    ]
    return sorted(profiles, key=lambda item: item["id"])


def write(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_manifest(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return normalized
