"""Structured AI render specifications and deterministic prompt compilation."""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from direction_contract import DIRECTION_LABELS, DIRECTION_ROWS, DIRECTION_VECTORS


SCHEMA = "sprite_lab.render_spec/v2"
PROMPT_SCHEMA = "sprite_lab.prompt_contract/v13"
GRID_ROWS = 8
GRID_COLUMNS = 8
OUTPUT_SIZE = 2048
DEFAULT_BACKGROUND = "transparent"

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
        "controls": "pose envelope, silhouette, geometry, contour, weapon placement and spatial boundaries",
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
            "elevation_deg": 35.264,
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
            key: {"enabled": True, **value} for key, value in REFERENCE_ROLES.items()
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

    output = incoming.get("output") if isinstance(incoming.get("output"), dict) else {}
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
        "elevation_deg": _float(incoming_camera.get("elevation_deg"), 35.264),
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
    source_contract = incoming.get("source_contract")
    if isinstance(source_contract, dict):
        spec["source_contract"] = copy.deepcopy(source_contract)
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
) -> list[dict[str, Any]]:
    """Build the ordered image-role contract used by every provider."""
    manifest = [
        {
            "index": 1,
            "type": "identity",
            "name": _clean_text(identity_name, "identity reference"),
            **REFERENCE_ROLES["identity"],
        }
    ]
    for index, channel in enumerate(channels, start=2):
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

When a structural reference contains a weapon or prop that is absent or partly hidden in {image}, preserve only its required pose, placement and contour, then render its design, materials and colors so they belong coherently to the identity from {image}.

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
    bones = image_by_type.get("bones")
    lineart = image_by_type.get("lineart")
    frame_control_item = image_by_type.get("frame_control")
    beauty_line = (
        f"Use IMAGE {beauty['index']}, the uploaded 8x8 beauty spritesheet, as the exact composition and layout source."
        if beauty
        else "Use the selected structural spritesheets as the exact composition and layout source."
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
        name = _clean_text(component.get("name") or component.get("role"), "prop")
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
        line for line in (bones_line, lineart_line, frame_control) if line
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
    prompt = compile_prompt(normalized, reference_manifest, additional_instructions)
    if normalized["asset"]["mode"] == "character_animation":
        role_descriptions = {
            "identity": "the authoritative character reference",
            "beauty": "the aligned beauty spritesheet",
            "bones": "the aligned bones guide",
            "lineart": "the aligned lineart guide",
            "frame_control": "the aligned 8x8 frame-control grid",
        }
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
            "in the final artwork. Return exactly one 2048x2048 PNG spritesheet with "
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
    if provider_name == "openai":
        lines.extend(
            [
                "The requested output must be a PNG with a fully transparent RGBA background in every empty area, not a solid color and not inherited from any input image.",
            ]
        )
    lines.extend(
        [
            "Return exactly one 2048x2048 PNG spritesheet with 8 rows, 8 columns and no labels, borders, grid lines or extra panels.",
        ]
    )
    return prompt.rstrip() + "\n\n" + "\n".join(lines) + "\n"


def spec_json(spec: dict[str, Any]) -> str:
    """Stable pretty JSON used by logs and debugging views."""
    return json.dumps(normalize_render_spec(spec), indent=2, ensure_ascii=False)
