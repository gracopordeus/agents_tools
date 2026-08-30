#!/usr/bin/env python3
"""Generate the pixel-mcp PoC through a real stdio MCP JSON-RPC client."""
from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

from path_config import PROJECT_ROOT, TOOLS_ROOT

ROOT = PROJECT_ROOT
PIXEL_MCP_ROOT = TOOLS_ROOT / "pixel-mcp"
SERVER = PIXEL_MCP_ROOT / "src/index.js"
DATA_DIR = ROOT / "artifacts/pixel_mcp_poc/pixel-mcp-data"
OUTPUT_DIR = ROOT / "assets/generated/pixel_mcp_poc/ember_orb"
BASE_SHEET_PATH = OUTPUT_DIR / "ember_orb_idle_base.png"
SHEET_PATH = OUTPUT_DIR / "ember_orb_idle_4x.png"
GIF_PATH = OUTPUT_DIR / "ember_orb_idle_4x.gif"
METADATA_PATH = OUTPUT_DIR / "ember_orb_idle.json"
FRAME_PREVIEW_PATH = ROOT / "artifacts/pixel_mcp_poc/ember_orb_view_frames.png"
GRID_PATH = ROOT / "artifacts/pixel_mcp_poc/ember_orb_grid.txt"

PALETTE = [
    "#2b1b3d",
    "#5b2e8a",
    "#a13b4b",
    "#e65c3c",
    "#f6c453",
    "#fff1a8",
]

GRID = "\n".join(
    [
        "................",
        "................",
        "......11........",
        "....11222211....",
        "...1122332211...",
        "..112234432211..",
        "..112345543211..",
        ".11234566543211.",
        ".11234566543211.",
        "..112345543211..",
        "..112234432211..",
        "...1122332211...",
        "....11222211....",
        "......1111......",
        "................",
        "................",
    ]
)


