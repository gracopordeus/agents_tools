"""Measured local x2 SR comparison: same inputs, warmup and synchronized timing."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np
from PIL import Image, ImageDraw
import torch
from upscale_backend import Upscaler


def metrics(predicted, reference):
    a, b = predicted.astype(np.float64), reference.astype(np.float64)
    error = (a-b)**2
    foreground = reference.max(axis=2) > 32
    def psnr(mse):
        return float(10*math.log10(255**2/max(float(mse), 1e-12)))
    # RGB SSIM, 11x11 Gaussian, sigma 1.5, valid interior (not a perceptual score).
    mu_a = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(b, (11, 11), 1.5)
    va = cv2.GaussianBlur(a*a, (11, 11), 1.5)-mu_a*mu_a
    vb = cv2.GaussianBlur(b*b, (11, 11), 1.5)-mu_b*mu_b
    cov = cv2.GaussianBlur(a*b, (11, 11), 1.5)-mu_a*mu_b
    ssim = ((2*mu_a*mu_b+6.5025)*(2*cov+58.5225))/((mu_a**2+mu_b**2+6.5025)*(va+vb+58.5225))
    return {"psnr_rgb_db": psnr(error.mean()), "psnr_foreground_db": psnr(error[foreground].mean()),
            "ssim_rgb": float(ssim[5:-5, 5:-5].mean())}


def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(4)
    torch.manual_seed(42)
    torch.backends.cudnn.benchmark = False
    source_root = ROOT / "work/sprite-renders/sprite_85f38c1f82f44451"
    sources = [
        source_root / "row4_col4.png",
        source_root / "row4_col0.png",
        source_root / "row3_col4.png",
        source_root / "row5_col4.png",
    ]
    refs, inputs, provenance = [], [], []
    for i, path in enumerate(sources):
        reference = Image.open(path).convert("RGB").resize((512, 512), Image.Resampling.LANCZOS)
        low = reference.resize((256, 256), Image.Resampling.BICUBIC)
        reference.save(args.output / f"reference_{i}.png")
        low.save(args.output / f"input_{i}.png")
        refs.append(np.asarray(reference))
        inputs.append(np.asarray(low)[:, :, ::-1].copy())
        provenance.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    profiles = [("anime_x4plus_6b", "cpu", "fp32"), ("anime_x4plus_6b", "cuda", "fp32"),
                ("swinir_light_x2", "cuda", "fp32"), ("swinir_m_classical_df2k_x2", "cuda", "fp32"), ("realcugan_x2", "cuda", "fp32"),
                ("realcugan_conservative_x2", "cuda", "fp32")]
    report = {"gpu": torch.cuda.get_device_name(), "torch": torch.__version__, "cpu_threads": 4,
              "input_size": 256, "output_size": 512, "repeats": args.repeats, "inputs": provenance,
              "method": "Synthetic bicubic downsample 512->256, RGB reconstruction; not ground truth of invented detail",
              "timing_scope": "warm inference + H2D/D2H + resize, excluding model loading and PNG I/O",
              "results": []}
    panel = Image.new("RGB", (512*(len(profiles)+1), 4*544), "#252525")
    draw = ImageDraw.Draw(panel)
    for i, ref in enumerate(refs):
        panel.paste(Image.fromarray(ref), (0, i*544+32))
        draw.text((8, i*544+8), "Reference 512px (synthetic benchmark)", fill="white")
    for column, (profile, device, precision) in enumerate(profiles, 1):
        print(f"Loading {profile} {device} {precision}", flush=True)
        started = time.perf_counter()
        model = Upscaler(profile, device=device, precision=precision)
        load_seconds = time.perf_counter()-started
        model.enhance(inputs[0])  # excluded warmup, same size
        if device == "cuda":
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
        times, scores, first_outputs = [], [], []
        name = f"{profile}_{device}_{precision}"
        destination = args.output / name
        destination.mkdir()
        for repeat in range(args.repeats):
            for i, bgr in enumerate(inputs):
                if device == "cuda":
                    torch.cuda.synchronize()
                start = time.perf_counter()
                result = model.enhance(bgr)[0][:, :, ::-1].copy()
                if device == "cuda":
                    torch.cuda.synchronize()
                elapsed = time.perf_counter()-start
                times.append(elapsed)
                if repeat == 0:
                    first_outputs.append(result)
                    scores.append(metrics(result, refs[i]))
                    Image.fromarray(result).save(destination / f"row0_col{i}.png")
                    panel.paste(Image.fromarray(result), (column*512, i*544+32))
                    draw.text((column*512+8, i*544+8), name, fill="white")
                else:
                    if not np.array_equal(first_outputs[i], result):
                        raise RuntimeError(f"Non-repeatable output: {name} input {i}")
                print(f"{name} repeat={repeat} image={i} seconds={elapsed:.3f}", flush=True)
        row = {"profile": profile, "device": device, "precision": precision,
               "median_seconds": statistics.median(times), "mean_seconds": statistics.mean(times),
               "times_seconds": times, "load_seconds": load_seconds,
               "peak_vram_bytes": torch.cuda.max_memory_allocated() if device == "cuda" else 0,
               "weight_sha256": model.sha256, "scores": scores,
               **{key: statistics.mean(s[key] for s in scores) for key in scores[0]},
               "repeat_outputs_identical": True}
        report["results"].append(row)
        baseline = report["results"][0]["median_seconds"]
        gpu_baseline = report["results"][1]["median_seconds"] if len(report["results"]) > 1 else baseline
        for item in report["results"]:
            item["speedup_vs_esrgan_cpu"] = baseline/item["median_seconds"]
            item["speedup_vs_esrgan_gpu"] = gpu_baseline/item["median_seconds"]
        (args.output / "results.json").write_text(json.dumps(report, indent=2)+"\n")
        del model
        gc.collect()
        torch.cuda.empty_cache()
    panel.save(args.output / "comparison.png")
    lines = ["# POC GPU — resultados medidos", "", "256→512 px; quatro imagens × três repetições após aquecimento. FP32; CPU com quatro threads.", "",
             "| Modelo | Dispositivo | s/imagem (mediana) | Ganho vs ESRGAN CPU | Ganho vs ESRGAN GPU | VRAM alocada MiB | PSNR RGB dB | PSNR foreground dB | SSIM RGB |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in report["results"]:
        lines.append(f'| {r["profile"]} | {r["device"]} | {r["median_seconds"]:.3f} | {r["speedup_vs_esrgan_cpu"]:.2f}× | {r["speedup_vs_esrgan_gpu"]:.2f}× | {r["peak_vram_bytes"]/1048576:.0f} | {r["psnr_rgb_db"]:.2f} | {r["psnr_foreground_db"]:.2f} | {r["ssim_rgb"]:.4f} |')
    lines += ["", "Tempos incluem inferência, transferências CPU/GPU e resize final; excluem carga dos pesos, aquecimento, PNG, máscaras e limpeza de bordas.", "",
              "PSNR/SSIM medem reconstrução de imagens artificialmente reduzidas por bicubic. Não medem criação de detalhes corretos em sprites reais nem estabilidade temporal. Comparação usa exatamente as mesmas referências. Foreground: pixels da referência com max(R,G,B)>32.", "",
              "Todos os resultados repetidos são comparados pixel a pixel. A baseline CPU usa o checkpoint ESRGAN atual carregado pelo mesmo backend Spandrel, não um tempo histórico de outro ambiente."]
    (args.output / "RESULTS.md").write_text("\n".join(lines)+"\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    run(parser.parse_args())
