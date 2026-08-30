#!/usr/bin/env python3
"""Validate Mixamo catalog manifests and completed render jobs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

from mixamo_catalog import read_catalog, validate_catalog


def validate_outputs(catalog: dict) -> list[str]:
    errors: list[str] = []
    output_root = Path(catalog["output_root"])
    for job in catalog["jobs"]:
        if job.get("status") not in {"done", "skipped"}:
            continue
        job_dir = output_root / job["output"]
        metadata = job_dir / "sheet_metadata.json"
        sheet = job_dir / f"sheet_{job['render']['directions']}x{job['render']['phases']}.png"
        if not metadata.is_file():
            errors.append(f"missing sheet metadata: {metadata}")
            continue
        if not sheet.is_file():
            errors.append(f"missing sheet: {sheet}")
        try:
            data = json.loads(metadata.read_text())
            if data.get("rows") != job["render"]["directions"]:
                errors.append(f"row count mismatch: {job['id']}")
            if data.get("columns") != job["render"]["phases"]:
                errors.append(f"phase count mismatch: {job['id']}")
            if data.get("transparent_background") is not True:
                errors.append(f"sheet is not marked transparent: {job['id']}")
            expected_size = (data["columns"] * data["cell"][0], data["rows"] * data["cell"][1])
            with Image.open(sheet) as image:
                if image.mode != "RGBA":
                    errors.append(f"sheet is not RGBA: {job['id']}")
                if image.size != expected_size:
                    errors.append(f"sheet dimensions mismatch: {job['id']} {image.size} != {expected_size}")
            for direction, frame_paths in data.get("frames", {}).items():
                if len(frame_paths) != job["render"]["phases"]:
                    errors.append(f"frame count mismatch: {job['id']}:{direction}")
                for frame_path in frame_paths:
                    path = Path(frame_path)
                    if not path.is_absolute():
                        path = job_dir / path
                    if not path.is_file():
                        errors.append(f"missing frame: {path}")
                        continue
                    with Image.open(path) as image:
                        if image.mode != "RGBA" or image.size != tuple(data["cell"]):
                            errors.append(f"invalid frame contract: {path}")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"invalid sheet metadata {metadata}: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Mixamo catalog")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--no-sources", action="store_true")
    parser.add_argument("--outputs", action="store_true")
    args = parser.parse_args()
    catalog = read_catalog(args.manifest)
    errors = validate_catalog(catalog, check_sources=not args.no_sources)
    if args.outputs:
        errors.extend(validate_outputs(catalog))
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 1
    print(f"MIXAMO_CATALOG_VALID ({len(catalog['jobs'])} jobs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
