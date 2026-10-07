"""Create a clean, single-frame DF2K + lineart comparison."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pregan_realesrgan_reuse_mask_pipeline import apply_lineart  # noqa: E402
from upscale_backend import Upscaler  # noqa: E402


SOURCE = ROOT / "work/sprite-renders/sprite_337ef428f95045c8/row4_col4.png"
LINEART = ROOT / "work/sprite-renders/sprite_337ef428f95045c8/lineart/row4_col4.png"
OUTPUT = ROOT / "work/postprocess-benchmark/swinir-m-lineart-clean-v2"


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"output já existe: {OUTPUT}")
    OUTPUT.mkdir(parents=True)

    with Image.open(SOURCE) as opened:
        high_source = opened.convert("RGB")
    input_256 = high_source.resize((256, 256), Image.Resampling.LANCZOS).convert("RGBA")
    input_256.putalpha(255)
    input_256.save(OUTPUT / "input_256.png")

    rgb = np.asarray(input_256.convert("RGB"), dtype=np.uint8)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    started = time.monotonic()
    upscaler = Upscaler(
        "swinir_m_classical_df2k_x2",
        tile_size=256,
        tile_pad=32,
        device="cuda",
        precision="fp32",
    )
    output_bgr, _ = upscaler.enhance(bgr, outscale=2.0)
    output_rgb = cv2.cvtColor(output_bgr, cv2.COLOR_BGR2RGB)
    without_lineart = Image.fromarray(output_rgb, mode="RGB").convert("RGBA")
    without_lineart.putalpha(255)
    without_lineart.save(OUTPUT / "df2k_without_lineart.png")

    with Image.open(LINEART) as opened:
        guide = opened.convert("RGBA").resize((512, 512), Image.Resampling.LANCZOS)
    guide_preview = Image.new("RGB", (512, 512), "#555555")
    guide_preview.paste((0, 0, 0), mask=guide.getchannel("A"))
    guide_preview.save(OUTPUT / "lineart_guide.png")
    with_lineart = apply_lineart(without_lineart, guide, 0.85)
    with_lineart.save(OUTPUT / "df2k_with_lineart.png")

    # A four-column layout makes the input scale and the lineart contribution explicit.
    panel = (384, 384)
    header = 32
    canvas = Image.new("RGB", ((panel[0] * 4), (panel[1] + header)), "#171717")
    draw = ImageDraw.Draw(canvas)
    entries = [
        ("input 256", input_256),
        ("DF2K 512", without_lineart),
        ("lineart", guide_preview),
        ("DF2K + lineart", with_lineart),
    ]
    for index, (label, image) in enumerate(entries):
        draw.text((index * panel[0] + 6, 9), label, fill="white")
        preview = image.convert("RGB").resize(panel, Image.Resampling.NEAREST if index == 0 else Image.Resampling.LANCZOS)
        canvas.paste(preview, (index * panel[0], header))
        preview.close()
    canvas.save(OUTPUT / "comparison.png")

    elapsed = time.monotonic() - started
    metadata = {
        "schema": "sprite_lab.swinir_lineart_clean_comparison/v2",
        "source": str(SOURCE.resolve()),
        "source_original_size": list(high_source.size),
        "input_size": [256, 256],
        "output_size": [512, 512],
        "lineart_source": str(LINEART.resolve()),
        "model_profile": "swinir_m_classical_df2k_x2",
        "device": "cuda",
        "precision": "fp32",
        "lineart_strength": 0.85,
        "lineart_composite": "black_overlay_on_opaque_comparison",
        "elapsed_seconds": round(elapsed, 3),
        "weight_sha256": upscaler.sha256,
    }
    (OUTPUT / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (OUTPUT / "README.md").write_text(
        "# Comparativo limpo: SwinIR-M DF2K + lineart\n\n"
        "Fonte: um único frame exportado pelo Blender, sem deriva temporal de sequência. "
        "Ele foi reduzido para 256×256 e ampliado para 512×512 com SwinIR-M ClassicalSR DF2K x2.\n\n"
        "A coluna `lineart` é o lineart estrutural do mesmo frame Blender. O overlay preto usa força 0,85.\n",
        encoding="utf-8",
    )
    print(OUTPUT / "comparison.png")


if __name__ == "__main__":
    main()
