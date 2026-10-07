# armour_3d_decimate

Reduz peças de armadura já separadas a um orçamento de triângulos por peça e mede o que a
redução custou. Roda dentro do Blender sobre a saída do `armour_3d_split`; o arquivo de
entrada nunca é salvo.

```bash
blender -b --factory-startup --python-exit-code 2 -P armour_3d_decimate/decimate.py -- \
  --input outputs/armour_3d_split/run/armour_split.blend \
  --budgets armour_3d_decimate/budgets.plate.json \
  --out outputs/armour_3d_decimate/run_001
```

Exit `0` se todos os gates passam, `1` se algum reprova. A pasta de saída precisa ser nova.

## Orçamentos

`budgets.plate.json`: triângulos por peça (`budgets`), precisão do alvo (`target_tolerance`),
ângulo das arestas duras (`crease_deg`), solda opcional de vértices coincidentes
(`weld_over_diagonal`, desligada: em peças da Tripo ela cria arestas não-manifold) e os gates.

## Gates por peça

| Gate | Padrão |
|---|---|
| triângulos dentro do orçamento | ±10% |
| IoU de silhueta em frente, costas, esquerda e direita | ≥ 0,95 |
| distância entre superfícies, p99 | ≤ 0,5% da diagonal da peça |
| distância entre superfícies, máxima | ≤ 2% da diagonal |
| arestas abertas e não-manifold | não aumentam |
| faces de área zero | nenhuma |

A distância é medida nos dois sentidos, nos vértices e nos centros das faces.

## Saídas

`armour_decimated.blend`, `report.json`, `before_after_sheet.png` (uma linha por peça: frente,
três quartos e costas, antes | depois) e os renders individuais em `renders/`.

As normais são recalculadas (sombreamento suave, arestas acima de `crease_deg` duras): as
normais importadas não sobrevivem à decimação. O relevo fino se perde; para recuperá-lo é
preciso assar um normal map, o que exige UV.
