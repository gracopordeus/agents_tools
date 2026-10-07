"""End-to-end CLI checks, approved-alpha invariance and CPU/CUDA mask comparison."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    source = ROOT / "work/sprite-renders/sprite_85f38c1f82f44451"
    sheet = Image.new("RGB", (8*256, 256))
    for col in range(8):
        with Image.open(source / f"row4_col{col}.png") as frame:
            sheet.paste(frame.convert("RGB"), (col*256, 0))
    sheet_path = args.output / "source.png"
    sheet.save(sheet_path)
    records = {}
    def execute(name, command):
        started = time.perf_counter()
        result = subprocess.run([sys.executable, *map(str, command)], cwd=ROOT,
                                env={**os.environ, "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4"},
                                capture_output=True, text=True)
        (args.output / f"{name}.log").write_text(result.stdout+"\n"+result.stderr)
        if result.returncode:
            raise RuntimeError(f"{name} failed; see {args.output / (name+'.log')}")
        lines = result.stdout.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("{"))
        parsed = json.loads("\n".join(lines[start:]))
        records[name] = {"wall_seconds": time.perf_counter()-started, "report": parsed}
        (args.output / "validation.json").write_text(json.dumps(records, indent=2)+"\n")
        print(f"{name}: {records[name]['wall_seconds']:.2f}s", flush=True)

    common = ["--rows", "1", "--phases", "8", "--fps", str(1/0.2375)]
    mask_pass = args.output / "mask_pass"
    execute("mask_pass_gpu", [ROOT / "realesrgan_birefnet_pipeline.py", sheet_path, mask_pass,
            *common, "--realesrgan-python", sys.executable, "--birefnet-python", sys.executable,
            "--realesrgan-repo", ROOT / "work/Real-ESRGAN", "--device", "cuda", "--chroma-cleanup", "auto"])
    for profile in ("anime_x4plus_6b", "swinir_light_x2", "realcugan_x2", "realcugan_conservative_x2"):
        target = args.output / profile
        execute(profile, [ROOT / "pregan_realesrgan_reuse_mask_pipeline.py", sheet_path,
                mask_pass / "foreground_cleanup_masks", target, *common,
                "--realesrgan-repo", ROOT / "work/Real-ESRGAN", "--model-profile", profile, "--device", "cuda"])
        for col in range(8):
            name = f"row0_col{col}.png"
            expected = np.asarray(Image.open(mask_pass / "foreground_cleanup_masks" / name))
            actual = Image.open(target / name)
            assert actual.size == (512, 512)
            assert np.array_equal(np.asarray(actual.getchannel("A")), expected), f"Alpha changed: {profile} {name}"
        records[profile]["exact_approved_alpha"] = True
    cpu = args.output / "mask_cpu"
    execute("birefnet_cpu", [ROOT / "birefnet_lite_remove.py", mask_pass / "realesrgan_512", cpu,
            "--mask-output", cpu / "masks", "--device", "cpu"])
    gpu = args.output / "mask_gpu_repeat"
    execute("birefnet_gpu", [ROOT / "birefnet_lite_remove.py", mask_pass / "realesrgan_512", gpu,
            "--mask-output", gpu / "masks", "--device", "cuda"])
    intersections, unions, differences, repeat_diffs = 0, 0, 0, 0
    for col in range(8):
        name = f"row0_col{col}.png"
        a = np.asarray(Image.open(cpu / "masks" / name)) > 0
        b = np.asarray(Image.open(gpu / "masks" / name)) > 0
        original = np.asarray(Image.open(mask_pass / "birefnet_masks" / name)) > 0
        intersections += int((a & b).sum())
        unions += int((a | b).sum())
        differences += int((a != b).sum())
        repeat_diffs += int((b != original).sum())
    records["mask_comparison"] = {"cpu_gpu_iou": intersections/max(unions, 1),
            "cpu_gpu_different_pixels": differences, "gpu_repeat_different_pixels": repeat_diffs,
            "pixels": 8*512*512}
    records["status"] = "passed"
    (args.output / "validation.json").write_text(json.dumps(records, indent=2)+"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    run(args)
