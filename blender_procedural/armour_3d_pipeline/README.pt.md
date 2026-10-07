# armour_3d_pipeline

Um comando do GLB da Tripo até a armadura encaixada no corpo, com veredito por etapa. Encadeia as
ferramentas, cada uma no seu processo e na sua pasta, e para na primeira que não entregar:

| Passo | Ferramenta | Pasta | Entrega |
|---|---|---|---|
| 1 | `armour_3d_split` | `1_split/` | peças separadas (`armour_split.blend`) |
| 2 | `armour_3d_decimate` | `2_decimate/` | pares e metades espelhados, tampas afundadas, peças no orçamento |
| 3 | `armour_3d_fit` | `3_fit/` | armadura no corpo, com skin, máscara e GLB por asset |
| 4 | `armour_3d_gates` | `4_gates/` | veredito por etapa (`gates.json`) |
| 5 | `--game` | `5_game/` | peças instaladas no projeto Godot e auditoria dele |

Nenhum passo é reimplementado aqui: o comando valida o arquivo do asset, passa os caminhos adiante
e registra o que aconteceu em `pipeline.json`. **O veredito do run é o dos gates por etapa.** O fit
que reprova nos seus próprios gates ainda entrega uma armadura para medir.

## Uso

```bash
cd /home/ggnp/tools/blender_procedural
python3 -m armour_3d_pipeline --asset armour_3d_pipeline/assets/medieval_knight_dedos.asset.json \
  --out outputs/armour_3d_pipeline/run_001 --autofix 3
```

| Opção | Efeito |
|---|---|
| `--asset ARQ` | arquivo do asset (obrigatório) |
| `--out PASTA` | pasta do run: nova, ou a de um run anterior com `--resume` |
| `--style plate\|leather` | sobrescreve `fit.style` do asset |
| `--until split\|decimate\|fit\|gates` | último passo a rodar (padrão `gates`) |
| `--resume` | mantém os passos que já entregaram em `--out` e roda os demais |
| `--autofix N` | responde aos gates reprovados com os remédios deles e roda de novo, até N vezes |
| `--game` | com todos os gates em PASS, instala as peças no projeto do jogo e roda a auditoria |
| `--blender EXEC`, `--godot EXEC` | executáveis (padrão `blender`, `godot`) |

Códigos de saída: `0` todos os gates passaram (e a auditoria do jogo, com `--game`); `1` algum gate
reprovou; `2` erro de configuração ou um passo não produziu resultado.

O asset de referência leva cerca de dois minutos.

**`--autofix`** não muda arquivo do repositório. Cada tentativa grava em `overrides/attempt_k/` os
remendos (`patches.json`) e as cópias remendadas do perfil de slots e do orçamento, e roda de novo a
partir do passo afetado; a pasta do passo anterior vira `<passo>_superseded_k`. Quando os gates
passam assim, `pipeline.json` → `promote` diz onde estão os perfis que passaram.

**`--game`** guarda antes as peças que estavam no projeto em `5_game/previous_pieces/`. A auditoria
passar não é a aprovação: a armadura é olhada com o jogo rodando e o usuário dá o aceite.

## Arquivo do asset

```json
{
  "name": "medieval_knight_dedos",
  "source": "../../../../3d_assets/medieval knight armor 3d model dedos.glb",
  "split": {"plan": "auto", "normal_policy": "backup"},
  "decimate": {"budgets": "../../armour_3d_decimate/budgets.plate_arpg.json"},
  "fit": {
    "pieces": {"Helmet": "helmet", "Suit": "suit", "Glove_L": "glove.L", "Glove_R": "glove.R",
               "Boot_L": "boot.L", "Boot_R": "boot.R"},
    "body": {"rig": "../../armour_3d_fit/fixtures/kcc_makehuman_52/host_geometry.json",
             "poses": "../../armour_3d_fit/fixtures/kcc_makehuman_52/host_poses.json"},
    "body_profile": "../../armour_3d_fit/profiles/body.kcc_makehuman_52.json",
    "slot_profile": "../../armour_3d_fit/profiles/slots.plate_assembled.json",
    "style": "plate",
    "config": {}
  },
  "gates": {"limits": "../../armour_3d_gates/gates.plate.json"},
  "game": {"project": "../../../../Games/OpenKCC-Locomotion", "pieces_dir": "assets/medieval_knight/pieces",
           "audit": "tests/medieval_knight_armour_audit.gd"}
}
```

