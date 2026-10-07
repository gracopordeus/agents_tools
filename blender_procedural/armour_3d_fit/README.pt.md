# armour_3d_fit

Encaixa peças de armadura já separadas e otimizadas num corpo MakeHuman com rig. Recebe as
peças montadas como uma figura de pé e entrega a
armadura posicionada, com skin no esqueleto do host, a máscara do corpo e um GLB.

É o terceiro passo depois de `armour_3d_split` e `armour_3d_decimate`. Só move vértices: a
topologia não muda e os quads continuam quads no `.blend`. O GLB sai em triângulos, porque o
formato não guarda quads. Não cria UV e nunca salva os arquivos de entrada.

## Como funciona

1. **Posição inicial por marcos do corpo.** Cada slot do catálogo diz onde a peça se apoia e
   que tamanho ela deve ter: a escala única do conjunto (armadura montada) ou um comprimento
   anatômico (o tronco vai do pescoço ao joelho, as manoplas ficam no punho, ao longo do
   antebraço). As botas giram para a direção do pé e são inclinadas ao longo da canela por
   cisalhamento, de modo que a sola continua no chão.
2. **Sub-partes.** Mangas giram em torno do ombro, coxas em torno do quadril e a mão da
   manopla em torno do punho. Pertence à sub-parte o que está junto de corpo que segue os
   ossos dela. A sub-parte balança na articulação; girar em torno do próprio comprimento é
   penalizado.
3. **Proporção por seções.** Corpo e peça são lidos como pilhas de seções transversais ao
   longo do tronco ou do membro: um centro e um raio por setor angular, como se mede uma
   circunferência. O que se faz com essas medidas depende do **estilo** (ver abaixo).
4. **Assento.** Uma correção rígida pequena (translação e rotação, com teto) acomoda a peça.
5. **Dedos.** Os dedos da manopla são achados na malha como tubos que se separam da mão. Se
   forem dedos inteiros, a palma é ajustada para as raízes deles caírem sobre as dos dedos do
   corpo, cada dedo toma a direção e as dobras do dedo do corpo a partir da própria raiz, e
   suas falanges recebem os ossos desse dedo. Se vierem fundidos (só as pontas soltas), a
   manopla fica como está e herda os pesos dos dedos como o resto do skin.
6. **Máscara do corpo.** Some a pele que está debaixo da armadura (por oclusão), os ossos
   que a peça cobre por inteiro (cabeça, mãos, pés) e a pele que atravessou a armadura.
7. **Skin.** Cada peça herda os pesos do corpo vizinho, restritos aos ossos do slot.
8. **Poses.** Corpo e armadura são deformados nos quadros dos clipes do host e a pele que
   atravessa a armadura é medida de novo.
9. **Saída.** `.blend`, GLB com o esqueleto, renders e `report.json` com os gates.

## Estilos

O estilo diz como a peça recebe as proporções do corpo. Escolha com `--style` ou com
`config.style` no job; o padrão é `plate`.

| Estilo | Para | Como ajusta |
|---|---|---|
| `plate` | armadura de placas | o conjunto todo recebe uma escala só; depois cada peça (e cada manga ou coxa) cresce por **um fator único em todas as direções**, a partir de onde se apoia no corpo. A forma modelada não muda, só o tamanho. |
| `leather` | couro, jaqueta, calça | flexível: cada seção acompanha a circunferência do corpo naquela altura, com um deslocamento radial suave que preserva o relevo. |

No `plate`, o fator de cada peça vem das seções justas: a largura e a profundidade que o corpo
pede (a reta acima de 85% das seções, `quantile`, com 4 mm de folga, `extra_gap_m`) são tiradas
em média ao longo da peça e a maior das duas decide. O teto é `isotropic_max` (0,45). A peça
cresce em torno de `proportion.pivot`: um osso (`{"bone": "neck"}`: o elmo e o tronco crescem do
pescoço; a manga do ombro, a coxa do quadril), `"bottom"` (a bota mantém a sola no chão) ou
`"body_top"`. Um slot limita o próprio fator com `proportion.isotropic_max`: a manopla usa 0,
porque o tamanho dela é o da mão, não o da grossura do antebraço. Nesse modo nada mais muda a
forma: `fit_length`, a escala da palma e a das sub-partes ficam desligadas. O corpo que ainda
assim encosta numa placa é escondido pela máscara.

