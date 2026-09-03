"""Canonical camera-direction contract shared by Sprite Lab renders."""

from __future__ import annotations

DIRECTION_ROWS = ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8")
DIRECTION_LABELS = {
    "r1": "north",
    "r2": "north_east",
    "r3": "east",
    "r4": "south_east",
    "r5": "south",
    "r6": "south_west",
    "r7": "west",
    "r8": "north_west",
}
# These are the physical screen-space targets consumed by the existing
# Blender camera loop.  Keep them stable: the semantic labels above are
# calibrated against the exported image rows and must not alter camera yaw.
DIRECTION_TARGETS = {
    row: target
    for row, target in zip(
        DIRECTION_ROWS,
        ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)),
    )
}
# Semantic compass vectors are kept separate from the physical Blender camera
# targets.  This lets the contract match the exported rows without changing
# the already-correct camera capture flow.
DIRECTION_VECTORS = {
    "r1": (0, 1),
    "r2": (1, 1),
    "r3": (1, 0),
    "r4": (1, -1),
    "r5": (0, -1),
    "r6": (-1, -1),
    "r7": (-1, 0),
    "r8": (-1, 1),
}


def ordered_subset(rows: list[str] | tuple[str, ...]) -> bool:
    """Return whether rows preserve the canonical clockwise order."""
    return tuple(rows) == tuple(row for row in DIRECTION_ROWS if row in rows)


def direction_contract_for(rows: list[str] | tuple[str, ...] | None = None) -> dict:
    """Return the positional contract for a full sheet or an ordered subset.

    ``row{n}`` is the physical position in a PNG.  ``id`` is the semantic
    direction consumed by prompts and game adapters.  Keeping both fields
    makes it impossible for a consumer to silently remap a row by its label.
    """
    row_ids = tuple(DIRECTION_ROWS if rows is None else rows)
    if not row_ids or not ordered_subset(row_ids):
        raise ValueError(f"rows fora da ordem canônica: {list(row_ids)!r}")
    return {
        "schema": "sprite_lab.direction_contract/v1",
        "rows": [
            {
                "row": index + 1,
                "row_id": row_id,
                "id": DIRECTION_LABELS[row_id],
                "label": DIRECTION_LABELS[row_id],
                "target": list(DIRECTION_TARGETS[row_id]),
                "vector": list(DIRECTION_VECTORS[row_id]),
            }
            for index, row_id in enumerate(row_ids)
        ],
        "rotation_sequence": [
            {"row": index + 1, "row_id": row_id, "phase": index + 1}
            for index, row_id in enumerate(row_ids)
        ],
        "gif_order": [DIRECTION_LABELS[row_id] for row_id in row_ids],
        "gif_starts_with": DIRECTION_LABELS[row_ids[0]],
        "frame_order": "columns_0_to_phases_minus_1",
    }


# The rotating inspection GIF advances through the same camera order.
ROTATION_SEQUENCE = tuple((row, index) for index, row in enumerate(DIRECTION_ROWS))
DIRECTION_CONTRACT = direction_contract_for()