Caminhos relativos valem a partir da pasta do arquivo. `split.normal_policy` (padrão `strict`),
`fit.style` (padrão `plate`), `fit.config`, `fit.body.poses`, `gates` (padrão `gates.plate.json`) e
`game` (só exigido com `--game`) são opcionais.

`split.plan` aceita `"auto"`: as peças de um conjunto montado de pé são nomeadas pela posição
(tronco = maior componente; elmo = o que cruza o meio do corpo acima dele; botas = as duas mais
baixas; manoplas = as outras duas; esquerda em `+X`). Se o conjunto não se lê assim, o split recusa
e o plano é escrito à mão depois de `armour_3d_split inspect`. Com `"auto"` os nomes são sempre
`Helmet`, `Suit`, `Glove_L`, `Glove_R`, `Boot_L`, `Boot_R`.

As peças precisam ter os mesmos nomes nos três lugares: plano do split, `budgets` da decimação e
`fit.pieces`. O comando recusa o asset antes de abrir o Blender se diferirem.

## O que pedir à Tripo

O encaixe sai melhor quando o asset já vem assim:

- armadura montada como uma figura de pé, de frente, braços caídos ao lado do corpo;
- malha em quads, já na contagem final de faces (reduzir depois destrói os quads);
- exportada em GLB;
- manoplas com a mão aberta e os cinco dedos separados um do outro até a base, do tamanho de
  uma mão proporcional ao corpo (dedos fundidos lado a lado abrem e fecham só em bloco);
- peças que não se tocam (elmo, tronco com coxas, manoplas, botas), para saírem como ilhas
  separadas no split.

## Asset novo

1. Copie um arquivo de `assets/`, aponte `source` e deixe `split.plan` em `"auto"`.
2. O catálogo de slots `slots.plate_assembled.json` espera a armadura montada como uma figura de pé,
   de frente para `-Y`. Peças lado a lado (layout de catálogo) não são atendidas.
3. Orçamento: `budgets.plate_arpg.json` para o jogo (triângulos); `budgets.plate_quads.json` para
   malha que já vem em quads.
4. Rode com `--autofix 3`. Leia os gates reprovados em `4_gates/gates.json`.
5. Se o split recusar o `"auto"` (sétima peça, fragmento entre duas peças, par do mesmo lado), rode
   `python3 -m armour_3d_split inspect --input NOVO.glb --out <pasta>`, escreva o plano a partir de
   `plan.template.json` e aponte `split.plan` para ele.
6. Peças com outra anatomia (capa, saia longa): catálogo de slots novo em `armour_3d_fit/profiles/`.

## Retomar

Cada passo só é refeito se não entregou ou se um passo anterior foi refeito. A pasta de um
passo refeito não é apagada: vira `<pasta>_superseded_N`.

```bash
python3 -m armour_3d_pipeline --asset ARQ --out outputs/armour_3d_pipeline/run_001 --resume --style leather
```

Mudar só o estilo não refaz split nem decimação, mas o fit já entregue também é mantido se
tiver passado. Para forçar outro fit, use uma pasta de saída nova ou renomeie `3_fit/`.

## Resultado nos assets de referência

| Asset | Run | Gates | Observação |
|---|---|---|---|
| `medieval_knight_dedos` | `dedos_052` | todos passam; pior pose 134 cm² | saída idêntica ao run aprovado `dedos_050`; auditoria do jogo PASS |
| `medieval_knight` | `knight_024` | **reprova** nas etapas 3, 6 e 7 | metades espelhadas com 632 arestas abertas no tronco; manga 7 cm além do cotovelo mesmo com a regra em 85%; pior pose 202 cm²; GLB não reimporta íntegro. `--autofix 3` esgotou as faixas sem resolver |

`medieval_knight` é a versão anterior do asset (dedos fundidos, malha de quads) e fica como caso que
a pipeline ainda não resolve.
