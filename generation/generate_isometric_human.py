#!/usr/bin/env python3
"""Deterministically generate the first isometric human placeholder sprite."""
from __future__ import annotations
import struct
import zlib
from pathlib import Path

from path_config import PROJECT_ROOT

W, H, FRAME_W, FRAME_H = 128, 80, 32, 40
C = {"outline": (23,32,43,255), "skin": (232,168,120,255), "skin_shadow": (184,102,88,255), "hair": (59,37,48,255), "tunic": (75,123,168,255), "tunic_shadow": (49,81,112,255), "trouser": (48,61,98,255), "boot": (32,29,43,255), "shadow": (36,50,71,210)}

def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

def write_png(path: Path, pixels) -> None:
    raw = b"".join(b"\0" + b"".join(bytes(p) for p in row) for row in pixels)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W,H,8,6,0,0,0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(data)

def rect(image, x, y, width, height, color) -> None:
    for py in range(max(0, y), min(H, y + height)):
        for px in range(max(0, x), min(W, x + width)):
            image[py][px] = color

def pixel(image, x, y, color) -> None:
    if 0 <= x < W and 0 <= y < H: image[y][x] = color

def draw_frame(image, frame: int, row: int) -> None:
    ox, oy = frame * FRAME_W, row * FRAME_H
    for y in range(35, 39):
        span = 8 - abs(y - 36)
        rect(image, ox + 16 - span, oy + y, span * 2 + 1, 1, C["shadow"])
    bob = 1 if (row == 0 and frame == 1) or (row == 1 and frame in (1,3)) else 0
    left_shift = -1 if row == 1 and frame in (1,2) else 0
    right_shift = 1 if row == 1 and frame in (0,3) else 0
    rect(image, ox+11+left_shift, oy+30, 5, 7, C["trouser"]); rect(image, ox+17+right_shift, oy+30, 5, 7, C["trouser"])
    rect(image, ox+10+left_shift, oy+36, 6, 3, C["boot"]); rect(image, ox+17+right_shift, oy+36, 6, 3, C["boot"])
    rect(image, ox+9, oy+17+bob, 14, 14, C["outline"]); rect(image, ox+11, oy+18+bob, 10, 12, C["tunic"]); rect(image, ox+17, oy+19+bob, 4, 10, C["tunic_shadow"])
    rect(image, ox+7, oy+19+bob, 4, 10, C["outline"]); rect(image, ox+8, oy+20+bob, 3, 8, C["tunic_shadow"]); rect(image, ox+21, oy+19+bob, 4, 10, C["outline"]); rect(image, ox+21, oy+20+bob, 3, 8, C["tunic"])
    rect(image, ox+8, oy+28+bob, 4, 3, C["skin_shadow"]); rect(image, ox+21, oy+28+bob, 4, 3, C["skin"])
    rect(image, ox+11, oy+7+bob, 11, 11, C["outline"]); rect(image, ox+13, oy+8+bob, 8, 8, C["skin"]); rect(image, ox+13, oy+8+bob, 8, 3, C["hair"]); rect(image, ox+12, oy+10+bob, 2, 5, C["hair"]); rect(image, ox+19, oy+11+bob, 2, 3, C["skin_shadow"]); pixel(image, ox+18, oy+12+bob, C["outline"])

def main() -> None:
    output = PROJECT_ROOT / "assets/generated/isometric_human_player.png"
    image = [[(0,0,0,0) for _ in range(W)] for _ in range(H)]
    for row, count in ((0,2),(1,4)):
        for frame in range(count): draw_frame(image, frame, row)
    output.parent.mkdir(parents=True, exist_ok=True); write_png(output, image); print(output)

if __name__ == "__main__": main()