class McpStdioClient:
    """Small line-delimited JSON-RPC client for the MCP stdio transport."""

    def __init__(self) -> None:
        self._next_id = 1
        self._process = subprocess.Popen(
            ["node", str(SERVER), "--data-dir", str(DATA_DIR)],
            cwd=PIXEL_MCP_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        while True:
            line = self._process.stdout.readline() if self._process.stdout else b""
            if not line:
                raise RuntimeError(
                    f"pixel-mcp closed stdio while waiting for {method} "
                    f"(exit={self._process.poll()})"
                )
            message = json.loads(line.decode("utf-8"))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise RuntimeError(f"MCP {method} error: {message['error']}")
            return message["result"]

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if result.get("isError"):
            raise RuntimeError(f"MCP tool {name} failed: {text_of(result)}")
        return result

    def _send(self, message: dict[str, Any]) -> None:
        if not self._process.stdin:
            raise RuntimeError("pixel-mcp stdin is unavailable")
        self._process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self._process.stdin.flush()

    def close(self) -> None:
        if self._process.stdin:
            self._process.stdin.close()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()


def text_of(result: dict[str, Any]) -> str:
    return "\n".join(
        item.get("text", "")
        for item in result.get("content", [])
        if item.get("type") == "text"
    )


def first_image(result: dict[str, Any]) -> bytes:
    for item in result.get("content", []):
        if item.get("type") == "image" and item.get("data"):
            return base64.b64decode(item["data"])
    raise RuntimeError("MCP response did not contain an image")


def extract_doc_id(text: str) -> str:
    for token in text.replace("\n", " ").split():
        if token.startswith("doc-"):
            return token.rstrip(".,")
    raise RuntimeError(f"MCP response did not contain a document id: {text}")


def scale_nearest(source: Path, destination: Path, scale: int) -> None:
    image = Image.open(source).convert("RGBA")
    image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST).save(
        destination
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FRAME_PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    client = McpStdioClient()
    call_names: list[str] = []

    def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        call_names.append(name)
        return client.call_tool(name, arguments)

    try:
        initialized = client.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "simple-arpg-pixel-mcp-poc", "version": "0.1.0"},
            },
        )
        client.notify("notifications/initialized", {})

        tools = client.request("tools/list", {})
        tool_names = {tool["name"] for tool in tools["tools"]}
        required_tools = {
            "create_document",
            "paste_grid",
            "add_frame",
            "shift_layer",
            "view_frames",
            "lint",
            "pack_sprite_sheet",
            "export",
        }
        missing = sorted(required_tools - tool_names)
        if missing:
            raise RuntimeError(f"Required MCP tools are unavailable: {missing}")

        created = call("create_document", {"width": 16, "height": 16, "palette": PALETTE})
        doc_id = extract_doc_id(text_of(created))
        call(
            "paste_grid",
            {
                "doc_id": doc_id,
                "x": 0,
                "y": 0,
                "grid_text": GRID,
                "skip_transparent": False,
                "render": False,
            },
        )
        call(
            "set_frame_duration",
            {"doc_id": doc_id, "frame": 0, "duration_ms": 125},
        )

        frame_shifts = [0, -1, -2, -1]
        for frame in range(1, len(frame_shifts)):
            call(
                "add_frame",
                {"doc_id": doc_id, "duplicate_from": 0, "duration_ms": 125},
            )
            call("set_active_frame", {"doc_id": doc_id, "frame": frame})
            call(
                "shift_layer",
                {
                    "doc_id": doc_id,
                    "dx": 0,
                    "dy": frame_shifts[frame],
                    "wrap": False,
                    "render": False,
                },
            )

        grid = call("view_text", {"doc_id": doc_id, "frame": 0})
        GRID_PATH.write_text(f"{text_of(grid)}\n", encoding="utf-8")

        lint = call("lint", {"doc_id": doc_id})
        frame_preview = call("view_frames", {"doc_id": doc_id, "onion": True})
        FRAME_PREVIEW_PATH.write_bytes(first_image(frame_preview))

        call(
            "pack_sprite_sheet",
            {
                "sprites": [
                    {"doc_id": doc_id, "frame": frame} for frame in range(4)
                ],
                "columns": 4,
                "cell_width": 16,
                "cell_height": 16,
                "path": str(BASE_SHEET_PATH),
            },
        )
        scale_nearest(BASE_SHEET_PATH, SHEET_PATH, 4)

        call(
            "export",
            {
                "doc_id": doc_id,
                "format": "gif",
                "scale": 4,
                "loop": True,
                "path": str(GIF_PATH),
            },
        )

        metadata = {
            "id": "pixel_mcp_poc_ember_orb_idle",
            "generator": "pixel-mcp",
            "generator_version": "git:dd21c15",
            "generator_path": str(PIXEL_MCP_ROOT),
            "transport": "stdio-json-rpc",
            "protocol_version": initialized["protocolVersion"],
            "data_dir": str(DATA_DIR),
            "doc_id": doc_id,
            "base_width": 16,
            "base_height": 16,
            "frame_width": 16,
            "frame_height": 16,
            "frame_count": 4,
            "frame_duration_ms": 125,
            "fps": 8,
            "loop": True,
            "scale": 4,
            "palette": PALETTE,
            "frame_shifts": frame_shifts,
            "outputs": {
                "spritesheet": str(SHEET_PATH.relative_to(ROOT)),
                "preview_gif": str(GIF_PATH.relative_to(ROOT)),
                "metadata": str(METADATA_PATH.relative_to(ROOT)),
                "base_grid": str(GRID_PATH.relative_to(ROOT)),
                "frame_preview": str(FRAME_PREVIEW_PATH.relative_to(ROOT)),
            },
            "lint": text_of(lint),
            "mcp_tools": sorted(set(call_names)),
            "call_count": len(call_names),
        }
        METADATA_PATH.write_text(f"{json.dumps(metadata, indent=2)}\n", encoding="utf-8")

        print(json.dumps({
            "doc_id": doc_id,
            "protocol_version": initialized["protocolVersion"],
            "tool_count": len(tool_names),
            "call_count": len(call_names),
            "spritesheet": str(SHEET_PATH),
            "gif": str(GIF_PATH),
            "metadata": str(METADATA_PATH),
        }, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