**Ajuste completo num slot** (`proportion.shape: "flexible"`). Uma peça de placa que precisa seguir o
membro de perto recebe o mesmo ajuste do `leather`, seção por seção, qualquer que seja o estilo. A
bota usa isso: a canela do corpo de referência é grossa na panturrilha e fina no tornozelo, e um
fator único não cobria as duas. O deslocamento para fora vai até `max_out_m` (5 cm na bota) e a
folga alvo é `gap_m` (1,5 cm na bota: com 0,6 cm a parte de trás da panturrilha ficava a 0,1–0,4 cm
da placa em repouso e aparecia no jogo ao menor movimento).

**Comprimento que sempre segue o membro** (`fit_length` com `"always": true`). Vale mesmo no modo de
fator único. A bota é encurtada ao longo da canela até acabar no joelho (`from_bone` tornozelo,
`to_bone` joelho, `fraction` 1,0): além dele, a parte de cima é rígida com a canela e sai para fora
da coxa quando a perna dobra.

No asset de referência a bota passava 18 cm do joelho e 396 cm² de pele da canela por perna
atravessavam o cano; agora passa 0,5 cm e ficam 22–40 cm², sem pele à vista em repouso. Foi
tentado antes um perfil em cone (fatores no tornozelo, na panturrilha e no joelho, com retas entre
eles): deixava a bota com aspecto achatado e foi removido.

**Canos centrados na perna** (sub-parte com `"centre": true`; as pernas do `suit`). É o último passo
da peça, depois do assento. Na boca do cano (o quarto final dele, `mouth`), o meio da cavidade é
medido contra o meio da carne da perna, não do osso, e o cano balança em torno da articulação até
os dois coincidirem. A cavidade é lida de dentro para fora (a parede mais próxima em cada direção),
então abas e placas que saem para um lado não deslocam o meio. Mais acima o cano não é usado: as
lâminas sobrepostas fazem a parede interna saltar e dão uma inclinação falsa (tentado: 18–20° de
giro e o cinto deformado). Cada lado é centrado por conta própria. No asset de referência a boca
estava 3,6 e 4,3 cm fora do centro; com 6,1° e 7,4° de balanço fica a 0,01–0,02 cm.
`report.json` → `pieces.Suit.centred_parts`.

**Tronco** (slot `suit` do catálogo de placas, decisão do usuário em 2026-10-07). Usa o ajuste
completo na peça e nas sub-partes. O ajuste completo só alarga em volta do corpo; o comprimento
vem à parte, por `lengthen`:

- `"lengthen": true` (tronco): ao longo do eixo a peça cresce pelo fator único.
- `"lengthen": {"fraction": ...}` (mangas e coxas) e `fit_length` (bota, manopla): o comprimento é
  **medido no corpo**, em coordenada de cadeia do membro: `1.0` é a articulação no fim do primeiro
  osso (cotovelo, joelho), `1.5` é a metade do osso seguinte (`beyond_bone`), `0.6` é 60% do primeiro.
  O que fica antes da primeira articulação (ombreira) não muda; o lado esquerdo repete o direito.

**De onde vem a fração: do desenho do asset** (`"fraction": "design"`, decisão do usuário em
2026-10-07). O corpo é a régua, o asset é o desenho: um conjunto desenhado com manga até o cotovelo
e um com manga até o meio do antebraço não podem ser cortados os dois no cotovelo. Logo depois de
as peças serem postas no corpo (uma escala só, nada redimensionado), o asset está sobre o corpo nas
suas próprias proporções, que são humanas. `read_design` lê ali onde cai a borda de cada peça e
decide (`core.design_reach`):

| Leitura | Resultado |
|---|---|
| borda desenhada a até `snap` (15% do osso) de uma articulação | vai para a articulação |
| duas peças que se tocam no asset (`meets`; folga até `touch_share` = 3% da altura) e uma delas perto da articulação | as duas na articulação |
| duas peças que se tocam longe da articulação (manga até o meio do antebraço, manopla curta) | encontram-se no ponto desenhado, repartido para não sobrar pele nem sobrepor |
| peça que não toca vizinha e termina longe da articulação | fica onde foi desenhada |

