# armour_3d_diagnose

Mede um run pronto da pipeline: onde a armadura está fora do corpo, e por quanto. Serve para achar a
causa de um defeito antes de mexer num perfil, e para conferir depois que ele sumiu.

Não altera nem salva nada no run. `summary` só lê os relatórios; as outras medidas abrem
`<run>/3_fit/armour_fitted.blend` no Blender e medem a geometria encaixada contra o corpo do job.
Nenhuma medida conhece nome de osso ou de peça: tudo sai do perfil do corpo, do catálogo de slots e do
job gravados no run.

## Uso

```bash
cd /home/ggnp/tools/blender_procedural
python3 -m armour_3d_diagnose summary   --run outputs/armour_3d_pipeline/<run>
python3 -m armour_3d_diagnose summary   --run <run> --against <outro_run>      # os gates lado a lado
python3 -m armour_3d_diagnose clearance --run <run> --piece Boot_R --bands 8
python3 -m armour_3d_diagnose all       --run <run> --out <run>/diagnose.json   # todas as de geometria
```

| Opção | Efeito |
|---|---|
| `--piece NOME` | só essa peça (pode repetir) |
| `--bands N` | fatias ao longo de um osso ou da altura de uma peça (padrão 5) |
| `--reach-m X` | `clearance`: até onde procurar placa a partir da pele (padrão 0,2 m) |
| `--out arquivo.json` | grava as tabelas também em JSON |
| `--blender caminho` | executável do Blender |

## Medidas

| Medida | Responde | Lê |
|---|---|---|
| `summary` | o que passou e o que reprovou; como cada peça e sub-parte foi dimensionada; onde a pele fica escondida; quais as piores poses e sob qual peça | relatórios |
| `clearance` | quanto espaço há entre a pele e a placa por cima, por fatia de cada osso e por lado do membro (frente, trás, esquerda, direita do personagem) | geometria |
| `alignment` | o meio da peça está no meio do membro? Desvio para a esquerda e para a frente, largura e profundidade de cada um | geometria |
| `views` | closes das juntas (gola, ombros, cotovelos, punhos, quadris, joelhos) de frente, de trás, de lado e, no meio do corpo, de cima; grava PNGs em `--out`. Não mede: é a evidência para a avaliação visual | geometria |
| `length` | cada regra de comprimento dos slots (`lengthen`, `fit_length`): onde o asset desenhou a borda (`drawn_%`), o que isso pede (`design_%`), o que a regra usou (`told_%`) e onde a borda está. 100% = a articulação, 150% = metade do osso seguinte | geometria + relatório do fit |
| `proportion` | tamanho de cada peça contra o tronco, como no desenho (`relative_to` ou o asset como veio) e como encaixou | geometria + passo 2 |
| `shape` | quanto o encaixe deformou cada peça em relação a como ela veio (permitindo mover, girar e crescer por um fator) | geometria + passo 2 |
| `enclosed` | peça contra o corpo que ela esconde inteiro (elmo na cabeça, pé da bota): folga por altura e lado, centro, tamanhos e quanto a peça sobra acima e abaixo | geometria |
| `reach` | onde a peça começa e acaba ao longo de cada osso: antes da articulação, nela, ou quanto passa dela | geometria |
| `gloves` | canhão contra o antebraço (inclinação, desvio do osso) e cada dedo da manopla contra o dedo que está dentro | geometria |
| `collar` | folga entre a gola e o pescoço, por altura e por lado | geometria |
| `weights` | que ossos movem cada altura da peça | geometria |
| `symmetry` | quanto a armadura encaixada difere da própria imagem espelhada (ou do par) | geometria |

## Sintoma → medida

| O que se vê | Rode | Olhe |
|---|---|---|
| pele aparecendo no jogo onde o render parado está limpo | `clearance --piece <peça>` | `min_cm` perto de zero e `no_plate_over_it` > 0 na fatia e no lado do defeito |
| peça saindo do membro quando ele dobra | `reach` | `past_the_far_joint_cm`: a parte além da articulação é rígida com o osso de antes |
| peça grande ou pequena demais para o conjunto | `proportion` | `fitted_over_design` longe de 1 |
| manga ou coxa além da articulação, bota além do joelho | `length` | `beyond_cm` |
| elmo grande, pequeno, alto ou fora do centro da cabeça | `enclosed` | `sizes`: larguras da peça e do corpo, `above_the_body_cm`; `room`: mediana por lado (a mínima de 0,0 costuma ser a tampa afundada) |
| vão entre duas peças (coxa e bota, manga e manopla) | `reach` | `ends_at_%` de uma e `starts_at_%` da outra |
| peça torta ou deslocada no membro | `alignment` | `off_to_the_left_cm` e `off_to_the_front_cm` ao longo do osso |
| manopla torta, dedos ao lado dos dedos do corpo | `gloves` | `tilt_against_forearm_deg`, `off_the_bone_cm`, `beside_the_finger_cm` |
| gola aberta, ou pescoço atravessando a gola | `collar` | folgas por lado; negativa é pescoço através da gola |
| um lado diferente do outro | `symmetry`, depois `summary` | `max_mm`; em "pieces", `seat_shift` em x; em "how each piece…", giro e fator das partes `.L` e `.R` |
| peça não acompanha o osso esperado | `weights` | os ossos de cada fatia de altura |
| não sei onde está o problema | `summary` | "skin hidden at rest" (pele que a máscara esconde) e "worst poses" (peça e osso por baixo da pele) |
| quero saber se uma mudança melhorou | `summary --against <run anterior>` | os gates dos dois runs lado a lado |

Leituras que enganam:

- `alignment` usa o meio da extensão da peça. Uma aba ou placa que sai para um lado desloca esse meio
  sem que a cavidade esteja fora do centro; para canos, confie em `centred_parts` no `summary`.
- `clearance` só enxerga a peça pedida. Pele sem placa por cima (`no_plate_over_it`) pode estar
  coberta por outra peça, ou nua de propósito (cotovelo, atrás do joelho).
- `symmetry` do elmo e do tronco não dá zero mesmo com a malha espelhada: o ajuste seção por seção
  e a centragem dos canos são feitos lado a lado. No baseline: tronco 16 mm no pior ponto, 2,5 mm
  em 95% dos vértices; manoplas e botas 0,00 mm.

## Parâmetros

As medidas usam os padrões de `checks.py` (`DEFAULTS`): `bands`, `reach_m`, `owned` (peso de skin a
partir do qual um vértice conta como movido por um osso), `collar_step_m`, `symmetry_samples`. A gola
usa os mesmos parâmetros do encaixe (`COLLAR_DEFAULTS` do `armour_3d_fit` mais a regra `collar` do slot).

## Estrutura

| Arquivo | Papel |
|---|---|
| `run.py` | linha de comando, `summary`, impressão das tabelas |
| `checks.py` | medidas de geometria, dentro do Blender |
| `tests/` | impressão das tabelas e leitura dos relatórios |

```bash
python3 -m unittest discover -s armour_3d_diagnose/tests -p 'test_*.py'
```
