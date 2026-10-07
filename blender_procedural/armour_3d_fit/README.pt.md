# armour_3d_fit

Encaixa peças de armadura já separadas e decimadas num corpo MakeHuman com rig. Recebe as
peças em layout de catálogo (lado a lado, de pé) e entrega a armadura posicionada, com skin
no esqueleto do host, a máscara do corpo e um GLB.

É o terceiro passo depois de `armour_3d_split` e `armour_3d_decimate`. Não altera a
topologia das peças, não cria UV e nunca salva os arquivos de entrada.

## Como funciona

1. **Posição inicial por marcos do corpo.** Cada slot do catálogo diz onde a peça se apoia e
   que comprimento ela deve ter: o peitoral vai do pescoço ao quadril, o saiote da cintura
   para baixo, as botas do chão ao joelho, as manoplas no punho, ao longo do antebraço.
2. **Sub-partes.** Mangas de ombreira giram em torno do ombro para acompanhar o braço.
   Pertence à manga o que está junto de corpo que segue os ossos do braço.
3. **Proporção por seções.** Corpo e peça são lidos como pilhas de seções transversais ao
   longo do tronco ou do membro: um centro e um raio por setor angular, como se mede uma
   circunferência. O que se faz com essas medidas depende do **estilo** (ver abaixo).
4. **Assento.** Uma correção rígida pequena (translação e rotação, com teto) acomoda a peça.
5. **Máscara do corpo.** Some a pele que está debaixo da armadura (por oclusão), os ossos
   que a peça cobre por inteiro (cabeça, mãos, pés) e a pele que atravessou a armadura.
6. **Skin.** Cada peça herda os pesos do corpo vizinho, restritos aos ossos do slot.
7. **Poses.** Corpo e armadura são deformados nos quadros dos clipes do host e a pele que
   atravessa a armadura é medida de novo.
8. **Saída.** `.blend`, GLB com o esqueleto, renders e `report.json` com os gates.

## Estilos

O estilo diz como a peça recebe as proporções do corpo. Escolha com `--style` ou com
`config.style` no job; o padrão é `plate`.

| Estilo | Para | Como ajusta |
|---|---|---|
| `plate` | armadura de placas | rígido: uma escala em profundidade e outra em largura por peça, que só pode afunilar linearmente ao longo do eixo. Toda linha reta da peça continua reta, e a peça fica afastada do corpo. |
| `leather` | couro, jaqueta, calça | flexível: cada seção acompanha a circunferência do corpo naquela altura, com um deslocamento radial suave que preserva o relevo. |

No `plate`, o tamanho vem das seções justas: a escala é a reta que fica acima de 85% das
seções (`quantile`), com afunilamento de no máximo 0,2 de ponta a ponta (`taper`) e 4 mm a
mais de folga (`extra_gap_m`). O corpo que ainda assim encosta numa placa é escondido pela
máscara. No `leather`, os pontos justos de cada seção decidem e uma placa folgada não puxa a
seção para dentro.

Robe (peça solta, tipo vestido) ainda não existe: precisa de outra regra, em que a peça cai
da cintura em vez de seguir ou cercar o corpo.

