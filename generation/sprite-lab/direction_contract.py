"""Canonical camera-direction contract shared by Sprite Lab renders."""

from __future__ import annotations

DIRECTION_ROWS = ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8")
DIRECTION_LABELS = {
    "r1": "south",
    "r2": "south_east",
    "r3": "east",
    "r4": "north_east",
    "r5": "north",
    "r6": "north_west",
    "r7": "west",
    "r8": "south_west",
}
# Coordinates are screen-space targets: x grows east and y grows north.
DIRECTION_TARGETS = {
    row: target
    for row, target in zip(
        DIRECTION_ROWS,
        ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)),
    )
}
# The rotating inspection GIF advances through the same camera order.
ROTATION_SEQUENCE = tuple((row, index) for index, row in enumerate(DIRECTION_ROWS))

DIRECTION_CONTRACT = {
    "schema": "sprite_lab.direction_contract/v1",
    "rows": [
        {"row": row, "label": DIRECTION_LABELS[row], "target": list(DIRECTION_TARGETS[row])}
        for row in DIRECTION_ROWS
    ],
    "rotation_sequence": [
        {"row": row, "phase": phase + 1} for row, phase in ROTATION_SEQUENCE
    ],
}


def ordered_subset(rows: list[str] | tuple[str, ...]) -> bool:
    """Return whether rows preserve the canonical clockwise order."""
    return tuple(rows) == tuple(row for row in DIRECTION_ROWS if row in rows)
