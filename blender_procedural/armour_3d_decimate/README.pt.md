# armour_3d_decimate

Prepara peças de armadura já separadas para o encaixe: recupera os quads, leva cada peça ao
seu orçamento de triângulos e mede o que isso custou. Roda dentro do Blender sobre a saída do
`armour_3d_split`; o arquivo de entrada nunca é salvo.

```bash
blender -b --factory-startup --python-exit-code 2 -P armour_3d_decimate/decimate.py -- \
  --input outputs/armour_3d_split/run/armour_split.blend \
  --budgets armour_3d_decimate/budgets.plate_quads.json \
  --out outputs/armour_3d_decimate/run_001
```

Exit `0` se todos os gates passam, `1` se algum reprova. A pasta de saída precisa ser nova.

## Quads

Um GLB não guarda quads: uma malha de quads exportada pela Tripo chega com cada quad partido
em dois triângulos. Com `"quads": {"enabled": true}` a ferramenta junta os pares de volta
antes de qualquer outra coisa, sem mover vértices.

Reduzir a contagem destrói os quads. Nos assets de referência, a malha inteira fica com 95–98%
das faces em quads; reduzida a um terço, sobram 40–55%. Por isso existem dois modos:

| `reduce` | Comportamento |
|---|---|
| `over_budget` | o orçamento é um teto: a peça que já cabe fica como veio (geometria e normais intactas) |
| `to_budget` (padrão) | toda peça é reduzida até o orçamento |

Para manter quads, peça à Tripo a malha em quads já na contagem final e use `over_budget`.
O Un-Subdivide do Blender não serve: a malha da Tripo não é uma grade regular.

## Pares espelhados

Um conjunto gerado nunca traz duas manoplas ou duas botas iguais. Com `mirror_pairs` no arquivo
de orçamento, a melhor metade de cada par fica e a outra é trocada pela imagem espelhada dela,
no plano do meio do conjunto. Isso acontece antes de tudo; a metade mantida é reduzida uma vez e
a outra é o espelho do resultado, então as duas têm a mesma topologia.

```json
"mirror_pairs": [{"pieces": ["Glove_L", "Glove_R"], "keep": "auto", "fingers": 5},
                 {"pieces": ["Boot_L", "Boot_R"], "keep": "auto"}]
```

Com `"keep": "auto"` a escolha segue esta ordem, e a primeira medida que diferir decide:

1. menos defeitos: arestas abertas, arestas não-manifold, faces sem área, ilhas soltas e pares de
   faces que se atravessam;
2. em manoplas (`fingers`): número de dedos separados na malha mais perto do esperado, depois
   maior comprimento de dedo livre (dedos fundidos não articulam);
3. mais triângulos na origem.

`"keep": "Glove_L"` fixa a escolha. `report.json` → `pairs` traz as medidas das duas metades, a
medida que decidiu (`decided_by`) e a folha `pair_<A>_<B>.png` (frente, três quartos e costas da
primeira, depois da segunda espelhada sobre ela). Quando as duas metades vêm limpas, a decisão
cai nos desempates 2 e 3, que são fracos: confira a folha e fixe `keep` se discordar.

## Parâmetros

Tudo o que os passos abaixo usam vem do arquivo de orçamento; o que ele não trouxer vale o padrão
do bloco correspondente no topo de `decimate.py`.

| Chave do arquivo | Padrões em | Campos |
|---|---|---|
| `mirror_axis` | `MIRROR_AXIS` | eixo do plano de espelho (`"x"`); o plano passa pelo meio do conjunto |
| `mirror_pairs[]` | `PAIR_DEFAULTS` | `pieces`, `keep`, `fingers`, `min_finger_vertices`, `min_finger_share` |
| `symmetrize[]` | `SYMMETRIZE_DEFAULTS` | `pieces`, `keep`, `weld_m`, `degenerate_m`, `attempts`, `margin_triangles` |
| `recess_lids[]` | `RECESS_DEFAULTS` | `pieces`, `out`, `zone`, `facing`, `grow`, `enclose`, `sectors`, `depth`, `inside`, `band`, `narrow_percentile` |
| `quads` | `QUAD_DEFAULTS` | `enabled`, `join_face_angle_deg`, `join_shape_angle_deg`, `min_quad_face_share` |

## Metades espelhadas

Um tronco ou um elmo gerado nunca é igual dos dois lados (um lado da gola mais alto, uma ombreira
mais baixa). Com `symmetrize` uma metade fica e é espelhada sobre a outra no plano do meio do
conjunto, e as duas são unidas ali. É feito na origem e de novo depois da redução, que não trata os
dois lados por igual; como a união acrescenta faces na costura, a redução mira mais baixo até a
peça caber no orçamento.

```json
"symmetrize": [{"pieces": ["Suit", "Helmet"], "keep": "R"}]
```

`keep` é o lado do personagem que fica (`"R"` = -X, o conjunto olha para -Y). `report.json` →
`parts[].symmetrized` traz triângulos antes e depois, arestas abertas e não-manifold e o erro de
espelho (distância de cada vértice espelhado à malha; zero no asset de referência). Detalhes que
só existiam num lado, como uma fivela ou bolsa, passam a existir nos dois ou em nenhum.

## Tampas afundadas

O gerador fecha cada abertura de uma peça com uma tampa rente à borda, e a bota parece tapada.
Com `recess_lids` a tampa é empurrada para dentro da peça: um fundo plano com parede em volta. A
malha continua fechada e com as mesmas faces; de fora a peça parece oca. É feito na malha de
origem, depois dos pares.

