"""Turn an approved 2048px Gemini sheet into the four Sprite Lab variants."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import huggingface_realesrgan
import sprite_render


MASK_CACHE_ROOT = Path(__file__).resolve().parent / "work" / "mask-cache"


def _mask_cache_key(generated_sheet: Path, rows: int, phases: int) -> str:
    digest = hashlib.sha256()
    with generated_sheet.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    digest.update(f"rows={rows};phases={phases};mask_model=anime_x4plus_6b;threshold=0.50;input=1024".encode())
    return digest.hexdigest()[:32]


def _load_cached_mask_pass(mask_pass: Path, cache_key: str, rows: int, phases: int) -> dict[str, Any] | None:
    cache_dir = MASK_CACHE_ROOT / cache_key
    cached_masks = cache_dir / "foreground_cleanup_masks"
    cached_report = cache_dir / "mask_report.json"
    expected = rows * phases
    if not cached_report.is_file() or len(list(cached_masks.glob("row*_col*.png"))) != expected:
        local_masks = mask_pass / "foreground_cleanup_masks"
        local_metadata = mask_pass / "render_metadata.json"
        if local_metadata.is_file() and len(list(local_masks.glob("row*_col*.png"))) == expected:
            return {
                "schema": "sprite_lab.realesrgan_birefnet/v1",
                "resumed_from_local_output": True,
                "source_metadata": str(local_metadata),
            }
        return None
    mask_pass.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cached_masks, mask_pass / "foreground_cleanup_masks", dirs_exist_ok=True)
    return json.loads(cached_report.read_text(encoding="utf-8"))


def _save_cached_mask_pass(mask_report: dict[str, Any], mask_pass: Path, cache_key: str) -> None:
    cached_dir = MASK_CACHE_ROOT / cache_key
    temporary = MASK_CACHE_ROOT / f".{cache_key}.tmp"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        mask_pass / "foreground_cleanup_masks",
        temporary / "foreground_cleanup_masks",
        dirs_exist_ok=True,
    )
    (temporary / "mask_report.json").write_text(
        json.dumps(mask_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if cached_dir.exists():
        shutil.rmtree(cached_dir)
    temporary.replace(cached_dir)


def _run(command: list[str], label: str) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} falhou:\n{completed.stdout}\n{completed.stderr}")
    lines = completed.stdout.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip().startswith("{"))
        return json.loads("\n".join(lines[start:]))
    except (StopIteration, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} não produziu JSON:\n{completed.stdout}") from error


def _build_ordered_gif(output: Path, rows: int, phases: int, fps: float) -> Path:
    direction_order = tuple(
        index + 1
        for index, _row_id in enumerate(sprite_render.DIRECTION_ROWS)
        if index < rows
    )
    frame_paths = [
        output / f"row{direction - 1}_col{phase}.png"
        for direction in direction_order
        for phase in range(phases)
    ]
    if not all(path.is_file() for path in frame_paths):
        raise RuntimeError("frames insuficientes para o GIF unificado original")
    return sprite_render._write_gif(
        frame_paths,
        output / "animation_all_directions_1-2-5-4-3-8-7-6.gif",
        fps,
    )


def process(
    generated_sheet: Path,
    structural_dir: Path,
    output: Path,
    *,
    rows: int = 8,
    phases: int = 8,
    source_cell: int = 256,
    fps: float = 10.0,
    foot_anchor: tuple[int, int] = (128, 220),
    realesrgan_repo: Path,
    python_executable: str,
    model_profile: str = "anime_x4plus_6b",
    progress_callback: Callable[[str, int, int | None], None] | None = None,
) -> dict[str, Any]:
    if not generated_sheet.is_file() or not structural_dir.is_dir():
        raise FileNotFoundError("generated_sheet ou structural_dir ausente")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    mask_pass = output / "mask_pass_realesrgan_birefnet"
    mask_cache_key = _mask_cache_key(generated_sheet, rows, phases)
    if progress_callback:
        progress_callback("mask_pass_realesrgan", 8, 1800)
    mask_report = _load_cached_mask_pass(mask_pass, mask_cache_key, rows, phases)
    mask_cache_hit = mask_report is not None
    if mask_report is None:
        mask_report = _run(
            [
            python_executable,
            str(Path(__file__).with_name("realesrgan_birefnet_pipeline.py")),
            str(generated_sheet),
            str(mask_pass),
            "--rows",
            str(rows),
            "--phases",
            str(phases),
            "--fps",
            str(fps),
            "--realesrgan-python",
            python_executable,
            "--realesrgan-repo",
            str(realesrgan_repo),
            "--model-profile",
            "anime_x4plus_6b",
            "--birefnet-python",
            python_executable,
            "--birefnet-threshold",
            "0.50",
            "--birefnet-input-size",
            "1024",
            "--realesrgan-tile-size",
            "256",
            "--realesrgan-tile-pad",
            "32",
            "--foot-anchor",
            str(foot_anchor[0]),
            str(foot_anchor[1]),
            "--chroma-cleanup",
            "foreground",
            "--chroma-edge-radius",
            "6",
            "--chroma-tolerance",
            "2",
            "--chroma-strength",
            "1",
            "--chroma-bleed-radius",
            "8",
            "--chroma-key-distance",
            "96",
            "--chroma-max-island-size",
            "2048",
            ],
            "passe de máscara Real-ESRGAN + BiRefNet-Lite",
        )
        MASK_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        _save_cached_mask_pass(mask_report, mask_pass, mask_cache_key)
    mask_report = {**mask_report, "cache": {"hit": mask_cache_hit, "key": mask_cache_key}}
    mask_source = mask_pass / "foreground_cleanup_masks"
    expected_masks = list(mask_source.glob("row*_col*.png"))
    if len(expected_masks) != rows * phases:
        raise RuntimeError(
            f"passe BiRefNet produziu {len(expected_masks)} de {rows * phases} máscaras"
        )

    official = output / "variants" / "original"
    if progress_callback:
        progress_callback("quality_pass_realesrgan", 74, 360)
    pregan_command = [
            python_executable,
            str(Path(__file__).with_name("pregan_realesrgan_reuse_mask_pipeline.py")),
            str(generated_sheet),
            str(mask_source),
            str(official),
            "--realesrgan-repo",
            str(realesrgan_repo),
            "--model-profile",
            model_profile,
            "--rows",
            str(rows),
            "--phases",
            str(phases),
            "--fps",
            str(fps),
            "--tile-size",
            "256",
            "--tile-pad",
            "32",
            "--tolerance",
            "2",
            "--final-bleed-radius",
            "8",
            "--foot-anchor",
            str(foot_anchor[0]),
            str(foot_anchor[1]),
        ]
    official_report = _run(
        pregan_command,
        "pipeline original",
    )
    _build_ordered_gif(official, rows, phases, fps)
    if progress_callback:
        progress_callback("building_color_variants", 90, 240)

    variants = {"original": official}
    refine_script = Path(__file__).with_name("temporal_palette_refine.py")
    for name, colors in (("frame_adjustment", 0), ("color_cohesion_256", 256), ("color_cohesion_128", 128)):
        variant = output / "variants" / name
        command = [
            python_executable,
            str(refine_script),
            str(official),
            str(variant),
            "--rows",
            str(rows),
            "--phases",
            str(phases),
            "--fps",
            str(fps),
            "--outlier-distance",
            "3.25",
            "--max-shift",
            "1.5",
            "--bleed-radius",
            "8",
        ]
        if colors:
            command.extend(["--colors", str(colors)])
        _run(command, f"variante {name}")
        variants[name] = variant
        if progress_callback:
            completed_percent = {"frame_adjustment": 93, "color_cohesion_256": 96, "color_cohesion_128": 99}[name]
            progress_callback("building_color_variants", completed_percent, max(0, 180 - completed_percent))

    for name, variant in variants.items():
        if name == "original":
            continue
        # Ensure each variant exposes the same deterministic deliverables.
        ordered = variant / "animation_all_directions_1-2-5-4-3-8-7-6.gif"
        if not ordered.is_file():
            raise RuntimeError(f"GIF unificado ausente na variante {name}")
    metadata = {
        "schema": "sprite_lab.gemini_sprite_postprocess/v1",
        "generated_sheet": str(generated_sheet.resolve()),
        "structural_dir": str(structural_dir.resolve()),
        "rows": rows,
        "phases": phases,
        "source_cell": source_cell,
        "output_cell": source_cell * 2,
        "fps": fps,
        "foot_anchor": [foot_anchor[0] * 2, foot_anchor[1] * 2],
        "direction_contract": {
            "row_order": [
                {"row": index, "id": sprite_render.DIRECTION_LABELS[row_id], "row_id": row_id}
                for index, row_id in enumerate(sprite_render.DIRECTION_ROWS[:rows])
            ],
            "gif_order": [
                sprite_render.DIRECTION_LABELS[row_id]
                for row_id in sprite_render.GIF_DIRECTION_ORDER
                if row_id in sprite_render.DIRECTION_ROWS[:rows]
            ],
            "gif_starts_with": sprite_render.DIRECTION_LABELS[sprite_render.DIRECTION_ROWS[0]],
            "frame_order": "columns_0_to_phases_minus_1",
        },
        "variants": {
            name: f"variants/{name}" for name in variants
        },
        "pipeline": [
            "realesrgan_2x_mask_pass_cpu",
            "birefnet_lite_512_binary_threshold_0.5",
            "foreground_chroma_cleanup_and_island_removal",
            "approved_birefnet_mask_512",
            "pregan_chroma_cleanup",
            "realesrgan_2x_quality_pass_cpu",
            "reapply_exact_approved_birefnet_mask",
            "four_output_variants",
        ],
        "structural_channels": ["beauty", "bones", "lineart"],
        "structural_postprocess": "validation_only_no_scale_transform",
        "mask_report": mask_report,
        "official_report": official_report,
        "mask_model_profile": "anime_x4plus_6b",
        "model_profile": model_profile,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    (output / "render_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("generated_sheet", type=Path)
    parser.add_argument("structural_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--realesrgan-repo", type=Path, required=True)
    parser.add_argument(
        "--model-profile",
        choices=tuple(huggingface_realesrgan.MODEL_PROFILES),
        default="anime_x4plus_6b",
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--foot-anchor", type=int, nargs=2, default=(128, 220))
    args = parser.parse_args()
    report = process(
        args.generated_sheet,
        args.structural_dir,
        args.output,
        fps=args.fps,
        foot_anchor=tuple(args.foot_anchor),
        realesrgan_repo=args.realesrgan_repo,
        python_executable=args.python,
        model_profile=args.model_profile,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
