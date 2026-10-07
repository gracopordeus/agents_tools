# armour_3d_gates

Gates por etapa de um run da pipeline. Lê os relatórios dos três passos, mede a armadura encaixada
com o `armour_3d_diagnose` e dá um veredito por etapa. Cada gate reprovado diz onde está o problema
e, quando existe, o remédio: qual parâmetro mudar e dentro de que faixa. Nada no run é alterado.

```bash
cd /home/ggnp/tools/blender_procedural
python3 -m armour_3d_gates --run outputs/armour_3d_pipeline/<run>      # grava <run>/4_gates/gates.json
```

No dia a dia quem chama é a pipeline (passo 4). Código de saída `0` quando todos os gates passam.

## Etapas e gates

| Etapa | Gate | Mede | Remédio automático |
|---|---|---|---|
| 1 separação | `split_delivered` | o passo 1 entregou | — |
| 2 pares | `pairs_mirrored` | todo par esquerda/direita é uma peça e o espelho dela | — |
| 3 metades | `halves_mirrored_mm` | peça do meio do corpo é metade + espelho (erro em mm) | inclui a peça em `symmetrize` |
| 3 metades | `halves_closed` | sem aresta aberta nem não-manifold depois de espelhar | — |
| 4 tampas | `lid_depth_share` | tampa afundada a 50% do comprimento da peça | — |
| 5 orçamento | `budget` | toda peça no orçamento e nos gates do passo 2 | — |
| 6 encaixe | `handedness` | cada manopla na sua mão | — |
| 6 encaixe | `length_as_designed_pct` | a regra de comprimento manda a borda para onde o asset a desenhou | troca `fraction` por `"design"` |
| 6 encaixe | `length_beyond_cm`, `length_short_cm` | borda onde a regra de comprimento mandou (cotovelo, joelho) | ajusta `fraction` da regra |
| 6 encaixe | `mouth_off_centre_cm` | membro no centro da boca do cano | mais rodadas de centragem |
| 6 encaixe | `collar_gap_cm`, `collar_sides_differ_cm` | folga gola–pescoço e igualdade dos lados | aumenta `collar.gap_m` |
| 6 encaixe | `set_proportion` | tamanho da peça contra o tronco, como no desenho | escreve `relative_to` com a proporção do asset |
| 6 encaixe | `pair_symmetry_mm`, `self_symmetry_*` | simetria depois do encaixe | — |
| 6 encaixe | `shape_p95_pct` | quanto o encaixe deformou cada peça | — |
| 7 máscara, skin, poses, export | `fit_gates` | os cinco gates do próprio fit | — |

A etapa 8 (jogo) não é gate deste arquivo: é o passo `--game` da pipeline, que instala as peças no
projeto Godot e roda a auditoria dele.

## Limites

`gates.plate.json`. Cada gate traz o limite e, ao lado, `approved`: o valor do run que o usuário
aprovou (`dedos_050`, 2026-10-07). Os limites são esse valor com uma margem pequena. **Só o usuário
muda um limite.** Dois limites carregam um defeito conhecido do run aprovado, e não uma meta:

- `collar_gap_cm` mínimo −1,0: no run aprovado o pescoço atravessa a borda da gola 0,9 cm na frente.
  Com o limite em 0 a correção automática resolve (`collar.gap_m` 0,012 → 0,024), mas a gola fica
  1,5 cm mais aberta nos lados; a troca é do usuário.
- O canhão da manopla passa 1,4 cm do cotovelo; a regra de comprimento dela não age em placa (a
  manopla não muda de forma), então isso aparece em `armour_3d_diagnose length` como leitura e não
  entra nos gates de comprimento.

## Remédios

`remedies.py`. Um remédio nunca edita arquivo do repositório: devolve remendos (`arquivo`, caminho,
valor, motivo). A pipeline, com `--autofix N`, aplica os remendos em cópias dos perfis dentro do run
(`overrides/attempt_k/`), roda de novo os passos afetados e repete até N vezes. Para quando não há
remédio para o que sobrou ou quando os remendos se repetem (faixa esgotada). Se os gates passam, o
`pipeline.json` traz em `promote` onde estão os perfis remendados; quem os copia para o repositório
é uma pessoa.

Gate sem remédio é para gente: o `gates.json` diz a peça, a linha da medida e o número.

## O que os gates não veem

- **Amassado local** (a gola com entalhe atrás): tentei três medidas e nenhuma separou o run
  amassado do corrigido. `shape_p95_pct` só acusa deformação geral maior que a do run aprovado.
  Continua sendo conferência visual: vistas de trás e de cima da gola.
- **Folga pele–placa por faixa**: há mínimas de 0,0 cm no run aprovado; virar gate reprovaria o
  baseline. Fica como medida (`armour_3d_diagnose clearance`).
- **Andar**: o clipe não está nas poses medidas.

## Verificação

```bash
python3 -m unittest discover -s armour_3d_gates/tests -p 'test_*.py'
```

`evaluate()` é puro (sem arquivo, sem Blender): os testes dão a ele medidas sintéticas e conferem
que cada defeito derruba o seu gate e só ele.
