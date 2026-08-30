#!/usr/bin/env python3
"""Build a deterministic manifest for all D2R visual and character assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

CHARACTER_ROOTS = (
    "global/chars/",
    "global/monsters/",
    "global/mercs/",
    "hd/character/",
    "hd/global/chars/",
    "hd/global/monsters/",
    "hd/global/mercenaries/",
)

VISUAL_ROOT_PARTS = (
    "/automap/",
    "/cinematics/",
    "/environment/",
    "/items/",
    "/missiles/",
    "/objects/",
    "/overlays/",
    "/tiles/",
    "/ui/",
    "/video/",
)

VISUAL_EXTENSIONS = {
    ".anim",
    ".animations",
    ".bmp",
    ".cof",
    ".dcc",
    ".dc6",
    ".ds1",
    ".dt1",
    ".gif",
    ".jpeg",
    ".jpg",
    ".material",
    ".model",
    ".pal",
    ".png",
    ".skeleton",
    ".sprite",
    ".tga",
    ".texture",
    ".webm",
    ".webp",
}


def canonical_archive_path(raw: str) -> str:
    value = raw.strip().split("\t", 1)[0].replace("\\", "/")
    if ":" in value:
        value = value.split(":", 1)[1]
    return value.lstrip("/").lower()


def category(raw: str) -> str | None:
    path = canonical_archive_path(raw)
    if path.startswith("data/"):
        path = path[5:]
    if any(path.startswith(root) for root in CHARACTER_ROOTS):
        return "characters"
    suffix = Path(path).suffix.lower()
    if suffix in VISUAL_EXTENSIONS or any(part in f"/{path}" for part in VISUAL_ROOT_PARTS):
        return "visual"
    return None


def parse_listing(lines: list[str]) -> dict[str, list[str]]:
    selected: dict[str, list[str]] = {"characters": [], "visual": []}
    seen: set[str] = set()
    for line in lines:
        fields = line.strip().split("\t")
        archive_name = fields[0]
        if len(fields) >= 3 and fields[2] == "0":
            continue
        kind = category(archive_name)
        key = archive_name.replace("\\", "/").lower()
        if kind and key not in seen:
            selected[kind].append(archive_name)
            seen.add(key)
    for paths in selected.values():
        paths.sort(key=str.lower)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--listfile", type=Path)
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    list_command = [str(root / "build/list_casc"), str(args.storage), "*"]
    if args.listfile:
        list_command.append(str(args.listfile))
    listing = subprocess.run(list_command, check=True, text=True, capture_output=True)
    selected = parse_listing(listing.stdout.splitlines())
    manifest_dir = args.output / "_manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    combined: list[str] = []
    for kind, paths in selected.items():
        (manifest_dir / f"{kind}.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")
        combined.extend(paths)
    combined.sort(key=str.lower)
    all_manifest = manifest_dir / "all_visual_and_characters.txt"
    all_manifest.write_text("\n".join(combined) + "\n", encoding="utf-8")
    report = {
        "characters": len(selected["characters"]),
        "visual": len(selected["visual"]),
        "total": len(combined),
        "scope": "locally_available",
    }
    (manifest_dir / "export_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    if not args.manifest_only:
        subprocess.run([str(root / "build/extract_d2r"), str(args.storage), str(args.output), str(all_manifest)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