A leitura fica em `report.json` → `pieces.<peça>.design` (`drawn`, `fraction`, `touches`, `why`) e
aparece em `armour_3d_diagnose length`. Um número em `fraction` continua valendo e ignora o desenho
(o gate `length_as_designed_pct` acusa a diferença). Com número, `fit_length` só encurta; com
`"design"`, estica ou encurta até `min_factor`/`max_factor` (0,5 a 1,5). A direção depois da
articulação é a do primeiro osso: vale para membro quase reto, como na pose de repouso.

No asset de referência as leituras são manga 92%, coxa 76%, bota 112% e manopla 104%; todas tocam
a vizinha e resolvem para 100%, o mesmo resultado de antes.

**Tamanho contra outra peça** (`relative_to` na `proportion`, usado no elmo):
`{"slot": "suit", "width": 0.28736, "height": 0.2951, "by": "mean"}` dá à peça a proporção planejada
no desenho contra a peça de referência já encaixada (largura no eixo de espelho, altura em Z). Sai
um fator só (`by`: `mean`, `width` ou `height`), que substitui o que o corpo sob a peça pediria; o
relatório traz os dois fatores em `isotropic.relative_to`. A peça de referência é encaixada antes.

**Manopla**: a mão fica só com a escala do conjunto; o canhão é a sub-parte `cuff.<lado>`, com
`proportion` de `shape: "flexible"` contra o antebraço e `"centre": true`. O slot leva
`"seat": {"translation_m": 0.0, "rotation_deg": 0.0}`: com limite zero o assento final não desloca
nem gira a peça (e não conta como parâmetro no teto).

Mais dois passos próprios do tronco:

- **Gola** (`collar`): o que fica acima da base do pescoço, a até `reach_m` do eixo dele. No ajuste
  do tronco a gola só pode ser empurrada para fora, nunca puxada para dentro (puxada, ela copiava a
  cavidade entre as omoplatas e ficava com um entalhe atrás). Depois o passo próprio a centra no
  pescoço, de lado e de frente para trás (`centre_front`, até `max_shift_m`), e a dimensiona pela
  borda, lendo cada setor junto com o seu espelho: estreitando em funil (nada na base, tudo na borda,
  até `max_in`) ou alargando a gola inteira (até `max_out`). No asset de referência a borda fecha 32%.
- **Assento no plano do meio** (`seat.midline`, também no elmo): a peça só anda para a frente, para
  cima e inclina para a frente. Deslocar para o lado, tombar ou girar a deixaria diferente dos dois
  lados.

Com a malha do tronco espelhada no passo 2, o resultado é simétrico: as duas pernas da calça
recebem o mesmo balanço (1,8°) e a gola não precisa de deslocamento lateral.

**Onde ficam os padrões.** Passos com regra própria leem os campos da regra e completam com um bloco
no código: `CENTRE_DEFAULTS` (boca do cano: `mouth`, `stations`, `sectors`, `closed`, `slab_m`,
`rounds`, `tolerance_m`; passe `"centre": {...}` em vez de `true` para trocar algum) e
`COLLAR_DEFAULTS` (gola: `gap_m`, `reach_m`, `blend_m`, `base_blend_m`, `max_in`, `max_out`,
`max_shift_m`, `centre_front`, `widest_percentile`, `rim_from`, `top_percentile`,
`sectors`, `neck_percentile`, `collar_percentile`, `side_percentiles`). `LENGTH_DEFAULTS` (comprimento pelo osso: `fraction`, `rim_percentile`, `min_factor`, `max_factor`,
`snap`, `touch_share`).
O resto está em `DEFAULTS`
e é trocado por `config` no job; `mirror_tolerance_m` decide quando duas peças são imagem espelhada
uma da outra.

