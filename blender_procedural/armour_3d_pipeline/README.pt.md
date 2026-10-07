# armour_3d_pipeline

Um comando do GLB da Tripo até a armadura encaixada no corpo. Encadeia três ferramentas, cada
uma no seu processo e na sua pasta, e para na primeira que não entregar:

| Passo | Ferramenta | Pasta | Entrega |
|---|---|---|---|
| 1 | `armour_3d_split` | `1_split/` | peças separadas (`armour_split.blend`) |
| 2 | `armour_3d_decimate` | `2_decimate/` | peças no orçamento de triângulos (`armour_decimated.blend`) |
| 3 | `armour_3d_fit` | `3_fit/` | armadura no corpo, com skin, máscara e GLB |

Nenhum passo é reimplementado aqui: o comando valida o arquivo do asset, passa os caminhos
adiante e registra o que aconteceu em `pipeline.json`.

## Uso

```bash
cd /home/ggnp/tools/blender_procedural
python3 -m armour_3d_pipeline --asset armour_3d_pipeline/assets/medieval_boots.asset.json \
  --out outputs/armour_3d_pipeline/run_001
```

| Opção | Efeito |
|---|---|
| `--asset ARQ` | arquivo do asset (obrigatório) |
| `--out PASTA` | pasta do run: nova, ou a de um run anterior com `--resume` |
| `--style plate\|leather` | sobrescreve `fit.style` do asset |
| `--until split\|decimate\|fit` | último passo a rodar (padrão `fit`) |
| `--resume` | mantém os passos que já entregaram em `--out` e roda os demais |
| `--blender EXEC` | executável do Blender (padrão `blender`) |

Códigos de saída: `0` todos os passos entregaram; `1` o fit rodou e algum gate reprovou;
`2` erro de configuração ou um passo não produziu resultado.

O exemplo leva cerca de um minuto e meio.

## Arquivo do asset

```json
{
  "name": "medieval_boots",
  "source": "../../assets_models/medieval armor 3d model boots.glb",
  "split": {"plan": "../../armour_3d_split/examples/medieval_boots.plan.json", "normal_policy": "backup"},
  "decimate": {"budgets": "../../armour_3d_decimate/budgets.plate.json"},
  "fit": {
    "pieces": {"Helmet": "helmet", "Chest": "chest", "Legs": "skirt", "Glove_L": "glove.L",
               "Glove_R": "glove.R", "Boot_L": "boot.L", "Boot_R": "boot.R"},
    "body": {"rig": "/caminho/host_geometry.json", "poses": "/caminho/host_poses.json"},
    "body_profile": "../../armour_3d_fit/profiles/body.kcc_makehuman_52.json",
    "slot_profile": "../../armour_3d_fit/profiles/slots.plate_catalogue.json",
    "style": "plate",
    "config": {}
  }
}
```

Caminhos relativos valem a partir da pasta do arquivo. `split.normal_policy` (padrão
`strict`), `fit.style` (padrão `plate`), `fit.config` e `fit.body.poses` são opcionais.

As peças precisam ter os mesmos nomes nos três lugares: `parts` do plano do split, `budgets`
da decimação e `fit.pieces`. O comando recusa o asset antes de abrir o Blender se diferirem.

## Asset novo

O plano do split é o único passo manual, porque depende de reconhecer as peças:

```bash
python3 -m armour_3d_split inspect --input NOVO.glb --out outputs/armour_3d_split/novo_inspect
```

1. Abra `inventory.json`, identifique as ilhas pelo tamanho e pela bounding box e escreva o
   plano a partir de `plan.template.json` (ver `armour_3d_split/README.pt.md`).
2. Copie um arquivo de `assets/`, aponte `source` e `split.plan` e ajuste `fit.pieces`.
3. Rode o comando. Se as peças tiverem outra anatomia (calça em vez de saiote, mangas longas),
   crie um catálogo de slots novo em `armour_3d_fit/profiles/`.

## Retomar

Cada passo só é refeito se não entregou ou se um passo anterior foi refeito. A pasta de um
passo refeito não é apagada: vira `<pasta>_superseded_N`.

```bash
python3 -m armour_3d_pipeline --asset ARQ --out outputs/armour_3d_pipeline/run_001 --resume --style leather
```

Mudar só o estilo não refaz split nem decimação, mas o fit já entregue também é mantido se
tiver passado. Para forçar outro fit, use uma pasta de saída nova ou renomeie `3_fit/`.

## Saídas

`pipeline.json` (status por passo, tempos, gates do fit e caminhos das entregas), um `.log`
por passo, `fit.job.json` (o job gerado para o fit) e as três pastas dos passos.

## Testes

```bash
python3 -m unittest discover -s armour_3d_pipeline/tests -p 'test_*.py' -v
```

Cobrem a validação do arquivo do asset. O encadeamento é verificado rodando o exemplo.
