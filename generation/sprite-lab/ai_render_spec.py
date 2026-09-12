"""Structured AI render specifications and deterministic prompt compilation."""
from __future__ import annotations

import copy
import json
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from direction_contract import DIRECTION_LABELS, DIRECTION_ROWS, DIRECTION_VECTORS


SCHEMA = "sprite_lab.render_spec/v2"
PROMPT_SCHEMA = "sprite_lab.prompt_contract/v14"
GRID_ROWS = 8
GRID_COLUMNS = 8
OUTPUT_SIZE = 2048
SUPPORTED_OUTPUT_SIZES = (1024, 2048)
DEFAULT_BACKGROUND = "transparent"
GENERATION_MODE_SINGLE_SHEET = "single_sheet"
GENERATION_MODE_CHARACTER_WEAPON_HOLDOUT = "character_weapon_holdout"
GENERATION_MODE_CHARACTER_COMPONENT_HOLDOUT = "character_component_holdout"
GENERATION_MODES = (
    GENERATION_MODE_SINGLE_SHEET,
    GENERATION_MODE_CHARACTER_WEAPON_HOLDOUT,
    GENERATION_MODE_CHARACTER_COMPONENT_HOLDOUT,
)
HOLDOUT_GENERATION_ORDER = ("character", "weapon")
HOLDOUT_COMPOSITION_ORDER = ("weapon", "character_holdout")
HOLDOUT_LAYERS = (
    {"id": "weapon", "z": 0},
    {"id": "character_holdout", "z": 1},
)

ASSET_MODES = (
    "character_animation",
    "prop_catalog",
    "building_catalog",
    "environment_catalog",
    "mixed_catalog",
    "custom",
)

ROW_SEMANTICS = {
    "character_animation": "direction",
    "prop_catalog": "asset_type",
    "building_catalog": "building_type",
    "environment_catalog": "environment_type",
    "mixed_catalog": "independent_asset",
    "custom": "defined_by_row_specification",
}

COLUMN_SEMANTICS = {
    "character_animation": "temporal_frame",
    "prop_catalog": "variant_or_state",
    "building_catalog": "variant_or_state",
    "environment_catalog": "variant",
    "mixed_catalog": "variant_or_state",
    "custom": "defined_by_column_specification",
}

REFERENCE_ROLES = {
    "identity": {
        "role": "authoritative_visual_identity",
        "controls": (
            "the final asset identity, body and costume design, distinctive proportions, "
            "face, helmet, clothing, armor, materials, colors, ornaments, palette and "
            "visual language in every output cell"
        ),
        "does_not_control": "pose, animation timing, camera, grid location or cell boundaries",
    },
    "identity_lineart": {
        "role": "identity_contour_guide",
        "controls": (
            "the contour, silhouette and separation of visible parts from the same identity "
            "shown in the identity reference"
        ),
        "does_not_control": (
            "colors, materials, shading, texture, pose, animation timing, camera, grid "
            "location or cell boundaries"
        ),
    },
    "beauty": {
        "role": "volume_depth_occlusion",
        "controls": "spatial placement, volume, depth, occlusion and structural reading",
        "does_not_control": (
            "character identity, face, anatomy design, clothing, armor, materials, colors, "
            "ornaments, palette or final artistic appearance"
        ),
    },
    "lineart": {
        "role": "silhouette_geometry",
        "controls": "pose envelope, silhouette, geometry, component contour, component placement and spatial boundaries",
        "does_not_control": "body design, clothing, armor, materials, colors, palette or visual identity",
    },
    "bones": {
        "role": "pose_skeleton_motion",
        "controls": "skeleton, pose, articulation, movement, timing and body orientation",
        "does_not_control": "appearance, face, clothing, armor, anatomy design, colors, palette or materials",
    },
    "frame_control": {
        "role": "cell_boundary_control",
        "controls": "the 8x8 grid, protected cell boundaries and spatial containment of each frame",
        "does_not_control": (
            "character identity, pose, animation, materials, colors or final artwork; "
            "the black guide lines must not appear in the output"
        ),
    },
}

CANONICAL_DIRECTION_ROWS = tuple(
    (DIRECTION_LABELS[row], list(DIRECTION_VECTORS[row])) for row in DIRECTION_ROWS
)


def _clean_text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _row_defaults(index: int, mode: str) -> dict[str, Any]:
    if mode == "character_animation":
        row_id, vector = CANONICAL_DIRECTION_ROWS[index - 1]
        name = row_id.replace("_", " ").title()
        description = f"Character facing {name.lower()}."
        row_type = "character"
        column_mode = "animation_frames"
        column_description = "Eight temporal phases of the animation."
    else:
        row_id = f"row_{index}"
        vector = None
        name = f"Asset {index}"
        description = ""
        row_type = {
            "prop_catalog": "prop",
            "building_catalog": "building",
            "environment_catalog": "environment",
        }.get(mode, "asset")
        column_mode = "variants"
        column_description = "Eight coherent variants of the same asset family."
    row: dict[str, Any] = {
        "index": index,
        "id": row_id,
        "type": row_type,
        "name": name,
        "description": description,
        "must_have": "",
        "must_not_have": "",
        "scale": {
            "policy": "inherit_global",
            "occupancy": None,
        },
        "anchor": "inherit_global",
        "columns": {
            "mode": column_mode,
            "description": column_description,
            "cells": [],
        },
        "include_in_prompt": True,
    }
    if vector is not None:
        row["vector"] = vector
    return row