A ideia de medir o corpo por seções vem do projeto de roupas
[opensew-2](https://github.com/MarcelloMorettoni/opensew-2) (GPL-3.0). Nenhum código de lá
foi copiado; a implementação em `core.py` é própria.

## Uso

```bash
cd /home/ggnp/tools/blender_procedural
python3 -m armour_3d_fit --job armour_3d_fit/examples/medieval_boots.job.json \
  --style plate --out outputs/armour_3d_fit/run_001
```

Requer Python 3.10+ e Blender 5.2.2 no `PATH` (ou `--blender /caminho/blender`). A pasta de
saída precisa ser nova ou vazia. Leva cerca de três minutos no exemplo.

| Exit | Significado |
|---:|---|
| 0 | `PASS`: todos os gates passaram |
| 1 | `FAIL`: o resultado foi gravado, mas algum gate reprovou |
| 2 | erro de configuração ou de execução; veja a mensagem e `failure.json` |

## Job

```json
{
  "name": "medieval_boots",
  "armour": "../../outputs/armour_3d_decimate/boots_002/armour_decimated.blend",
  "pieces": {"Helmet": "helmet", "Chest": "chest", "Legs": "skirt", "Glove_L": "glove.L",
             "Glove_R": "glove.R", "Boot_L": "boot.L", "Boot_R": "boot.R"},
  "body": {"rig": "/caminho/host_geometry.json", "poses": "/caminho/host_poses.json"},
  "body_profile": "../profiles/body.kcc_makehuman_52.json",
  "slot_profile": "../profiles/slots.plate_catalogue.json",
  "config": {}
}
```

- `pieces`: nome do objeto no `.blend` → slot do catálogo. Cada slot só pode ser usado uma vez.
- `body.rig`: JSON do host com malha, ossos e pesos. `body.poses` é opcional; sem ele o teste
  em poses não roda.
- Caminhos relativos valem a partir da pasta do job.
- `config` sobrescreve os padrões de `fit.py` (`DEFAULTS`); opção desconhecida é erro.

**Manoplas:** a ferramenta mede de que mão é cada manopla (pelo lado do polegar e pelo dorso
declarado no slot) e troca as duas quando o catálogo as nomeou ao contrário. O resultado fica
em `report.json` → `handedness`.

## Perfis

| Arquivo | Descreve | Criar outro quando |
|---|---|---|
| `profiles/body.*.json` | malha do corpo, nomes dos ossos por papel, grupos de ossos | o personagem for outro |
| `profiles/slots.*.json` | regra de posição, sub-partes, ossos de máscara e de skin por slot | a família de armadura for outra |

Regras de posição de um slot (`place`):

- `"type": "bbox"`: tamanho por uma medida do corpo (`size`: extensão de uma região num eixo,
  ou distância entre dois ossos, vezes `ratio`) e, por eixo, um ponto da caixa da peça
  (`piece`, de 0 a 1) apoiado num ponto da caixa de uma região do corpo (`region` + `body`)
  ou num osso (`bone`), com `offset_m` opcional.
- `"type": "axis"`: o eixo da peça (`piece_axis`) vai para a direção entre dois ossos
  (`bones`); o ponto a `piece_fraction` do comprimento fica em `at_bone`. Com `palm`, o
  dorso da peça (`piece_back`) vai para o dorso da mão; sem `palm`, `rolls` testa giros em
  torno do eixo e fica com o de menor custo.

Uma região do corpo é o conjunto de vértices cujo osso dominante está na lista.

Proporção (`proportion`, no slot ou numa sub-parte):

- `axis`: `"z"` para o tronco, ou `{"bones": [a, b]}` para um membro;
- `body`: ossos cujos vértices formam as seções do corpo (os braços ficam fora do tronco,
  senão a cintura sai medida com os braços dentro);
- `gap_m`: distância entre a superfície interna da peça e o corpo;
- `max_in_m` e `max_out_m`: teto do deslocamento para dentro e para fora. `max_in_m: 0`
  só deixa a peça crescer (elmo).

Elmo e manoplas são rígidos por escolha: as manoplas não têm `proportion`.

Outros campos do slot: `target_gap_m` (distância desejada entre cavidade e corpo),
`mask_bones` (ossos escondidos por inteiro), `skin_bones` (ossos permitidos no skin),
`ignore_bones` (corpo desconsiderado no refino) e `parts` (sub-partes que dobram).

## Saídas

| Arquivo | Conteúdo |
|---|---|
| `armour_fitted.blend` | corpo com Mask modifier (`BodyMask`), peças com skin e o esqueleto |
| `armour_fitted.glb` | esqueleto e peças com skin, reimportado e conferido |
| `body_mask.npz` | índices dos vértices e das faces do corpo a esconder no host |
| `placements.json` | matriz 4×4 de cada peça (sem a dobra das sub-partes nem a proporção) |
| `report.json` | parâmetros encontrados, medições, poses e gates |
| `fit_sheet.png` | seis vistas sem máscara (linha de cima) e com máscara (linha de baixo) |
| `poke_sheet.png` | as mesmas vistas com a pele que atravessou a armadura em vermelho |
| `worst_pose_sheet.png` | a pior pose testada, com a pele que atravessa em vermelho |

## Gates

| Gate | Mede | Padrão |
|---|---|---|
| `outer_surface_clear_of_body` | casca externa dentro de corpo exposto, somada | ≤ 25 cm² |
| `skin_hidden_for_coming_through` | pele escondida por ter atravessado a armadura em repouso | ≤ 8% do corpo no `plate`, ≤ 5% no `leather` |
| `no_parameter_at_limit` | nenhuma peça parou num teto de translação, rotação ou escala | lista vazia |
| `pose_poke_through` | pele que atravessa a armadura na pior pose | ≤ 150 cm² |
| `glb_reimports_intact` | peças, triângulos, esqueleto e skin depois de reimportar o GLB | verdadeiro |

"Atravessar" é pele visível com armadura a menos de 3 cm por baixo (`poke_depth_m`).

## Resultado no exemplo

Asset `medieval armor 3d model boots.glb`, corpo KCC de 2 m.

| Gate | `plate` | `leather` | Limite |
|---|---:|---:|---:|
| Casca externa dentro de corpo exposto | 0 cm² | 5 cm² | 25 cm² |
| Pele escondida por atravessar a armadura | 6,6% | 3,2% | 8% / 5% |
| Pele atravessando na pior pose | **1040 cm²** | **1049 cm²** | 150 cm² |

No `plate`, a pele escondida a mais são as coxas nas fendas de trás do saiote e o glúteo: a
placa reta não acompanha o corpo, então o corpo encosta nela e é mascarado. Por isso o limite
desse gate é maior no `plate` (`styles.plate.max_poke_masked_body_pct`). Nos dois estilos a pior
pose é `StrafeRight`, com a coxa atravessando o saiote na passada larga.

## Limitações conhecidas

- Saiote e botas acompanham os ossos por skin suave; passadas largas fazem a coxa atravessar
  o saiote.
- O corte da máscara pode aparecer em vistas rasantes, por exemplo na panturrilha junto ao
  topo da bota.
- Se as duas manoplas forem da mesma mão, uma fica com o polegar do lado errado; o report avisa.
- O catálogo `plate_catalogue` assume peças de pé e de frente para `-Y`.
- UV, textura e bake não fazem parte da ferramenta.

## Testes

```bash
python3 -m unittest discover -s armour_3d_fit/tests -p 'test_*.py' -v
```

Cobrem o núcleo numérico no Python do sistema: transformações e tetos, o otimizador num caso
analítico, a proporção por seções num corpo elíptico, detecção de mão de manopla, pesos de
skin e erosão da máscara. O pipeline no
Blender é verificado rodando o job de exemplo.

## Estrutura

| Arquivo | Papel |
|---|---|
| `run.py` | launcher: valida o job, calcula hashes, inicia o Blender isolado |
| `blender_entry.py` | ponto de entrada no Blender; grava `failure.json` em erro |
| `fit.py` | pipeline: posição, sub-partes, proporção, assento, máscara, skin, poses, renders, exportação |
| `core.py` | numpy puro: transformações, resíduos, otimizador, seções, mãos, skin, máscara |
| `sdf.py` | distância assinada ao corpo (BVH com pseudo-normais) |
| `profiles/`, `examples/`, `tests/` | perfis, job de exemplo e testes |