```json
"recess_lids": [{"pieces": ["Boot_L", "Boot_R"], "out": [0, 0, 1]},
                {"pieces": ["Glove_L", "Glove_R"], "out": "cuff"},
                {"pieces": ["Helmet"], "out": [0, 0, -1]}]
```

`out` é para onde a abertura olha, ou `"cuff"`: contra o eixo longo de uma manopla. A tampa é a
maior mancha de faces dessa ponta viradas para fora (`facing`, 0,8), estendida pelas rampas até a
parede da peça (`grow`, 0,3) e por tudo o que ela cerca. Só vértices se movem: todo vértice de
dentro da tampa vai para um único nível, a `depth` (0,5: metade) do comprimento da peça abaixo da
borda. Lá embaixo a peça é mais estreita que na boca, então o fundo é o contorno da tampa centrado
na peça naquele nível e encolhido até `inside` (0,85) da largura mais estreita dela ali. A borda
não se move; as faces penduradas nela viram a parede do rebaixo.

Os vértices movidos saem marcados no grupo `a3d_recess`. O `armour_3d_fit` lê a marca, deixa essas
faces fora das amostras de encaixe e da medida de proporção (o membro passa por elas) e remove o
grupo antes de exportar. Com quads ligados, os pares são juntados antes de afundar: as faces
esticadas na parede não formariam quads depois.

`report.json` → `parts[].recess` traz as faces da tampa, os vértices movidos, o comprimento da
peça, a fundura, e quanto o fundo foi encolhido (`floor_scale`) e deslocado (`floor_shift_m`). O tronco não é tratado: tem cinco aberturas em direções diferentes.

## Arquivo de orçamento

| Arquivo | Para |
|---|---|
| `budgets.plate_quads.json` | malha de quads da Tripo: recupera quads, só reduz o que passar do teto, exige ≥ 90% de quads |
| `budgets.plate_arpg.json` | malha de triângulos da Tripo, jogo ARPG top-down/isométrico: reduz cada peça até o teto da faixa (Helmet 4.500, Suit 16.000, luvas 1.800, botas 2.200; total 28.500); sem quads, sem gate de quads |

Para reduzir uma malha de triângulos densa, crie outro arquivo com `"reduce": "to_budget"`
e sem `quads`; a saída fica em triângulos.

Os pares são juntados quando o ângulo entre os dois triângulos e a deformação do quad ficam
abaixo de 60° (`join_face_angle_deg`, `join_shape_angle_deg`). Com 40° as placas curvas perdiam
quads legítimos: o tronco ficava em 90% em vez de 95%.

Campos: `budgets` (triângulos por peça), `reduce`, `quads` (`enabled`,
`join_face_angle_deg`, `join_shape_angle_deg`, `min_quad_face_share`), `target_tolerance`,
`crease_deg` (arestas duras depois de uma redução), `weld_over_diagonal` (solda de vértices
coincidentes, desligada: em peças da Tripo ela cria arestas não-manifold) e `gates`.

Um quad conta como dois triângulos no orçamento, que é o que o motor de jogo desenha.

## Gates por peça

| Gate | Padrão |
|---|---|
| triângulos no orçamento | ±10% se a peça foi reduzida; ≤ orçamento + 10% se não foi |
| fração de faces em quads | ≥ `min_quad_face_share` (0 se `quads` estiver desligado) |
| IoU de silhueta em frente, costas, esquerda e direita | ≥ 0,95 |
| distância entre superfícies, p99 | ≤ 0,5% da diagonal da peça |
| distância entre superfícies, máxima | ≤ 2% da diagonal |
| arestas abertas e não-manifold | não aumentam |
| faces de área zero | nenhuma |

A distância é medida nos dois sentidos, nos vértices e nos centros das faces. Juntar quads não
move vértices, mas a diagonal de um quad não plano pode trocar: no asset de referência isso dá
até 0,7 mm.

## Resultado nos assets de referência

Com `budgets.plate_quads.json`; nenhuma peça foi reduzida.

| Peça | `medieval_knight_dedos` | | `medieval_knight` | |
|---|---:|---:|---:|---:|
| | triângulos | faces em quads | triângulos | faces em quads |
| Helmet | 5.840 | 95% | 6.983 | 98% |
| Suit | 54.917 | 95% | 56.216 | 95% |
| Glove_L | 7.896 | 98% | 6.604 | 96% |
| Glove_R | 7.798 | 97% | 7.288 | 97% |
| Boot_L | 10.856 | 97% | 11.500 | 96% |
| Boot_R | 11.379 | 97% | 11.142 | 97% |
| Total | 98.686 | 96% | 99.733 | 96% |

## Saídas

`armour_decimated.blend`, `report.json` (por peça: `reduced`, `quads_recovered`, contagens de
`tri_faces`/`quad_faces`, distâncias, IoU e gates), `before_after_sheet.png` (uma linha por
peça: frente, três quartos e costas, antes | depois) e os renders individuais em `renders/`.

Numa peça reduzida as normais são recalculadas (sombreamento suave, arestas acima de
`crease_deg` duras) e o relevo fino se perde; para recuperá-lo é preciso assar um normal map,
o que exige UV. Numa peça não reduzida as normais importadas ficam.

Não há testes próprios: a ferramenta é verificada pelos gates do seu `report.json`.