def default_render_spec(
    *,
    mode: str = "character_animation",
    name: str = "",
    direction_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the application-owned baseline for a new AI render."""
    mode = mode if mode in ASSET_MODES else "character_animation"
    include_descriptions = mode != "character_animation"
    rows = [_row_defaults(index, mode) for index in range(1, GRID_ROWS + 1)]
    return {
        "version": "2.0",
        "generation_mode": GENERATION_MODE_SINGLE_SHEET,
        "output": {
            "width": OUTPUT_SIZE,
            "height": OUTPUT_SIZE,
            "grid": {"rows": GRID_ROWS, "columns": GRID_COLUMNS},
            "background": DEFAULT_BACKGROUND,
            "draw_grid": False,
        },
        "asset": {
            "mode": mode,
            "name": name,
            "global_description": "",
            "style": {
                "preset": "",
                "description": "",
            },
        },
        "camera": {
            "projection": "orthographic",
            "preset": "isometric",
            "elevation_deg": 30.0,
            "azimuth_deg": 45.0,
        },
        "framing": {
            "anchor": "bottom_center",
            "scale_policy": "normalize_per_row",
            "safe_area": 0.90,
            "allow_crop": False,
            "allow_cross_cell_overlap": False,
        },
        "prompt_options": {
            "include_rows": include_descriptions,
            "include_cells": include_descriptions,
        },
        "references": {
            key: {"enabled": key in {"identity", "beauty"}, **value}
            for key, value in REFERENCE_ROLES.items()
        },
        "rows": rows,
    }


def _normalize_cells(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cells = []
    for item in value[:GRID_COLUMNS]:
        if not isinstance(item, dict):
            continue
        column = item.get("column")
        try:
            column = int(column)
        except (TypeError, ValueError):
            continue
        if not 1 <= column <= GRID_COLUMNS:
            continue
        cells.append(
            {
                "column": column,
                "description": _clean_text(item.get("description")),
                "include_in_prompt": _bool(item.get("include_in_prompt"), True),
            }
        )
    return cells


def _aliased_value(value: dict[str, Any], snake_case: str, camel_case: str) -> Any:
    """Read temporary POC aliases while always emitting the canonical name."""
    return value.get(snake_case, value.get(camel_case))


def _relative_artifact_path(value: Any, field: str) -> str:
    path = _clean_text(value)
    if path and (
        PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()
    ):
        raise ValueError(f"{field} deve ser um caminho relativo")
    return path


def _component_inventory(components: list[dict[str, Any]]) -> str:
    if not components:
        return "nenhum componente encontrado"
    return ", ".join(
        f"id='{_clean_text(component.get('id'), '<sem id>')}', "
        f"role='{_clean_text(component.get('role'), '<sem role>')}'"
        for component in components
    )


def select_weapon_component(
    source_contract: Any,
    *,
    weapon_component_id: str | None = None,
) -> dict[str, Any]:
    """Return the single visible weapon as a detached, normalized object."""
    raw_components = (
        source_contract.get("components")
        if isinstance(source_contract, dict)
        else None
    )
    components = (
        [component for component in raw_components if isinstance(component, dict)]
        if isinstance(raw_components, list)
        else []
    )
    visible_components = [
        component
        for component in components
        if _bool(component.get("visible"), True)
    ]
    inventory = _component_inventory(components)
    requested_id = _clean_text(weapon_component_id)

    if requested_id:
        selected = next(
            (
                component
                for component in visible_components
                if _clean_text(component.get("id")) == requested_id
            ),
            None,
        )
        if selected is None or _clean_text(selected.get("role")).casefold() != "weapon":
            raise ValueError(
                f"weapon_component_id '{requested_id}' não identifica uma arma visível; "
                f"ids e roles encontrados: {inventory}"
            )

    visible_weapons = [
        component
        for component in visible_components
        if _clean_text(component.get("role")).casefold() == "weapon"
    ]
    if not visible_weapons:
        raise ValueError(
            "nenhuma arma visível encontrada; "
            f"ids e roles encontrados: {inventory}"
        )
    if len(visible_weapons) != 1:
        raise ValueError(
            "a POC suporta uma única arma visível; "
            f"ids e roles encontrados: {inventory}"
        )

    selected = visible_weapons[0]
    selected_id = _clean_text(selected.get("id"))
    if not selected_id:
        raise ValueError(
            "a arma visível deve declarar id; "
            f"ids e roles encontrados: {inventory}"
        )
    return {
        "id": selected_id,
        "asset_id": _clean_text(selected.get("asset_id")),
        "attach_to": _clean_text(selected.get("attach_to")),
        "hand": _clean_text(selected.get("hand")),
        "path": _clean_text(selected.get("path")),
    }


def select_layer_components(
    source_contract: Any,
    *,
    component_ids: list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Select visible source components in deterministic composition order."""
    raw_components = (
        source_contract.get("components")
        if isinstance(source_contract, dict)
        else None
    )
    components = (
        [component for component in raw_components if isinstance(component, dict)]
        if isinstance(raw_components, list)
        else []
    )
    visible = [component for component in components if _bool(component.get("visible"), True)]
    by_id: dict[str, dict[str, Any]] = {}
    for component in visible:
        component_id = _clean_text(component.get("id"))
        if not component_id:
            raise ValueError("todo componente visível deve declarar id")
        if component_id in by_id:
            raise ValueError(f"id de componente visível duplicado: {component_id}")
        by_id[component_id] = component
    requested = list(component_ids) if component_ids is not None else list(by_id)
    if not requested:
        raise ValueError("component_ids deve selecionar ao menos um componente visível")
    if any(not isinstance(item, str) or not item.strip() for item in requested):
        raise ValueError("component_ids deve conter ids não vazios")
    if len(set(requested)) != len(requested):
        raise ValueError("component_ids não pode conter ids duplicados")
    missing = [component_id for component_id in requested if component_id not in by_id]
    if missing:
        raise ValueError(
            "component_ids não identifica componentes visíveis: " + ", ".join(missing)
        )
    return [copy.deepcopy(by_id[component_id]) for component_id in requested]


def _normalize_modular_layer_contract(
    value: Any,
    source_contract: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(
            "layer_contract deve ser um objeto no modo character_component_holdout"
        )
    base_id = _clean_text(_aliased_value(value, "base_id", "baseId"), "character_full")
    raw_component_ids = _aliased_value(value, "component_ids", "componentIds")
    if not isinstance(raw_component_ids, list):
        raise ValueError("layer_contract.component_ids deve ser uma lista")
    component_ids = [_clean_text(item) for item in raw_component_ids]
    selected = select_layer_components(source_contract, component_ids=component_ids)
    expected_order = [base_id, *component_ids]
    generation_order = _aliased_value(value, "generation_order", "generationOrder")
    composition_order = _aliased_value(value, "composition_order", "compositionOrder")
    if generation_order != expected_order:
        raise ValueError(
            "layer_contract.generation_order deve listar a base seguida dos component_ids"
        )
    if composition_order != expected_order:
        raise ValueError(
            "layer_contract.composition_order deve listar a base seguida dos component_ids"
        )

    layers = value.get("layers")
    if not isinstance(layers, list) or len(layers) != len(expected_order):
        raise ValueError("layer_contract.layers deve declarar a base e todos os componentes")
    normalized_layers: list[dict[str, Any]] = []
    z_values: list[int] = []
    selected_by_id = {_clean_text(component.get("id")): component for component in selected}
    for index, expected_id in enumerate(expected_order):
        layer = layers[index]
        if not isinstance(layer, dict) or _clean_text(layer.get("id")) != expected_id:
            raise ValueError("layer_contract.layers deve seguir composition_order")
        try:
            z = int(layer.get("z"))
        except (TypeError, ValueError):
            raise ValueError(f"layer_contract.layers[{index}].z deve ser inteiro") from None
        if isinstance(layer.get("z"), bool) or z != layer.get("z"):
            raise ValueError(f"layer_contract.layers[{index}].z deve ser inteiro")
        z_values.append(z)
        normalized_layer: dict[str, Any] = {
            "id": expected_id,
            "role": "base" if index == 0 else "component",
            "z": z,
        }
        if index:
            component = selected_by_id[expected_id]
            normalized_layer["kind"] = _clean_text(
                layer.get("kind") or component.get("role"), "component"
            )
            normalized_layer["source_component_id"] = expected_id
        file_name = _relative_artifact_path(
            layer.get("file"), f"layer_contract.layers[{index}].file"
        )
        if file_name:
            normalized_layer["file"] = file_name
        normalized_layers.append(normalized_layer)
    if len(set(z_values)) != len(z_values) or z_values != sorted(z_values):
        raise ValueError("layer_contract.layers.z deve ser único e crescente")

    preview_value = value.get("preview")
    if preview_value is not None and not isinstance(preview_value, str):
        raise ValueError("layer_contract.preview deve ser string ou null")
    preview = _relative_artifact_path(preview_value, "layer_contract.preview")
    return {
        "version": 2,
        "base_id": base_id,
        "component_ids": component_ids,
        "generation_order": expected_order,
        "composition_order": expected_order,
        "layers": normalized_layers,
        "preview": preview or None,
    }


def _normalize_layer_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(
            "layer_contract deve ser um objeto no modo character_weapon_holdout"
        )

    weapon_component_id = _clean_text(
        _aliased_value(value, "weapon_component_id", "weaponComponentId")
    )
    if not weapon_component_id:
        raise ValueError("layer_contract.weapon_component_id deve ser explícito")

    generation_order = _aliased_value(
        value, "generation_order", "generationOrder"
    )
    if generation_order != list(HOLDOUT_GENERATION_ORDER):
        raise ValueError(
            "layer_contract.generation_order deve ser ['character', 'weapon']"
        )

    composition_order = _aliased_value(
        value, "composition_order", "compositionOrder"
    )
    if composition_order != list(HOLDOUT_COMPOSITION_ORDER):
        raise ValueError(
            "layer_contract.composition_order deve ser "
            "['weapon', 'character_holdout']"
        )

    layers = value.get("layers")
    if not isinstance(layers, list) or len(layers) != len(HOLDOUT_LAYERS):
        raise ValueError(
            "layer_contract.layers deve declarar weapon e character_holdout"
        )
    normalized_layers = []
    for index, expected in enumerate(HOLDOUT_LAYERS):
        layer = layers[index]
        if not isinstance(layer, dict) or layer.get("id") != expected["id"]:
            raise ValueError(
                "layer_contract.layers deve declarar weapon e character_holdout"
            )
        try:
            z = int(layer.get("z"))
        except (TypeError, ValueError):
            raise ValueError(f"layer_contract.layers[{index}].z deve ser inteiro") from None
        if z != expected["z"]:
            raise ValueError(
                f"layer_contract.layers[{index}].z deve ser {expected['z']}"
            )
        normalized_layer = {"id": expected["id"], "z": z}
        file_name = _relative_artifact_path(
            layer.get("file"), f"layer_contract.layers[{index}].file"
        )
        if file_name:
            normalized_layer["file"] = file_name
        normalized_layers.append(normalized_layer)

    preview_value = value.get("preview")
    if preview_value is not None and not isinstance(preview_value, str):
        raise ValueError("layer_contract.preview deve ser string ou null")
    preview = _relative_artifact_path(preview_value, "layer_contract.preview")

    return {
        "weapon_component_id": weapon_component_id,
        "generation_order": list(HOLDOUT_GENERATION_ORDER),
        "composition_order": list(HOLDOUT_COMPOSITION_ORDER),
        "layers": normalized_layers,
        "preview": preview or None,
    }


def normalize_render_spec(
    value: Any,
    *,
    mode: str | None = None,
    name: str = "",
    direction_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize user input while retaining the fixed 8x8 output contract."""
    incoming = value if isinstance(value, dict) else {}
    incoming_asset = incoming.get("asset") if isinstance(incoming.get("asset"), dict) else {}
    selected_mode = mode or _clean_text(incoming_asset.get("mode"), "character_animation")
    if selected_mode not in ASSET_MODES:
        selected_mode = "custom"
    baseline = default_render_spec(
        mode=selected_mode,
        name=name or _clean_text(incoming_asset.get("name")),
        direction_rows=direction_rows,
    )
    spec = copy.deepcopy(baseline)

    generation_mode = _clean_text(
        _aliased_value(incoming, "generation_mode", "generationMode"),
        GENERATION_MODE_SINGLE_SHEET,
    )
    if generation_mode not in GENERATION_MODES:
        raise ValueError(
            "generation_mode deve ser single_sheet, character_weapon_holdout "
            "ou character_component_holdout"
        )
    spec["generation_mode"] = generation_mode
    source_contract = incoming.get("source_contract")
    if generation_mode == GENERATION_MODE_CHARACTER_WEAPON_HOLDOUT:
        layer_contract = _aliased_value(incoming, "layer_contract", "layerContract")
        spec["layer_contract"] = _normalize_layer_contract(layer_contract)
    elif generation_mode == GENERATION_MODE_CHARACTER_COMPONENT_HOLDOUT:
        layer_contract = _aliased_value(incoming, "layer_contract", "layerContract")
        spec["layer_contract"] = _normalize_modular_layer_contract(
            layer_contract, source_contract
        )

    output = incoming.get("output") if isinstance(incoming.get("output"), dict) else {}
    requested_width = output.get("width", OUTPUT_SIZE)
    requested_height = output.get("height", OUTPUT_SIZE)
    if requested_width is None:
        requested_width = OUTPUT_SIZE
    if requested_height is None:
        requested_height = OUTPUT_SIZE
    try:
        width = int(requested_width)
        height = int(requested_height)
    except (TypeError, ValueError):
        raise ValueError(
            "output.width e output.height devem ser 1024 ou 2048, em formato quadrado"
        ) from None
    if (width != requested_width and not isinstance(requested_width, str)) or (
        height != requested_height and not isinstance(requested_height, str)
    ) or width != height or width not in SUPPORTED_OUTPUT_SIZES:
        raise ValueError(
            "a resolução do output deve ser 1024x1024 ou 2048x2048"
        )
    spec["output"]["width"] = width
    spec["output"]["height"] = height
    spec["output"]["background"] = _clean_text(
        output.get("background") or spec["output"]["background"],
        spec["output"]["background"],
    ).upper()
    spec["output"]["draw_grid"] = _bool(output.get("draw_grid"), False)

    asset = spec["asset"]
    asset["global_description"] = _clean_text(
        incoming_asset.get("global_description")
    )
    incoming_style = incoming_asset.get("style") if isinstance(incoming_asset.get("style"), dict) else {}
    asset["style"] = {
        "preset": _clean_text(incoming_style.get("preset")),
        "description": _clean_text(incoming_style.get("description")),
    }

    incoming_camera = incoming.get("camera") if isinstance(incoming.get("camera"), dict) else {}
    spec["camera"] = {
        "projection": _clean_text(incoming_camera.get("projection"), "orthographic"),
        "preset": _clean_text(incoming_camera.get("preset"), "isometric"),
        "elevation_deg": _float(incoming_camera.get("elevation_deg"), 30.0),
        "azimuth_deg": _float(incoming_camera.get("azimuth_deg"), 45.0),
    }

    incoming_framing = incoming.get("framing") if isinstance(incoming.get("framing"), dict) else {}
    safe_area = _float(incoming_framing.get("safe_area"), 0.90) or 0.90
    spec["framing"] = {
        "anchor": _clean_text(incoming_framing.get("anchor"), "bottom_center"),
        "scale_policy": _clean_text(
            incoming_framing.get("scale_policy"), "normalize_per_row"
        ),
        "safe_area": max(0.1, min(1.0, safe_area)),
        "allow_crop": _bool(incoming_framing.get("allow_crop"), False),
        "allow_cross_cell_overlap": _bool(
            incoming_framing.get("allow_cross_cell_overlap"), False
        ),
    }

    incoming_prompt_options = incoming.get("prompt_options")
    if isinstance(incoming_prompt_options, dict):
        spec["prompt_options"] = {
            "include_rows": _bool(
                incoming_prompt_options.get("include_rows"),
                baseline["prompt_options"]["include_rows"],
            ),
            "include_cells": _bool(
                incoming_prompt_options.get("include_cells"),
                baseline["prompt_options"]["include_cells"],
            ),
        }

    incoming_references = incoming.get("references")
    if isinstance(incoming_references, dict):
        for key, default in spec["references"].items():
            source = incoming_references.get(key)
            if not isinstance(source, dict):
                continue
            spec["references"][key]["enabled"] = _bool(
                source.get("enabled"), default["enabled"]
            )

    incoming_rows = incoming.get("rows")
    if isinstance(incoming_rows, list):
        for position, source_row in enumerate(incoming_rows[:GRID_ROWS]):
            if not isinstance(source_row, dict):
                continue
            target = spec["rows"][position]
            for key in ("id", "type", "name", "description", "must_have", "must_not_have", "anchor"):
                if key in source_row:
                    target[key] = _clean_text(source_row.get(key), target.get(key, ""))
            target["include_in_prompt"] = _bool(
                source_row.get("include_in_prompt"), True
            )
            vector = source_row.get("vector")
            if isinstance(vector, list) and len(vector) == 2:
                target["vector"] = vector[:2]
            source_scale = source_row.get("scale") if isinstance(source_row.get("scale"), dict) else {}
            target["scale"] = {
                "policy": _clean_text(source_scale.get("policy"), "inherit_global"),
                "occupancy": _float(source_scale.get("occupancy")),
            }
            source_columns = source_row.get("columns") if isinstance(source_row.get("columns"), dict) else {}
            target["columns"] = {
                "mode": _clean_text(source_columns.get("mode"), target["columns"]["mode"]),
                "description": _clean_text(
                    source_columns.get("description"), target["columns"]["description"]
                ),
                "cells": _normalize_cells(source_columns.get("cells")),
            }

    spec["version"] = "2.0"
    if isinstance(source_contract, dict):
        spec["source_contract"] = copy.deepcopy(source_contract)
    if generation_mode == GENERATION_MODE_CHARACTER_WEAPON_HOLDOUT:
        select_weapon_component(
            source_contract,
            weapon_component_id=spec["layer_contract"]["weapon_component_id"],
        )
    spec["asset"]["mode"] = selected_mode
    spec["asset"]["name"] = _clean_text(incoming_asset.get("name")) or name
    if selected_mode == "character_animation":
        source_rows = source_contract.get("directions") if isinstance(source_contract, dict) else None
        source_rows_are_valid = (
            isinstance(source_rows, list)
            and len(source_rows) == GRID_ROWS
            and all(isinstance(row, dict) and row.get("id") for row in source_rows)
        )
        # Row position is authoritative. When a structural render is selected,
        # persist its Blender order into RenderSpec instead of leaving the UI
        # defaults (which use the generic canonical order).
        rows = source_rows if source_rows_are_valid else [
            {"row": index, "id": direction, "vector": vector}
            for index, (direction, vector) in enumerate(CANONICAL_DIRECTION_ROWS, start=1)
        ]
        for index, source_row in enumerate(rows):
            direction = _clean_text(source_row.get("id"), CANONICAL_DIRECTION_ROWS[index][0])
            vector = source_row.get("vector")
            if not isinstance(vector, list) or len(vector) != 2:
                vector = list(CANONICAL_DIRECTION_ROWS[index][1])
            target = spec["rows"][index]
            target["id"] = direction
            target["name"] = direction.replace("_", " ").title()
            target["vector"] = list(vector)
            if re.fullmatch(r"Character facing [a-z -]+\.", _clean_text(target.get("description"))):
                target["description"] = f"Character facing {direction.replace('_', ' ')}."
    return spec


def build_reference_manifest(
    channels: list[str] | tuple[str, ...],
    *,
    identity_name: str = "identity reference",
    include_identity_lineart: bool = False,
    identity_lineart_mode: str = "lineart_standard",
) -> list[dict[str, Any]]:
    """Build the ordered image-role contract used by every provider."""
    identity_label = _clean_text(identity_name, "identity reference")
    manifest = [
        {
            "index": 1,
            "type": "identity",
            "name": identity_label,
            **REFERENCE_ROLES["identity"],
        }
    ]
    next_index = 2
    if include_identity_lineart:
        guide_label = {
            "lineart_standard": "lineart standard",
            "canny_edges": "canny edges",
        }.get(str(identity_lineart_mode).strip().casefold(), "identity contour guide")
        manifest.append(
            {
                "index": next_index,
                "type": "identity_lineart",
                "name": f"{identity_label} · {guide_label}",
                "guide_mode": str(identity_lineart_mode).strip().casefold(),
                **REFERENCE_ROLES["identity_lineart"],
            }
        )
        next_index += 1
    for index, channel in enumerate(channels, start=next_index):
        if channel not in REFERENCE_ROLES or channel == "identity":
            continue
        manifest.append({"index": index, "type": channel, **REFERENCE_ROLES[channel]})
    return manifest


def _reference_prompt(manifest: list[dict[str, Any]]) -> str:
    lines = [
        "The attached images are ordered exactly as follows:",
        "",
    ]
    for item in manifest:
        name = _clean_text(item.get("name"))
        heading = f"IMAGE {item['index']}" + (f" — {name}" if name else "") + ":"
        lines.extend(
            [
                heading,
                f"Role = {str(item['type']).upper()}_REFERENCE",
                f"Use for: {item['controls']}.",
                f"It does not control: {item['does_not_control']}.",
                "",
            ]
        )
    lines.append("Never transfer information from one reference role into another role unless explicitly requested.")
    return "\n".join(lines)


def _identity_transfer_prompt(manifest: list[dict[str, Any]]) -> str:
    identity = next(
        (item for item in manifest if item.get("type") == "identity"),
        {"index": 1, "name": "identity reference"},
    )
    image = f"IMAGE {identity.get('index', 1)}"
    name = _clean_text(identity.get("name"), "identity reference")
    return f"""{image} — {name} is the single authoritative source for the final visible identity.
It is not optional inspiration, a mood board, a loose style hint or a structural source.

Every output cell must depict the same asset identity from {image}. Copy its distinctive visible design faithfully across all 64 cells, including its body and costume design, face treatment, helmet or headgear, clothing, armor, materials, color palette, ornaments, markings and other recognizable features.

Treat Beauty, Bones and Lineart as anonymous structural proxies. Do not preserve their character identity, face, anatomy design, clothing, armor, palette, materials or decorative details merely because they are visible in those images. Replace those visual attributes with the identity from {image}.

When a structural reference contains a component that is absent or partly hidden in {image}, preserve only its required pose, placement and contour, then render its design, materials and colors so they belong coherently to the identity from {image}.

When {image} does not show the back or one side of a feature, infer a consistent continuation from the same design language. Never fill missing identity information by copying the structural proxy's appearance.

The Style Contract controls rendering treatment only. It must not redesign, replace, simplify or override the identity from {image}."""


_DIRECTION_ASSIGNMENT = re.compile(
    r"\b(?:row|r)\s*([1-8])\s*(?:=|:|\bis\b|\bfaces?\b)\s*"
    r"(north[\s_-]?east|north[\s_-]?west|south[\s_-]?east|south[\s_-]?west|north|south|east|west)\b",
    re.IGNORECASE,
)


def validate_additional_instructions(
    value: str,
    *,
    mode: str = "character_animation",
    direction_rows: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Return conflicts that would make the fixed prompt contract ambiguous."""
    if mode != "character_animation":
        return []
    if isinstance(direction_rows, list) and len(direction_rows) == GRID_ROWS:
        expected = {
            index: _clean_text(item.get("id"))
            for index, item in enumerate(direction_rows, start=1)
            if isinstance(item, dict)
        }
    else:
        expected = {
            index: direction
            for index, (direction, _vector) in enumerate(
                CANONICAL_DIRECTION_ROWS, start=1
            )
        }
    conflicts: list[str] = []
    for match in _DIRECTION_ASSIGNMENT.finditer(_clean_text(value)):
        row = int(match.group(1))
        supplied = re.sub(r"[\s-]+", "_", match.group(2).casefold())
        if expected.get(row) and supplied != expected[row]:
            conflicts.append(
                f"R{row} foi descrita como {supplied.upper()}, mas o contrato do Blender exige {expected[row].upper()}"
            )
    return conflicts


def _direction_contract_prompt() -> str:
    lines = [
        "For character_animation, row order is immutable and comes from the Blender export:",
        "",
    ]
    for index, (direction, vector) in enumerate(CANONICAL_DIRECTION_ROWS, start=1):
        lines.append(
            f"ROW {index} = {direction.upper()} / id {direction} / direction vector {vector}."
        )
    lines.extend(
        [
            "",
            "The direction labels describe where the character faces in the image, not where the camera is placed.",
            "In particular, ROW 1 is NORTH and must face north, while ROW 5 is SOUTH and must face south.",
            "Do not reorder, mirror, rotate, reinterpret or infer these rows from visual appearance.",
            "Every selected structural reference uses this exact row mapping. If Frame Control is selected, its 8x8 grid follows the same row mapping.",
        ]
    )
    return "\n".join(lines)


def _row_prompt(row: dict[str, Any], *, include_cells: bool = True) -> str:
    if row.get("include_in_prompt") is False:
        return ""
    scale = row.get("scale") if isinstance(row.get("scale"), dict) else {}
    columns = row.get("columns") if isinstance(row.get("columns"), dict) else {}
    occupancy = scale.get("occupancy")
    occupancy_text = f"{float(occupancy):.3g}" if occupancy is not None else "inherit global"
    lines = [
        f"ROW {row.get('index')} — {row.get('name') or row.get('id')}",
        "",
        f"Direction id: {row.get('id') or 'unspecified'}",
        f"Direction vector: {row.get('vector') or 'unspecified'}",
        f"Asset type: {row.get('type') or 'asset'}",
        f"Row id: {row.get('id') or 'unspecified'}",
        f"Description: {row.get('description') or 'None specified.'}",
        f"Required features: {row.get('must_have') or 'None specified.'}",
        f"Forbidden features: {row.get('must_not_have') or 'None specified.'}",
        f"Scale policy: {scale.get('policy') or 'inherit_global'}",
        f"Occupancy: {occupancy_text}",
        f"Anchor: {row.get('anchor') or 'inherit_global'}",
        f"Column mode: {columns.get('mode') or 'inherit_global'}",
        f"Column description: {columns.get('description') or 'Follow the row specification.'}",
    ]
    cells = columns.get("cells")
    if include_cells and isinstance(cells, list):
        for cell in sorted(cells, key=lambda item: int(item.get("column", 0))):
            if cell.get("include_in_prompt") is False:
                continue
            description = _clean_text(cell.get("description"))
            if description:
                lines.extend([f"Column {cell.get('column')}: {description}"])
    return "\n".join(lines)


def _compile_character_prompt(
    normalized: dict[str, Any],
    reference_manifest: list[dict[str, Any]],
    additional_instructions: str,
) -> str:
    """Compile the concise image-transfer prompt proven by the Gemini baseline."""
    output = normalized["output"]
    camera = normalized["camera"]
    source_contract = (
        normalized.get("source_contract")
        if isinstance(normalized.get("source_contract"), dict)
        else {}
    )
    prompt_options = normalized.get("prompt_options") or {}
    identity = next(
        (item for item in reference_manifest if item.get("type") == "identity"),
        {"index": 1, "name": "identity reference"},
    )
    identity_name = _clean_text(identity.get("name"), "identity reference")
    image_by_type = {str(item.get("type")): item for item in reference_manifest}
    identity_index = identity["index"]
    beauty = image_by_type.get("beauty")
    identity_lineart = image_by_type.get("identity_lineart")
    bones = image_by_type.get("bones")
    lineart = image_by_type.get("lineart")
    frame_control_item = image_by_type.get("frame_control")
    beauty_line = (
        f"Use IMAGE {beauty['index']}, the uploaded 8x8 beauty spritesheet, as the exact composition and layout source."
        if beauty
        else "Use the selected structural spritesheets as the exact composition and layout source."
    )
    identity_lineart_line = (
        f"Use IMAGE {identity_lineart['index']}, "
        f"the {'Canny edge map' if identity_lineart.get('guide_mode') == 'canny_edges' else 'lineart'} "
        f"derived from the identity reference, only to reinforce the identity contour, silhouette and separation of visible parts. It is a guide paired with IMAGE {identity_index}; IMAGE {identity_index} remains authoritative for colors, materials, shading, texture and every other visible design choice."
        if identity_lineart
        else ""
    )
    bones_line = (
        f"Use IMAGE {bones['index']} bones spritesheet only to preserve the exact pose, joint positions, limb articulation and animation phase of each cell."
        if bones
        else ""
    )
    lineart_line = (
        f"Use IMAGE {lineart['index']} lineart spritesheet only to preserve the visible mesh contour, silhouette, component contour and separation between body parts. Treat its drawing style as non-authoritative; do not copy internal linework or a flat linear appearance."
        if lineart
        else ""
    )
    frame_control = ""
    if frame_control_item:
        frame_control = (
            f"Use IMAGE {frame_control_item['index']} frame-control grid only to delimit "
            "the 64 cells. Keep every visible pixel inside its corresponding 256x256 "
            "cell and do not reproduce the guide lines."
        )
    components = source_contract.get("components")
    background = _clean_text(output.get("background"), "transparent")
    if background.casefold() == "transparent":
        background_instruction = "Use a fully transparent RGBA background in every empty pixel."
    elif background.casefold() in {"#00ff00", "00ff00", "lemon green", "lemongreen"}:
        background_instruction = (
            "Use a perfectly uniform pure lemon-green background (#00FF00) in every "
            "empty pixel. Do not use transparency, gradients, shadows or any other "
            "background color."
        )
    else:
        background_instruction = f"Use a perfectly uniform {background} background in every empty pixel."
    row_notes = ""
    if prompt_options.get("include_rows", False):
        notes = []
        for row in normalized["rows"]:
            if row.get("include_in_prompt") is False:
                continue
            lines = []
            direction = str(row.get("id") or "").replace("_", " ")
            description = _clean_text(row.get("description"))
            if description and description.casefold() != f"character facing {direction}.".casefold():
                lines.append(f"Description: {description}")
            if _clean_text(row.get("must_have")):
                lines.append(f"Required: {_clean_text(row.get('must_have'))}")
            if _clean_text(row.get("must_not_have")):
                lines.append(f"Avoid: {_clean_text(row.get('must_not_have'))}")
            if prompt_options.get("include_cells", False):
                columns = row.get("columns") if isinstance(row.get("columns"), dict) else {}
                for cell in columns.get("cells") or []:
                    cell_description = _clean_text(cell.get("description"))
                    if cell.get("include_in_prompt") is not False and cell_description:
                        lines.append(f"C{cell.get('column')}: {cell_description}")
            if lines:
                notes.append(f"R{row.get('index')} ({str(row.get('id')).upper()}): " + " ".join(lines))
        if notes:
            row_notes = "\n\nOptional row notes:\n" + "\n".join(notes)
    source_camera = source_contract.get("camera") if isinstance(source_contract.get("camera"), dict) else {}
    camera_projection = _clean_text(source_camera.get("type"), camera["projection"])
    camera_preset = _clean_text(source_camera.get("preset"), camera["preset"])
    source_action = source_contract.get("action") if isinstance(source_contract.get("action"), dict) else {}
    action_label = _clean_text(source_action.get("clip_name") or source_action.get("name"), "structural animation")
    contract_components = []
    for component in components if isinstance(components, list) else []:
        if not isinstance(component, dict):
            continue
        name = _clean_text(
            component.get("name")
            or component.get("asset_id")
            or component.get("id"),
            "component",
        )
        attach_to = _clean_text(component.get("attach_to"), "the same attachment point")
        hand = _clean_text(component.get("hand"))
        role = _clean_text(component.get("role"))
        contract_components.append(
            {
                "name": name,
                "role": role or "component",
                "attach_to": attach_to,
                "hand": hand or None,
            }
        )

    direction_rows = [
        {
            "row": int(row.get("index") or index),
            "id": _clean_text(row.get("id")),
            "vector": list(row.get("vector") or []),
        }
        for index, row in enumerate(normalized["rows"], start=1)
    ]
    direction_lines = ",\n".join(
        "        "
        + json.dumps(row, ensure_ascii=False, separators=(", ", ": "))
        for row in direction_rows
    )
    background_value = (
        "transparent"
        if background.casefold() == "transparent"
        else "#00FF00"
        if background.casefold() in {"#00ff00", "00ff00", "lemon green", "lemongreen"}
        else background
    )
    component_line = ""
    if contract_components:
        component_line = (
            ",\n    \"components\": "
            + json.dumps(contract_components, ensure_ascii=False, separators=(", ", ": "))
        )
    spritesheet_contract = f"""{{
  "content": {{
    "directions": {{
      "count": {GRID_ROWS},
      "rows": [
{direction_lines}
      ]
    }},
    "camera": {{ "type": {json.dumps(camera_projection)}, "preset": {json.dumps(camera_preset)}, "shadow": false }},
    "action": {json.dumps(action_label)},
    "background": {json.dumps(background_value)},
    "pixel_ratio": {json.dumps(f"{output['width']}x{output['height']}")}{component_line}
  }}
}}"""

    optional_lines = "\n\n".join(
        line for line in (identity_lineart_line, bones_line, lineart_line, frame_control) if line
    )
    extra = ""
    if additional_instructions:
        extra = f"\n\nAdditional instruction:\n{additional_instructions}"

    return f"""{beauty_line}

Transform the character in every cell to match the character design, proportions, clothing, materials, colors and visual identity shown in the reference image {identity_name}. Use this identity consistently in every cell; do not replace it with a generic archetype or copy the appearance of the structural references.

{optional_lines}

The final result must be a single 8x8 spritesheet with exactly 64 cells, preserving:
- the original 8 rows and 8 columns;
- the original camera angle and isometric perspective;
- the original direction of each row;
- the original animation phase of each column;
- the original cell size, framing, foot position and spacing;
- one complete character per cell;
- the head of each pose must follow the same order: spritesheetContract > direction > id.

IMPORTANT: {background_instruction}

spritesheetContract:
{spritesheet_contract}

Copy each corresponding structural cell into the same output position. Do not invent, combine, mirror, rotate, reorder or reinterpret poses. Bones and Lineart are guides only and must not appear in the final artwork. Do not crop the character or its components. Preserve every component listed in spritesheetContract in every cell, with its declared attachment and hand.{row_notes}{extra}
""".strip() + "\n"


def _weapon_manifest_item(
    reference_manifest: list[dict[str, Any]],
    *reference_types: str,
) -> dict[str, Any] | None:
    """Return the first manifest item matching one of the requested roles."""
    wanted = {str(item).casefold() for item in reference_types}
    return next(
        (
            item
            for item in reference_manifest
            if str(item.get("type") or "").casefold() in wanted
        ),
        None,
    )


def _weapon_reference_label(item: dict[str, Any] | None, fallback: str) -> str:
    """Format a physical reference identity without inventing an asset name."""
    if not item:
        return fallback
    index = item.get("index", "?")
    name = _clean_text(item.get("name"))
    return f"IMAGE {index}" + (f" ({name})" if name else "")


def _weapon_transform_contract(value: Any) -> dict[str, Any] | None:
    """Copy only stable transform fields in the source contract order."""
    if not isinstance(value, dict):
        return None
    transform: dict[str, Any] = {}
    for key in ("position", "rotation", "scale", "fit"):
        if key in value:
            transform[key] = copy.deepcopy(value[key])
    return transform or None


def _weapon_prompt_component(
    source_contract: dict[str, Any],
    selected_id: str,
) -> dict[str, Any]:
    """Build the machine-readable weapon contract without display-name guesses."""
    components = source_contract.get("components")
    source_component = next(
        (
            component
            for component in components or []
            if isinstance(component, dict)
            and _clean_text(component.get("id")) == selected_id
        ),
        {},
    )
    selected = select_weapon_component(
        source_contract,
        weapon_component_id=selected_id,
    )
    component: dict[str, Any] = {
        "id": selected["id"],
        "role": "weapon",
    }
    # asset_id is an opaque source identifier. It is intentionally not emitted
    # as ``name``: providers must not invent a human-readable weapon identity.
    if selected["asset_id"]:
        component["asset_id"] = selected["asset_id"]
    if selected["attach_to"]:
        component["attach_to"] = selected["attach_to"]
    if selected["hand"]:
        component["hand"] = selected["hand"]
    transform = _weapon_transform_contract(source_component.get("transform"))
    if transform is not None:
        component["transform"] = transform
    return component


def _compile_weapon_prompt(
    normalized: dict[str, Any],
    reference_manifest: list[dict[str, Any]],
    additional_instructions: str,
) -> str:
    """Compile the isolated weapon pass while preserving the source grid contract."""
    output = normalized["output"]
    camera = normalized["camera"]
    framing = normalized["framing"]
    source_contract = (
        normalized.get("source_contract")
        if isinstance(normalized.get("source_contract"), dict)
        else {}
    )
    layer_contract = normalized["layer_contract"]
    selected_id = layer_contract["weapon_component_id"]
    weapon_component = _weapon_prompt_component(source_contract, selected_id)

    weapon_reference = _weapon_manifest_item(
        reference_manifest, "weapon_reference", "weapon_identity"
    )
    character_reference = _weapon_manifest_item(
        reference_manifest, "character_full", "character"
    )
    weapon_guide = _weapon_manifest_item(
        reference_manifest,
        "weapon_guide",
        "weapon_beauty",
        "weapon_silhouette",
        "weapon_lineart",
    )
    weapon_image = _weapon_reference_label(
        weapon_reference, "the uploaded weapon visual reference"
    )
    character_image = _weapon_reference_label(
        character_reference, "the approved character_full layer"
    )
    guide_image = _weapon_reference_label(
        weapon_guide, "the uploaded weapon structural guide"
    )

    source_camera = source_contract.get("camera")
    source_camera = source_camera if isinstance(source_camera, dict) else {}
    camera_projection = _clean_text(source_camera.get("type"), camera["projection"])
    camera_preset = _clean_text(source_camera.get("preset"), camera["preset"])
    source_action = source_contract.get("action")
    source_action = source_action if isinstance(source_action, dict) else {}
    action_label = _clean_text(
        source_action.get("clip_name") or source_action.get("name"),
        "structural animation",
    )
    direction_rows = [
        {
            "row": int(row.get("index") or index),
            "id": _clean_text(row.get("id")),
            "vector": list(row.get("vector") or []),
        }
        for index, row in enumerate(normalized["rows"], start=1)
    ]
    direction_lines = ",\n".join(
        "        "
        + json.dumps(row, ensure_ascii=False, separators=(", ", ": "))
        for row in direction_rows
    )
    background = _clean_text(output.get("background"), "transparent")
    background_value = (
        "transparent"
        if background.casefold() == "transparent"
        else "#00FF00"
        if background.casefold() in {"#00ff00", "00ff00", "lemon green", "lemongreen"}
        else background
    )
    if background.casefold() == "transparent":
        background_instruction = (
            "Use a fully transparent RGBA background in every pixel outside the weapon."
        )
    elif background.casefold() in {"#00ff00", "00ff00", "lemon green", "lemongreen"}:
        background_instruction = (
            "Use a perfectly uniform pure lemon-green background (#00FF00) in every "
            "pixel outside the weapon; do not use transparency, gradients or shadows."
        )
    else:
        background_instruction = (
            f"Use a perfectly uniform {background} background in every pixel outside the weapon."
        )
    spritesheet_contract = f"""{{
  "content": {{
    "directions": {{
      "count": {GRID_ROWS},
      "rows": [
{direction_lines}
      ]
    }},
    "camera": {{ "type": {json.dumps(camera_projection)}, "preset": {json.dumps(camera_preset)}, "shadow": false }},
    "action": {json.dumps(action_label)},
    "background": {json.dumps(background_value)},
    "pixel_ratio": {json.dumps(f"{output['width']}x{output['height']}")},
    "weapon": {json.dumps(weapon_component, ensure_ascii=False, separators=(", ", ": "))}
  }}
}}"""

    extra = (
        f"\n\nAdditional instruction (supplemental only):\n{additional_instructions}"
        if additional_instructions
        else ""
    )
    return f"""WEAPON LAYER CONTRACT — REQUIRED
Generate only the isolated weapon layer for this step. The output must contain one complete weapon per cell, including portions that pass behind the character; do not clip the weapon to the character silhouette.

REFERENCE AUTHORITY CONTRACT
Use {weapon_image} as the authoritative weapon design: preserve its silhouette, materials, colors, markings and recognizable design in every cell. Do not transfer character identity from another reference into the weapon.
Use {character_image} as positioning and style context only. It establishes the approved character's frame, attachment context, scale and rendering relationship; it is not an authority for drawing the character in this output.
Use {guide_image} as the authoritative position, orientation, scale and animation phase guide for the weapon in every cell. Follow its attachment and depth envelope without copying guide lines or structural proxy appearance.

EXCLUSION CONTRACT
Do not draw any character, hand, body part, fingers, armor, clothing, shadow or silhouette. Do not draw any additional prop, shield, accessory, duplicate weapon or detached fragment. The final layer contains only the selected weapon over the requested background.

GRID AND ALIGNMENT CONTRACT
The final result must be one 8x8 spritesheet with exactly 64 cells. Preserve the original direction of each row, the original animation phase of each column, camera, framing, anchor and cell boundaries. Keep every weapon pixel inside its corresponding cell and never reorder, mirror, rotate or combine cells.
Attachment, hand, transform and source identity are machine-readable in spritesheetContract below. Treat asset_id as an opaque identifier; never invent or infer a weapon name from it.

IMPORTANT: {background_instruction}

spritesheetContract:
{spritesheet_contract}

Before producing the PNG, verify that every cell contains exactly the selected weapon aligned to the weapon guide, that no character or hand pixels leaked into the layer, and that the output preserves alpha/background and all 64 positions.{extra}
""".strip() + "\n"


def compile_prompt(
    spec: dict[str, Any],
    reference_manifest: list[dict[str, Any]],
    additional_instructions: str = "",
) -> str:
    """Compile a deterministic provider-neutral prompt from a RenderSpec."""
    normalized = normalize_render_spec(spec)
    output = normalized["output"]
    grid = output["grid"]
    asset = normalized["asset"]
    style = asset["style"]
    camera = normalized["camera"]
    framing = normalized["framing"]
    mode = asset["mode"]
    rows = normalized["rows"]
    prompt_options = normalized.get("prompt_options") or {}
    background = output["background"]
    extra = _clean_text(additional_instructions)
    source_contract = (
        normalized.get("source_contract")
        if isinstance(normalized.get("source_contract"), dict)
        else {}
    )
    conflicts = validate_additional_instructions(
        extra,
        mode=mode,
        direction_rows=source_contract.get("directions"),
    )
    if conflicts:
        raise ValueError(
            "Instruções adicionais conflitam com o contrato fixo: " + "; ".join(conflicts)
        )
    if mode == "character_animation":
        return _compile_character_prompt(normalized, reference_manifest, extra)
    asset_spec = f"""Asset mode: {mode}
Asset name: {asset.get('name') or 'unnamed asset'}
Global description: {asset.get('global_description') or 'None specified.'}
Row semantics: {ROW_SEMANTICS.get(mode, ROW_SEMANTICS['custom'])}
Column semantics: {COLUMN_SEMANTICS.get(mode, COLUMN_SEMANTICS['custom'])}"""
    style_contract = f"""Style preset: {style['preset'] or 'None specified.'}
Style description: {style['description'] or 'None specified.'}
Maintain consistent rendering language, material treatment, color logic, detail density and contrast.
Apply this treatment without overriding IMAGE 1."""
    prompt = f"""Prompt contract: {PROMPT_SCHEMA}

You are generating a production-ready game spritesheet.

==================================================
SYSTEM / FIXED OUTPUT CONTRACT
==================================================

Create exactly one {grid['columns']}-column by {grid['rows']}-row spritesheet.

Canvas: {output['width']} x {output['height']}
Grid: exactly {grid['rows']} rows, exactly {grid['columns']} columns, exactly {grid['rows'] * grid['columns']} cells.
Each cell has equal dimensions, contains one complete intended asset instance and must not show the grid.
Every asset must remain completely inside its own cell.
Never crop or allow any visible element to cross into another cell.
Do not draw labels, numbers, borders, UI, unrelated objects or grid lines.
Background: {background} perfectly uniform in every empty area.

==================================================
REFERENCE IMAGE CONTRACT
==================================================

{_reference_prompt(reference_manifest)}

==================================================
IDENTITY TRANSFER CONTRACT — HIGHEST VISUAL AUTHORITY
==================================================

{_identity_transfer_prompt(reference_manifest)}

==================================================
OPERATION / ASSET CONTRACT
==================================================

{asset_spec}

==================================================
DIRECTION CONTRACT
==================================================

{_direction_contract_prompt() if mode == 'character_animation' else 'This asset mode does not use the character direction contract.'}

==================================================
CAMERA CONTRACT
==================================================

Projection: {camera['projection']}
Camera preset: {camera['preset']}
Elevation: {camera['elevation_deg']} degrees
Azimuth: {camera['azimuth_deg']} degrees
Keep camera, perspective, zoom, horizon and lighting logic identical across every cell.

==================================================
FRAMING CONTRACT
==================================================

Anchor: {framing['anchor']}
Scale policy: {framing['scale_policy']}
Safe-area occupancy: {framing['safe_area']:.3g}
Allow crop: {str(framing['allow_crop']).lower()}
Allow cross-cell overlap: {str(framing['allow_cross_cell_overlap']).lower()}
Preserve consistent scale and spacing within each row.

==================================================
STYLE CONTRACT
==================================================

{style_contract}

==================================================
ROW / CELL SPECIFICATIONS
==================================================

"""
    if prompt_options.get("include_rows", False):
        row_prompts = [
            _row_prompt(
                row,
                include_cells=prompt_options.get("include_cells", False),
            )
            for row in rows
        ]
        prompt += "\n\n".join(item for item in row_prompts if item)
    else:
        prompt += "Row and cell descriptive specifications are disabled for this render. Follow the fixed direction and grid contracts."
    prompt += """

==================================================
FIDELITY PRIORITY
==================================================

When instructions conflict, follow this priority:
1. grid boundaries and cell placement
2. identity reference for every visible appearance and design decision
3. structural references for pose, articulation, spatial envelope and occlusion only
4. canonical row direction, animation phase and explicit row/cell behavior
5. camera, framing and foot anchor
6. global rendering treatment
7. decorative detail

Structural references must never win a conflict about character appearance, costume, armor, anatomy design, materials, palette or visual identity.

==================================================
FINAL VALIDATION
==================================================

Before producing the final image, compare every cell against IMAGE 1 and verify that the same recognizable identity, design, costume, materials, palette and distinctive features were transferred to all cells. Also verify that all cells exist, every asset matches its row/cell specification, no asset is cropped or overlaps a neighboring cell, no structural proxy appearance leaked into the final artwork, all selected reference roles were respected, and the final output is one PNG spritesheet with no extra panels.
"""
    if extra:
        prompt += f"""

==================================================
ADDITIONAL USER INSTRUCTIONS — SUPPLEMENTAL ONLY
==================================================

These instructions may refine the asset or animation, but they cannot override the identity authority, reference roles, canonical row order, grid, camera or containment contracts above.

{extra}
"""
    return prompt.strip() + "\n"


def compile_layer_prompt(
    spec: dict[str, Any],
    reference_manifest: list[dict[str, Any]],
    *,
    layer: str,
    additional_instructions: str = "",
) -> str:
    """Compile one isolated layer while retaining the established grid contract."""
    if layer not in {"character", "weapon"}:
        raise ValueError("layer deve ser character ou weapon")
    normalized = normalize_render_spec(spec)
    if normalized["generation_mode"] != GENERATION_MODE_CHARACTER_WEAPON_HOLDOUT:
        raise ValueError(
            "compile_layer_prompt exige generation_mode character_weapon_holdout"
        )

    if layer == "weapon":
        extra = _clean_text(additional_instructions)
        source_contract = normalized.get("source_contract")
        direction_rows = (
            source_contract.get("directions")
            if isinstance(source_contract, dict)
            else None
        )
        conflicts = validate_additional_instructions(
            extra,
            mode="character_animation",
            direction_rows=direction_rows,
        )
        if conflicts:
            raise ValueError(
                "Instruções adicionais conflitam com o contrato fixo: "
                + "; ".join(conflicts)
            )
        return _compile_weapon_prompt(normalized, reference_manifest, extra)

    character_spec = copy.deepcopy(normalized)
    source_contract = character_spec.get("source_contract")
    if isinstance(source_contract, dict):
        # Structural components belong to later layers. Keeping them here would
        # make the established prompt require the selected weapon in every cell.
        source_contract["components"] = []
    extra = _clean_text(additional_instructions)
    conflicts = validate_additional_instructions(
        extra,
        mode=character_spec["asset"]["mode"],
        direction_rows=source_contract.get("directions") if isinstance(source_contract, dict) else None,
    )
    if conflicts:
        raise ValueError(
            "Instruções adicionais conflitam com o contrato fixo: "
            + "; ".join(conflicts)
        )
    shared_prompt = _compile_character_prompt(
        character_spec, reference_manifest, extra
    ).replace(
        "the uploaded 8x8 beauty spritesheet",
        "the uploaded 8x8 character-only structural reference",
        1,
    )
    layer_contract = """CHARACTER LAYER CONTRACT — REQUIRED
Generate only the isolated character layer for this step.
Do not draw any weapon, shield or visible prop in any cell, including detached fragments, silhouettes or shadows from those objects.
Preserve the exact gripping hand pose, finger placement, wrist angle and spacing established by the structural guides even though the held object is absent.
Do not compose, position, infer or preview the weapon layer in this step. Composition is outside this prompt.
Keep every empty pixel outside the character fully transparent RGBA."""
    return layer_contract + "\n\n" + shared_prompt


def compile_modular_layer_prompt(
    spec: dict[str, Any],
    reference_manifest: list[dict[str, Any]],
    *,
    layer_id: str,
    additional_instructions: str = "",
) -> str:
    """Compile an isolated base or arbitrary equipment layer for the v2 flow."""
    normalized = normalize_render_spec(spec)
    if normalized["generation_mode"] != GENERATION_MODE_CHARACTER_COMPONENT_HOLDOUT:
        raise ValueError(
            "compile_modular_layer_prompt exige generation_mode character_component_holdout"
        )
    contract = normalized["layer_contract"]
    requested_id = _clean_text(layer_id)
    known_ids = contract["composition_order"]
    if requested_id not in known_ids:
        raise ValueError(
            f"layer_id desconhecido: {requested_id}; esperado um de {known_ids}"
        )
    extra = _clean_text(additional_instructions)
    source_contract = normalized.get("source_contract")
    source_contract = source_contract if isinstance(source_contract, dict) else {}
    conflicts = validate_additional_instructions(
        extra,
        mode=normalized["asset"]["mode"],
        direction_rows=source_contract.get("directions"),
    )
    if conflicts:
        raise ValueError(
            "Instruções adicionais conflitam com o contrato fixo: " + "; ".join(conflicts)
        )

    if requested_id == contract["base_id"]:
        base_spec = copy.deepcopy(normalized)
        base_source = base_spec.get("source_contract")
        if isinstance(base_source, dict):
            base_source["components"] = []
        shared_prompt = _compile_character_prompt(
            base_spec, reference_manifest, extra
        ).replace(
            "the uploaded 8x8 beauty spritesheet",
            "the uploaded 8x8 character-only structural reference",
            1,
        )
        excluded = ", ".join(contract["component_ids"])
        return (
            "IMMUTABLE BASE LAYER CONTRACT — REQUIRED\n"
            "Generate only the complete character base. Do not draw any detachable "
            f"component selected for another layer ({excluded}). Preserve pose, hands, "
            "camera, frame registration and foot anchor. Empty pixels must be transparent "
            "RGBA. This base will be reused unchanged with every equipment combination; "
            "never pre-cut holes for another layer.\n\n"
            + shared_prompt
        )

    selected = select_layer_components(
        source_contract, component_ids=[requested_id]
    )[0]
    layer = next(item for item in contract["layers"] if item["id"] == requested_id)
    output = normalized["output"]
    grid = output["grid"]
    references = "\n".join(
        f"IMAGE {item.get('index', index)} — "
        f"{_clean_text(item.get('name') or item.get('type'), 'reference')}"
        for index, item in enumerate(reference_manifest, start=1)
    ) or "No image references were declared."
    component_contract = {
        "id": requested_id,
        "kind": layer["kind"],
        "source": selected,
        "grid": {
            "rows": grid["rows"],
            "columns": grid["columns"],
            "width": output["width"],
            "height": output["height"],
        },
        "composition": {
            "base_id": contract["base_id"],
            "z": layer["z"],
            "visibility": "computed after generation from Blender holdout/depth",
        },
    }
    supplemental = (
        f"\n\nSUPPLEMENTAL USER INSTRUCTIONS\n{extra}" if extra else ""
    )
    return f"""MODULAR COMPONENT LAYER CONTRACT — REQUIRED
Generate only component {requested_id!r} ({layer['kind']}) on a transparent RGBA background. Do not draw the character, hands, body, another garment, another weapon, shadows, labels, grid lines or detached fragments from another component.

Generate the complete component in its exact animated position, including portions that pass behind the character or another component. Do not cut it to the character silhouette and do not invent visibility. A deterministic Blender holdout/depth pass will create the visible mask after this image is generated.

Preserve exactly {grid['rows']} rows by {grid['columns']} columns ({grid['rows'] * grid['columns']} cells), row directions, animation phase, camera, scale, pivot, foot anchor and cell boundaries. Keep all pixels inside their corresponding cell.

REFERENCE ORDER
{references}

machineReadableComponentContract:
{json.dumps(component_contract, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)}

Before returning the PNG, verify that every cell contains exactly one complete instance of {requested_id!r}, aligned to its structural guide, with no character pixels and no component from another layer.{supplemental}
""".strip() + "\n"


def compile_provider_prompt(
    spec: dict[str, Any],
    reference_manifest: list[dict[str, Any]],
    additional_instructions: str = "",
    *,
    provider: str = "openai",
) -> str:
    """Compile the exact prompt sent to a provider, including physical input order."""
    provider_name = _clean_text(provider, "openai").casefold()
    normalized = normalize_render_spec(spec)
    output_size = (
        normalized["output"]["width"],
        normalized["output"]["height"],
    )
    prompt = compile_prompt(normalized, reference_manifest, additional_instructions)
    if normalized["asset"]["mode"] == "character_animation":
        role_descriptions = {
            "identity": "the authoritative character reference",
            "identity_lineart": "the lineart derived from the authoritative character reference",
            "beauty": "the aligned beauty spritesheet",
            "bones": "the aligned bones guide",
            "lineart": "the aligned lineart guide",
            "frame_control": "the aligned 8x8 frame-control grid",
        }
        identity_lineart_item = next(
            (item for item in reference_manifest if item.get("type") == "identity_lineart"),
            None,
        )
        if identity_lineart_item and identity_lineart_item.get("guide_mode") == "canny_edges":
            role_descriptions["identity_lineart"] = (
                "the Canny edge guide derived from the authoritative character reference"
            )
        ordered_inputs = []
        ordinals = ("first", "second", "third", "fourth", "fifth", "sixth")
        for position, item in enumerate(reference_manifest):
            role = role_descriptions.get(
                str(item.get("type")),
                f"the {str(item.get('type') or 'structural')} reference",
            )
            name = _clean_text(item.get("name"))
            suffix = f" ({name})" if name and item.get("type") == "identity" else ""
            ordinal = ordinals[position] if position < len(ordinals) else f"{position + 1}th"
            if position == 0:
                ordered_inputs.append(f"{ordinal} image is {role}{suffix}")
            else:
                ordered_inputs.append(f"{ordinal} is {role}{suffix}")
        input_contract = "; ".join(ordered_inputs)
        return (
            prompt.rstrip()
            + f"\n\nUse the {len(reference_manifest)} uploaded images in this order: "
            + input_contract
            + ". Preserve the 8x8 grid, cell boundaries, camera, pose, direction, "
            "animation phase, scale and foot anchor. Structural guides must not appear "
            f"in the final artwork. Return exactly one {output_size[0]}x{output_size[1]} PNG "
            "spritesheet with "
            "no labels, borders, grid lines or extra panels.\n"
        )
    provider_label = {
        "openai": "OpenAI",
        "google": "Google Gemini",
        "gemini": "Google Gemini",
        "qwen": "Qwen",
    }.get(provider_name, provider_name or "image provider")
    lines = [
        "==================================================",
        "PROVIDER INPUT AND DELIVERY CONTRACT",
        "==================================================",
        "",
        f"The {provider_label} request receives the images in this exact physical order:",
    ]
    for item in reference_manifest:
        name = _clean_text(item.get("name"))
        label = str(item.get("type", "reference")).upper()
        suffix = f" — {name}" if name else ""
        lines.append(f"IMAGE {item['index']} = {label}_REFERENCE{suffix}.")
    lines.extend(
        [
            "",
            "Inspect IMAGE 1 first and use it as the authoritative visible identity in every output cell.",
            "Use every other image only within its declared structural role. Do not average, blend or merge the structural proxy's appearance with IMAGE 1.",
        ]
    )
    if any(item.get("type") == "frame_control" for item in reference_manifest):
        lines.extend(
            [
                "The FRAME_CONTROL_REFERENCE marks exact cell boundaries; keep every visible pixel inside its own cell.",
                "Do not reproduce its lines in the output.",
            ]
        )
    identity_lineart_item = next(
        (item for item in reference_manifest if item.get("type") == "identity_lineart"),
        None,
    )
    if identity_lineart_item:
        guide_description = (
            "Canny edge guide"
            if identity_lineart_item.get("guide_mode") == "canny_edges"
            else "lineart guide"
        )
        lines.extend(
            [
                f"IMAGE {identity_lineart_item['index']} is a derived {guide_description} from IMAGE 1. Use it only for the identity contour and silhouette; IMAGE 1 remains authoritative for appearance, colors, materials and style.",
            ]
        )
    if provider_name == "openai":
        lines.extend(
            [
                "The requested output must be a PNG with a fully transparent RGBA background in every empty area, not a solid color and not inherited from any input image.",
            ]
        )
    lines.extend(
        [
            f"Return exactly one {output_size[0]}x{output_size[1]} PNG spritesheet with 8 rows, 8 columns and no labels, borders, grid lines or extra panels.",
        ]
    )
    return prompt.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def spec_json(spec: dict[str, Any]) -> str:
    """Stable pretty JSON used by logs and debugging views."""
    return json.dumps(normalize_render_spec(spec), indent=2, ensure_ascii=False)
