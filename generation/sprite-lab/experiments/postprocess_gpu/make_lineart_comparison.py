"""Create a visual before/lineart/after comparison for the local POC."""
from pathlib import Path
from PIL import Image, ImageDraw
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pregan_realesrgan_reuse_mask_pipeline import apply_lineart

source = ROOT / "work/postprocess-benchmark/swinir-m-df2k-e2e-v1"
lineart_root = ROOT / "work/sprite-renders/sprite_337ef428f95045c8/lineart"
output = ROOT / "work/postprocess-benchmark/swinir-m-lineart-v1"
output.mkdir(parents=True, exist_ok=False)
size = (384, 384)
canvas = Image.new("RGB", (size[0] * 3, (size[1] + 28) * 8), "#171717")
draw = ImageDraw.Draw(canvas)
for col in range(8):
    name = f"row0_col{col}.png"
    with Image.open(source / name) as opened:
        before = opened.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    with Image.open(lineart_root / f"row4_col{col}.png") as opened:
        guide = opened.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    after = apply_lineart(before, guide, 0.85)
    line_preview = Image.new("RGB", size, "#555")
    line_preview.paste((0, 0, 0), mask=guide.getchannel("A"))
    y = col * (size[1] + 28)
    canvas.paste(before.convert("RGB"), (0, y + 28))
    canvas.paste(line_preview, (size[0], y + 28))
    canvas.paste(after.convert("RGB"), (size[0] * 2, y + 28))
    draw.text((6, y + 7), f"coluna {col} — antes", fill="white")
    draw.text((size[0] + 6, y + 7), "lineart", fill="white")
    draw.text((size[0] * 2 + 6, y + 7), "depois", fill="white")
    before.save(output / f"before_col{col}.png")
    line_preview.save(output / f"lineart_col{col}.png")
    after.save(output / f"after_col{col}.png")
    before.close(); guide.close(); after.close(); line_preview.close()
canvas.save(output / "comparison.png")
(output / "README.md").write_text(
    "# Comparativo DF2K + lineart\n\n"
    "Antes: saída do SwinIR-M DF2K. Meio: alpha do lineart Blender. Depois: overlay preto com força 0,85, limitado ao alpha aprovado.\n"
    "\nA fonte desta comparação é um render estrutural local do Blender; ela valida o mecanismo do overlay, não a qualidade de uma geração de imagem.\n"
)
print(output / "comparison.png")
