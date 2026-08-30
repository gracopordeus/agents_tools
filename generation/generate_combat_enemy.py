#!/usr/bin/env python3
"""Generate the deterministic 32x32 placeholder enemy sprite."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

from path_config import PROJECT_ROOT

W = H = 32
COLORS = {
    "outline": (23, 32, 43, 255),
    "body": (184, 46, 56, 255),
    "body_shadow": (116, 30, 48, 255),
    "eye": (255, 205, 84, 255),
    "shadow": (36, 50, 71, 180),
}


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def main() -> None:
    image = [[(0, 0, 0, 0) for _ in range(W)] for _ in range(H)]

    def pixel(x: int, y: int, color: tuple[int, int, int, int]) -> None:
        if 0 <= x < W and 0 <= y < H:
            image[y][x] = color

    for y in range(25, 29):
        for x in range(7, 25):
            if abs(x - 16) <= 9 - abs(y - 27):
                pixel(x, y, COLORS["shadow"])
    for y in range(7, 25):
        half = max(0, 9 - abs(y - 16))
        for x in range(16 - half, 17 + half):
            edge = half == 0 or x in (16 - half, 16 + half)
            pixel(x, y, COLORS["outline"] if edge else COLORS["body"])
    for y in range(12, 22):
        for x in range(18, 24):
            pixel(x, y, COLORS["body_shadow"])
    pixel(12, 14, COLORS["eye"])
    pixel(20, 14, COLORS["eye"])

    raw = b"".join(b"\0" + b"".join(bytes(p) for p in row) for row in image)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    output = PROJECT_ROOT / "assets/generated/combat_enemy_32.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    print(output)


if __name__ == "__main__":
    main()