**Simetria** (`config.symmetry_reference`, padrão `"R"`; só estilos rígidos). O corpo é simétrico,
a armadura gerada não, e ajustados em separado os dois lados ficam diferentes. O lado de referência
é ajustado e o outro repete o ajuste, espelhado: as sub-partes `nome.L`/`nome.R` de uma peça
recebem o mesmo giro refletido e o mesmo fator, e a peça que é imagem espelhada da sua gêmea
(`glove.L`/`glove.R`, `boot.L`/`boot.R` depois do passo 2) vira o espelho exato do encaixe dela.
`mirrored_from` no report diz o que foi copiado. `null` ajusta cada lado por conta própria.

Outras formas de dimensionar a placa, em `config` (medidas no asset de referência):

| `config` | Efeito | Pele escondida | Pior pose |
|---|---|---:|---:|
| padrão | escala do conjunto + um fator por peça | 3,9% | 50 cm² |
| `styles.plate.isotropic: false`, `uniform_set_scale: false` | Suit com escala própria; largura e profundidade de cada peça ajustadas em separado (a peça muda de forma) | 4,7% | 127 cm² |
| `keep_shape: true` | escala do conjunto e mais nada | 21,7% | 519 cm² |

No `leather`, os pontos justos de cada seção decidem e uma placa folgada não puxa a seção para
dentro; a peça deforma seção a seção, e nenhuma das opções acima se aplica.

Robe (peça solta, tipo vestido) ainda não existe: precisa de outra regra, em que a peça cai
da cintura em vez de seguir ou cercar o corpo.

