#!/usr/bin/env python3
"""Generate a soldier walk cycle through the real pixel-mcp stdio API."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from generate_pixel_mcp_poc import McpStdioClient, extract_doc_id, text_of
from path_config import PROJECT_ROOT


ROOT = PROJECT_ROOT
SOURCE = ROOT / "assets/generated/pixelmaker_soldier/soldier_64.png"
OUTPUT_DIR = ROOT / "assets/generated/pixel_mcp_soldier_walk"
BASE_SHEET = OUTPUT_DIR / "soldier_walk_mcp_base.png"
SHEET = OUTPUT_DIR / "soldier_walk_mcp_64.png"
GIF = OUTPUT_DIR / "soldier_walk_mcp_64.gif"
METADATA = OUTPUT_DIR / "soldier_walk_mcp_64.json"
DATA_DIR = ROOT / "artifacts/pixel_mcp_soldier_walk/pixel-mcp-data"


def scale_nearest(source: Path, destination: Path, scale: int) -> None:
    image = Image.open(source).convert("RGBA")
    image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST).save(
        destination
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = McpStdioClient()
    calls: list[str] = []

    def call(name: str, arguments: dict[str, object]) -> dict[str, object]:
        calls.append(name)
        result = client.call_tool(name, arguments)
        if result.get("isError"):
            raise RuntimeError(f"MCP tool {name} failed: {text_of(result)}")
        return result

    try:
        client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "simple-arpg-soldier-walk", "version": "0.1.0"},
            },
        )
        client.notify("notifications/initialized", {})

        opened = call("open_document", {"path": str(SOURCE), "max_colors": 63})
        doc_id = extract_doc_id(text_of(opened)).rstrip(":")
        call("set_frame_duration", {"doc_id": doc_id, "frame": 0, "duration_ms": 125})

        # Alternating leg offsets create a readable walk while keeping the torso
        # and bottom-center pivot stable. Coordinates are in the 64x64 source.
        poses = [
            [],
            [("left_leg", -2, 0), ("right_leg", 1, 0)],
            [("left_leg", 1, 0), ("right_leg", -2, 0)],
            [("left_leg", 1, 0), ("right_leg", 1, 0)],
        ]
        regions = {"left_leg": [14, 43, 18, 21], "right_leg": [32, 43, 18, 21]}
        for frame, operations in enumerate(poses[1:], start=1):
            call("add_frame", {"doc_id": doc_id, "duplicate_from": 0, "duration_ms": 125})
            call("set_active_frame", {"doc_id": doc_id, "frame": frame})
            for region_name, dx, dy in operations:
                x, y, width, height = regions[region_name]
                call(
                    "move_region",
                    {
                        "doc_id": doc_id,
                        "region": {"rect": {"x": x, "y": y, "w": width, "h": height}},
                        "dx": dx,
                        "dy": dy,
                        "clip": True,
                        "render": False,
                    },
                )

        call("view_frames", {"doc_id": doc_id, "onion": True})
        lint = call("lint", {"doc_id": doc_id})
        call(
            "pack_sprite_sheet",
            {
                "sprites": [{"doc_id": doc_id, "frame": frame} for frame in range(4)],
                "columns": 4,
                "cell_width": 64,
                "cell_height": 64,
                "path": str(BASE_SHEET),
            },
        )
        scale_nearest(BASE_SHEET, SHEET, 1)
        call(
            "export",
            {"doc_id": doc_id, "format": "gif", "scale": 1, "loop": True, "path": str(GIF)},
        )

        metadata = {
            "generator": "pixel-mcp",
            "generator_version": "git:dd21c15",
            "source": str(SOURCE.relative_to(ROOT)),
            "doc_id": doc_id,
            "frame_count": 4,
            "frame_width": 64,
            "frame_height": 64,
            "frame_duration_ms": 125,
            "fps": 8,
            "loop": True,
            "pivot": [32, 64],
            "calls": calls,
            "lint": json.loads(text_of(lint)) if text_of(lint).startswith("{") else text_of(lint),
        }
        METADATA.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(f"PASS: generated {SHEET}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
