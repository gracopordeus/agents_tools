"""Build one horizontal spritesheet for each conditioning channel."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

import conditioning_schema as schema


def build_sheets(manifest_path: Path, output_dir: Path) -> dict[str, Path]:
    manifest = schema.load_manifest(manifest_path)
    root = manifest_path.parent
    width, height = manifest["cell_size"]
    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    for channel in ("beauty", "silhouette", "segmentation"):
        if channel not in manifest["channels"]:
            continue
        sheet = Image.new("RGBA", (width * manifest["frame_count"], height), (0, 0, 0, 0))
        for index, frame in enumerate(manifest["frames"]):
            source = root / frame["channels"][channel]
            if not source.is_file():
                sheet.close()
                raise FileNotFoundError(source)
            with Image.open(source) as image:
                cell = image.convert("RGBA")
                if cell.size != (width, height):
                    cell.close()
                    sheet.close()
                    raise ValueError(f"dimensão inválida em {source}")
                sheet.alpha_composite(cell, (index * width, 0))
                cell.close()
        destination = output_dir / f"spritesheet_{channel}.png"
        sheet.save(destination, format="PNG")
        sheet.close()
        results[channel] = destination
    (output_dir / "conditioning-sheets.json").write_text(
        json.dumps(
            {
                "schema": "generation.conditioning_sheets/v1",
                "manifest": str(manifest_path.resolve()),
                "cell_size": manifest["cell_size"],
                "frame_count": manifest["frame_count"],
                "sheets": {name: str(path.resolve()) for name, path in results.items()},
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    for channel, path in build_sheets(args.manifest, args.output_dir).items():
        print(f"{channel}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