A ideia de medir o corpo por seções vem do projeto de roupas
[opensew-2](https://github.com/MarcelloMorettoni/opensew-2) (GPL-3.0). Nenhum código de lá
foi copiado; a implementação em `core.py` é própria.

## Uso

```bash
cd /home/ggnp/tools/blender_procedural
python3 -m armour_3d_fit --job armour_3d_fit/examples/medieval_knight.job.json \
  --style plate --out outputs/armour_3d_fit/run_001
```

Requer Python 3.10+ e Blender 5.2.2 no `PATH` (ou `--blender /caminho/blender`). A pasta de
saída precisa ser nova ou vazia. Leva cerca de um minuto no asset de referência. O job de
exemplo lê a saída do passo 2 do run de referência da pipeline; no dia a dia, use
`armour_3d_pipeline`, que gera o job sozinho.

| Exit | Significado |
|---:|---|
| 0 | `PASS`: todos os gates passaram |
| 1 | `FAIL`: o resultado foi gravado, mas algum gate reprovou |
| 2 | erro de configuração ou de execução; veja a mensagem e `failure.json` |

## Job

```json
{
  "name": "medieval_knight",
  "armour": "../../outputs/armour_3d_pipeline/knight_004/2_decimate/armour_decimated.blend",
  "pieces": {"Helmet": "helmet", "Suit": "suit", "Glove_L": "glove.L", "Glove_R": "glove.R",
             "Boot_L": "boot.L", "Boot_R": "boot.R"},
  "body": {"rig": "../fixtures/kcc_makehuman_52/host_geometry.json",
           "poses": "../fixtures/kcc_makehuman_52/host_poses.json"},
  "body_profile": "../profiles/body.kcc_makehuman_52.json",
  "slot_profile": "../profiles/slots.plate_assembled.json",
  "config": {}
}
```

- `pieces`: nome do objeto no `.blend` → slot do catálogo. Cada slot só pode ser usado uma vez.
- `body.rig`: JSON do host com malha, ossos e pesos. `body.poses` é opcional; sem ele o teste
  em poses não roda.
- Caminhos relativos valem a partir da pasta do job.
- `config` sobrescreve os padrões de `fit.py` (`DEFAULTS`); opção desconhecida é erro.

**Manoplas:** a ferramenta decide de que mão é cada manopla e troca as duas quando os nomes
vieram ao contrário. Numa armadura montada o lado vem da posição da peça
(`"handedness_from": "layout"`: de frente para `-Y`, a manopla em `+X` é a esquerda). Num
catálogo vem da forma: lado do polegar e dorso declarado no slot; essa medida pode errar em
manoplas com placas assimétricas. O resultado fica em `report.json` → `handedness`.

## Perfis

| Arquivo | Descreve | Criar outro quando |
|---|---|---|
| `profiles/body.*.json` | malha do corpo, nomes dos ossos por papel, grupos de ossos, e `mirror`: o plano de simetria do corpo (`axis`, `at_m`), de que lado dele fica a esquerda do personagem (`left`) e como os dois lados aparecem no nome dos ossos (`bone_tokens`) | o personagem for outro |
| `profiles/slots.*.json` | regra de posição, sub-partes, ossos de máscara e de skin por slot | a família de armadura for outra |

| Catálogo de slots | Layout do asset | Slots |
|---|---|---|
| `slots.plate_assembled.json` | armadura montada, de pé, braços caídos, de frente para `-Y` | `helmet`, `suit` (peitoral, ombreiras e coxas numa peça), `glove.L/R`, `boot.L/R` |

Regras de posição de um slot (`place`):

- `"type": "bbox"`: tamanho por uma medida do corpo (`size`: extensão de uma região num eixo,
  ou distância entre dois ossos, vezes `ratio`) e, por eixo, um ponto da caixa da peça
  (`piece`, de 0 a 1) apoiado num ponto da caixa de uma região do corpo (`region` + `body`)
  ou num osso (`bone`), com `offset_m` opcional.
- `"type": "axis"`: o eixo da peça (`piece_axis`) vai para a direção entre dois ossos
  (`bones`); o ponto a `piece_fraction` do comprimento fica em `at_bone`. `piece_fraction`
  pode ser um número ou `{"from_finger_roots": true, "fallback": f}`, que acha o punho a
  partir das raízes dos dedos da manopla e usa `f` quando não consegue separá-los. Com
  `"piece_axis_from": "mesh"` o eixo declarado é só a dica do sentido: o eixo é medido na malha, e a
  linha central do canhão (os meios das seções entre a borda e o punho) vai sobre o osso, com o
  ponto do punho na articulação. Sem isso, uma manopla de figura em pose A (braços abertos) fica
  torta em relação ao antebraço; o resultado sai em `placement.cuff_line`. Com `palm`, o
  dorso da peça (`piece_back`) vai para o dorso da mão; sem `palm`, `rolls` testa giros em
  torno do eixo e fica com o de menor custo.

- `size`: `{"set_height_ratio": r}` usa uma escala só para todas as peças (altura do corpo ×
  `r` ÷ altura do conjunto), e mantém as proporções entre peças de uma armadura montada. As
  outras formas dimensionam a peça por uma medida do corpo.
- `yaw_to` (em `bbox`): gira a peça em torno da vertical até o pé dela apontar na direção de
  dois ossos (`bones`), medindo o pé pela faixa de baixo da peça (`piece_below`).

Uma região do corpo é o conjunto de vértices cujo osso dominante está na lista.

Sub-partes (`parts`): `pivot_bone` (articulação), `toward_bone` (para onde o membro vai),
`bones` (ossos cujo corpo define a sub-parte), `max_rotation_deg`, `max_scale`,
`from_bones` (direção em que a sub-parte foi posta; sem ele, pendurada para baixo),
`twist_weight` (custo de girar em torno do próprio comprimento) e `"fit": false` (alinha
direto ao osso, sem otimizar; para partes sem cavidade, como a mão da manopla).

Dedos (`fingers`, no slot da manopla): `wrist` e `toward` (osso do punho e osso para onde a
mão aponta) e `chains` (nome do dedo → grupo de ossos do perfil do corpo, polegar primeiro,
depois do lado do polegar para fora). Opcionais: `min_vertices`, `min_height_m`,
`min_length_fraction` (quanto do dedo do corpo o tubo precisa cobrir para contar como dedo
inteiro; 0,55), `start_fraction` e `thumb_start_fraction` (onde o dedo sai da palma, em
fração do primeiro osso), `max_palm_scale` (0,15), `min_scale` e `max_scale` da seção do dedo,
`gap_m`, `blend_m`, `joint_blend_m`, `root_smooth_iterations` e `tip_margin_m`. O resultado
fica em `report.json` → `fingers`, com `articulated` e, se não, o motivo.

Proporção (`proportion`, no slot ou numa sub-parte):

- `axis`: `"z"` para o tronco, ou `{"bones": [a, b]}` para um membro;
- `body`: ossos cujos vértices formam as seções do corpo (os braços ficam fora do tronco,
  senão a cintura sai medida com os braços dentro);
- `gap_m`: distância entre a superfície interna da peça e o corpo;
- `max_in_m` e `max_out_m`: teto do deslocamento para dentro e para fora. `max_in_m: 0`
  só deixa a peça crescer (elmo).

No estilo `plate`, a largura de um anel fechado em torno do membro é medida a partir do meio
do próprio anel, e tampas e solas (faces viradas para o eixo, `wall_max_axis_dot`) ficam fora
da medida. As peças da Tripo fecham as aberturas com tampas; sem isso a peça parecia não ter
espaço dentro e era inflada.

Outros campos do slot: `asset` (nome do arquivo em que a peça é entregue; peças com o mesmo nome saem juntas num GLB), `target_gap_m` (distância desejada entre cavidade e corpo),
`mask_bones` (ossos escondidos por inteiro), `skin_bones` (ossos permitidos no skin),
`ignore_bones` (corpo desconsiderado no refino), `fit_length` (encurta a peça ao longo de um
membro, de `from_bone` para `to_bone`, até `fraction` do comprimento dele; nunca estica: o
canhão da manopla acaba no cotovelo), `lean` (cisalha a peça ao longo de dois
ossos, acima do segundo: a bota segue a canela) e `seat` (tetos próprios do assento:
`translation_m`, `rotation_deg`, e `lock_z` para a peça não sair do chão; `rotation_deg: 0`
trava a rotação).

## Saídas

| Arquivo | Conteúdo |
|---|---|
| `armour_fitted.blend` | corpo com Mask modifier (`BodyMask`), peças com skin e o esqueleto |
| `armour_fitted.glb` | esqueleto e peças com skin, reimportado e conferido |
| `pieces/<Asset>.glb` | **um arquivo por asset**: uma peça, ou as peças que os slots agrupam com `"asset"` (`Gloves` = as duas manoplas, `Boots` = as duas botas), com o esqueleto e o skin; reimportado e conferido |
| `pieces/<Asset>.mask.json` | vértices do corpo que o asset esconde quando usado sozinho |
| `pieces/set.mask.json`, `pieces/manifest.json` | máscara do conjunto completo (inclui a pele que só aparece entre dois assets) e a lista dos assets com suas peças |
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
| `pose_poke_through` | pele que atravessa a armadura **e fica à vista** na pior pose | ≤ 150 cm² |
| `glb_reimports_intact` | peças, triângulos, esqueleto e skin depois de reimportar o GLB | verdadeiro |

"Atravessar" é pele não mascarada com armadura a menos de 3 cm por baixo (`poke_depth_m`). Nas
poses só conta a pele que fica à vista: a que outra placa cobre (mesmo teste de oclusão da
máscara) fica fora do gate e é registrada à parte, por quadro, em `poke_area_incl_covered_cm2`.
Cada quadro traz também a área por osso (`area_cm2_by_bone`) e por peça que está debaixo da pele, com o osso que move a placa ali (`area_cm2_by_piece`, por exemplo `Suit:mixamorig_Spine2`): é o que diz se o problema é a manga ou o peitoral. `config.pose_clips` restringe os clipes testados.

## Resultado nos assets de referência

Corpo KCC de 2 m, estilo `plate`.

| Gate | `medieval_knight_dedos` | `medieval_knight` | Limite |
|---|---:|---:|---:|
| Casca externa dentro de corpo exposto | 1 cm² | 0 cm² | 25 cm² |
| Pele escondida por atravessar a armadura | 3,9% | 2,5% | 8% |
| Peça parada num teto | nenhuma | nenhuma | nenhuma |
| Pele à vista atravessando na pior pose | 50 cm² | 12 cm² | 150 cm² |
| GLB reimportado (conjunto e peça a peça) | íntegro | íntegro | íntegro |
| Resultado | `PASS` | `PASS` | |

Runs: `outputs/armour_3d_pipeline/dedos_040` (referência; todos os gates passam, pior pose 136 cm²) e `knight_022`. A tabela acima é de antes do
espelhamento dos pares, da simetria, das tampas afundadas, da bota no joelho e do ajuste completo
no tronco. Hoje `medieval_knight_dedos` passa com 82 cm² na pior pose, 1,9% de pele escondida e
28.266 triângulos. `medieval_knight` **reprova**: 219 cm² na pior pose (coxa esquerda, com o ajuste
completo no tronco) e o GLB do tronco volta com um triângulo a menos (58.427 de 58.428), descartado
pelo exportador.

**`medieval_knight_dedos`** (referência atual: GLB de 2026-10-07 03:26, figura em pose A, malha de
triângulos reduzida ao orçamento ARPG, 28.488 triângulos, mãos abertas com os cinco dedos
articulados). Fatores por peça: elmo 1,33; tronco 1,16; mangas 1,20; coxas 1,40 e 1,44 (perto do
teto de 1,45); botas 1,19 e 1,18; manoplas 1,00 (o antebraço pedia 1,18 e 1,22).

Dois defeitos deste asset foram resolvidos no perfil:

- A manga passa do cotovelo. Presa só ao braço, era atravessada pelo antebraço; o slot `suit` tem
  `forearm.L/R` em `skin_bones` e a ponta da manga acompanha o antebraço.
- As manoplas vêm a cerca de 37° da vertical que o slot declarava. Com `piece_axis_from: mesh` a
  linha central do canhão fica a 2–4° do osso do antebraço (era 25°).

Como a manopla não é redimensionada, a mão dela é maior que a do corpo: os dedos passam cerca de
2 cm dos dedos do corpo e ficam 1,5 a 3,7 cm ao lado deles.

**`medieval_knight`** (dedos fundidos lado a lado, malha de quads). Só as pontas dos dedos são
soltas, então a manopla não é articulada dedo a dedo: herda os pesos dos dedos do corpo e a mão
fecha em bloco. Serve de teste desse caminho e do caminho de quads.

O estilo `leather` não foi rodado nesses assets.

## Limitações conhecidas

- Os dedos articulados ficam presos à palma da manopla, não aos ossos: giram em torno das
  articulações do corpo, que ficam a alguns centímetros. Em repouso a mão fica natural; fechada,
  o punho fica um pouco aberto, em garra.
- Na pose de repouso os dedos da manopla acompanham os do corpo, que são levemente curvados;
  não ficam retos como no asset.
- Manopla com dedos fundidos fecha e abre em bloco; se a animação abrir os dedos em leque, a
  malha entre eles estica.
- Entre a manga e o canhão da manopla o braço fica à mostra por dentro do cotovelo: o asset
  não tem placa ali.
- Atrás do joelho dobrado sobra pele à vista entre a coxa e a bota.
- O corte da máscara pode aparecer em vistas rasantes, por exemplo na panturrilha junto ao
  topo da bota.
- Se as duas manoplas forem da mesma mão, uma fica com o polegar do lado errado; o report avisa.
- O catálogo assume a armadura de pé e de frente para `-Y`. Asset em layout de catálogo (peças
  lado a lado) não tem catálogo de slots.
- UV, textura e bake não fazem parte da ferramenta.

## Testes

```bash
python3 -m unittest discover -s armour_3d_fit/tests -p 'test_*.py' -v
```

Cobrem o núcleo numérico no Python do sistema: transformações e tetos, o otimizador num caso
analítico, a separação dos dedos e o assentamento de um dedo numa cadeia de ossos, a
proporção por seções num corpo elíptico, a largura de um anel em torno de um
membro inclinado, detecção de mão de manopla, pesos de skin e erosão da máscara. O pipeline no
Blender é verificado rodando os dois assets de referência pela pipeline.

## Estrutura

| Arquivo | Papel |
|---|---|
| `run.py` | launcher: valida o job, calcula hashes, inicia o Blender isolado |
| `blender_entry.py` | ponto de entrada no Blender; grava `failure.json` em erro |
| `fit.py` | pipeline: posição, sub-partes, proporção, assento, máscara, skin, poses, renders, exportação |
| `core.py` | numpy puro: transformações, resíduos, otimizador, seções, mãos, skin, máscara |
| `sdf.py` | distância assinada ao corpo (BVH com pseudo-normais) |
| `profiles/`, `examples/`, `tests/` | perfis, job de exemplo e testes |
| `fixtures/kcc_makehuman_52/` | malha, esqueleto, pesos e poses do corpo KCC exportados do host |
