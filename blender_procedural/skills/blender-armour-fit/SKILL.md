---
name: blender-armour-fit
description: Leva um asset de armadura ou roupa gerado por IA 3D (Tripo) até o encaixe num personagem MakeHuman com rig - separa as peças, recupera quads e otimiza, posiciona, proporciona por estilo (placa ou couro), mascara o corpo, faz skin e exporta GLB. A aprovação final é a validação do asset dentro do Godot. Use para encaixar, reencaixar ou ajustar proporção de armadura em personagem; não para modelar o asset nem para UV e textura.
---

# Blender Armour Fit

Encaixe de armaduras geradas por IA em personagens com rig, com as ferramentas de
`/home/ggnp/tools/blender_procedural`. Blender 5.2.2, sempre headless. Cada ferramenta tem
`README.pt.md`; leia o da ferramenta antes de mexer nela.

| Passo | Ferramenta | Faz |
|---|---|---|
| 1 | `armour_3d_split` | separa o GLB em peças nomeadas, com partição exata das faces |
| 2 | `armour_3d_decimate` | pares, metades espelhadas, tampas afundadas, orçamento do jogo |
| 3 | `armour_3d_fit` | posiciona, proporciona, mascara o corpo, faz skin, testa poses, exporta |
| 4 | `armour_3d_gates` | veredito por etapa e remédio para o que reprova |
| 5 | `armour_3d_pipeline --game` | instala as peças no projeto Godot e roda a auditoria dele |
| tudo | `armour_3d_pipeline` | encadeia os passos a partir de um arquivo de asset |
| medir | `armour_3d_diagnose` | mede um run pronto, sem alterá-lo |

O corpo KCC (malha, esqueleto, pesos e poses) está em `armour_3d_fit/fixtures/kcc_makehuman_52/`.
`armour/` e `armour_gen/` guardam só scripts de geração procedural antiga; não fazem parte deste fluxo.

## Roteiro: do GLB ao aceite

Sempre nesta ordem. Diga ao usuário em que passo o asset está.

1. **Entradas.** Confirme o que só o usuário dá (ver "O que o usuário decide"). Se o arquivo do
   asset foi substituído, o SHA-256 mudou: um plano de split escrito à mão deixa de valer.
2. **Arquivo do asset.** Copie um de `armour_3d_pipeline/assets/`, aponte `source`, deixe
   `split.plan` em `"auto"` e escolha o orçamento (`budgets.plate_arpg.json` para o jogo).
3. **Pipeline, pasta nova:**
   ```bash
   cd /home/ggnp/tools/blender_procedural
   python3 -m armour_3d_pipeline --asset armour_3d_pipeline/assets/<asset>.asset.json \
     --out outputs/armour_3d_pipeline/<run_novo> --autofix 3
   ```
4. **Leia os gates** (`<run>/4_gates/gates.json`, ou o que o comando imprime). Etapa reprovada:
   siga "Quando um gate reprova". Não siga adiante com gate em `FAIL` sem o usuário mandar.
5. **Gauntlet visual:** monte o pacote de evidência, abra você mesmo as imagens e passe pelo
   avaliador visual independente, em laço de até cinco tentativas (ver "Gauntlet visual"). Gerar
   imagem sem abrir não conta.
6. **Jogo:** o mesmo comando com `--resume --game` instala as peças (guardando as anteriores em
   `5_game/previous_pieces/`) e roda a auditoria do Godot.
7. **Capturas com o jogo rodando** (ver "No jogo"), de frente, de trás e pela câmera do jogo, parado
   e nos clipes de movimento. Abra as imagens.
8. **Aceite do usuário**, jogando. Só ele aprova.

Defeito achado nos passos 5, 7 ou 8 volta para a pipeline com uma medida (ver "Diagnóstico"), e o
roteiro recomeça do passo 3. Cinco voltas sem fechar acionam o redirecionamento do gauntlet visual.

## Etapas, ordem interna e gates

A ordem dentro de cada passo importa: várias correções só funcionam porque vêm depois de outra.

| Etapa | Passo | O que acontece, em ordem | Gates |
|---|---|---|---|
| 1 separação | 1 | componentes da malha → peças nomeadas pela posição (`"auto"`) ou por plano | `split_delivered` |
| 2 pares | 2 | luvas e botas: escolhe a melhor de cada par e espelha sobre a outra | `pairs_mirrored` |
| 3 metades | 2 | tronco e elmo: metade direita espelhada, antes e depois da redução | `halves_mirrored_mm`, `halves_closed` |
| 4 tampas | 2 | tampa das aberturas afundada a 50% do comprimento da peça | `lid_depth_share` |
| 5 orçamento | 2 | junção de quads (só em malha de quads) e redução até o teto da peça | `budget` |
| 6 encaixe | 3 | escala única do conjunto → posição pelos ossos → **leitura do desenho** (onde o asset termina cada peça) → comprimento pelo osso (`fit_length`) → inclinação da bota → giro das sub-partes → proporção (comprimento das sub-partes, depois largura) → assento → centro das bocas → gola → dedos | `handedness`, `length_as_designed_pct`, `length_*`, `mouth_off_centre_cm`, `collar_*`, `set_proportion`, `*_symmetry_*`, `shape_p95_pct` |
| 7 máscara, skin, poses, export | 3 | peças espelhadas recebem o espelho do encaixe → máscara do corpo → skin → poses → GLB por asset → reimportação | `fit_gates` (os cinco do fit) |
| 8 jogo | 5 | cópia das peças, importação, auditoria do Godot | auditoria `PASS` |

**Corpo é a régua, asset é o desenho.** O comprimento de cada peça num membro vem de onde o asset a
desenhou, levado às proporções do corpo; não é fixo no cotovelo nem no joelho. Com as peças postas
no corpo e nada redimensionado, o fit lê onde cai cada borda (100% = a articulação, 150% = metade do
osso seguinte) e decide: borda desenhada perto da articulação vai para ela; peças que se tocam no
asset continuam se tocando; o resto fica onde foi desenhado. Um conjunto com manga até o meio do
antebraço sai com manga até o meio do antebraço. Veja a leitura em `armour_3d_diagnose length`
(`drawn_%`, `design_%`, `told_%`) antes de julgar um comprimento.

Consequências da ordem que já custaram retrabalho:

- O **assento** vem depois da proporção e desloca a peça inteira. Onde a peça já foi centrada por
  conta própria (manopla), ele é desligado no slot (`seat` com limites zero).
- O **centro das bocas** e a **gola** vêm por último, para nada os tirar do lugar.
- A peça **dimensionada contra outra** (`relative_to`, o elmo contra o tronco) é encaixada depois
  da peça de referência.
- As **tampas** são afundadas depois de juntar os quads, e as faces do rebaixo (`a3d_recess`) ficam
  fora das medidas do encaixe.

Limites em `armour_3d_gates/gates.plate.json`: são os valores do run que o usuário aprovou
(`dedos_050`), com margem pequena. Só o usuário muda um limite.

## Quando um gate reprova

1. **Leia o gate**: ele traz a peça, a linha da medida e o número (`where`).
2. **Remédio automático** (`--autofix N`): existe para comprimento fora da articulação, boca fora
   do centro, pescoço através da gola, peça fora da proporção do desenho e peça do meio não
   espelhada. A pipeline remenda **cópias** dos perfis em `<run>/overrides/attempt_k/` e roda de
   novo. Se passar, `pipeline.json` → `promote` aponta os perfis que passaram; copie os remendos
   para o perfil do repositório e diga ao usuário o que mudou e por quê.
3. **Sem remédio, ou faixa esgotada**: a pipeline para e diz o que sobrou. Aí é ajuste à mão, até
   cinco tentativas; na quinta sem fechar, chame o redirecionamento do gauntlet visual:
   - meça com `armour_3d_diagnose` (tabela "sintoma → medida" no README dele);
   - procure o sintoma em "Sintoma → onde mexer";
   - mude **um** parâmetro no perfil;
   - rode só o encaixe: pasta nova com `1_split` e `2_decimate` copiados de um run bom, e
     `--resume`;
   - compare com `armour_3d_diagnose summary --run <novo> --against <referência>`;
   - se o número não mudou, **desfaça** a mudança antes de tentar outra.
4. **Runs de teste** vão para a lixeira (`gio trash`) quando não servem mais; o perfil volta ao
   estado anterior se o teste não foi adotado.
5. **Gate de poses**: peça comprida demais esconde pele que atravessa outra peça. Ao encurtar, o
   número pode subir sem o encaixe ter piorado. Diga o número e de onde vem (`summary` → piores
   poses, coluna `under`).

Nunca afrouxe um limite para passar. Se o run só serve com gate reprovado, quem decide é o usuário,
sabendo do número e da causa; registre a decisão e mantenha o `FAIL` em todo relato.

## O que os gates não veem

Conferência visual obrigatória, porque nenhuma medida pega:

- **Amassado local**, como a gola com entalhe atrás: olhe a gola de trás e de cima
  (`armour_3d_diagnose views` gera os closes de cada junta).
  `shape_p95_pct` só acusa deformação geral maior que a do run aprovado.
- **Tamanho que "parece errado"** quando o desenho não é o do asset: o gate `set_proportion` compara
  com o asset como veio ou com `relative_to`; se o usuário tem outra proporção em mente, ele precisa
  dizer os números.
- **Andar**: o clipe `Walking` não está nas poses medidas (fixture somente leitura). A evidência é
  a captura no jogo.
- **Orientação das manoplas**: o relatório não prova; confira em close que o dorso blindado está no
  dorso da mão e o polegar do lado de dentro, nas duas mãos.

## Gauntlet visual: o laço de avaliação

Os gates dizem se os números fecham; o gauntlet visual diz se a armadura **parece certa**. Ele vem
depois dos gates e antes do aceite do usuário, e é feito por um **avaliador visual independente**:
um agente novo, somente leitura, que não mexe em perfil, não roda a pipeline e não delega. Quem
corrige é a sessão que conduz o asset. Não substitui o aceite do usuário no jogo.

**Quando entra**

- **Revisão:** todos os gates por etapa em `PASS`. Uma revisão por tentativa.
- **Redirecionamento:** a quinta tentativa seguida sem `approved`, ou cinco tentativas de correção
  sem conseguir fechar os gates. O avaliador deixa de pedir retoques e dá outro rumo.

**Pacote de evidência** (uma pasta por tentativa, `<run>/6_visual/`; nunca reaproveite a de outra)

```bash
python3 -m armour_3d_diagnose views --run <run> --out <run>/6_visual/views      # closes das juntas
python3 -m armour_3d_diagnose summary --run <run> --against <run de referência> > <run>/6_visual/summary.txt
```

Mais `3_fit/fit_sheet.png`, `poke_sheet.png`, `worst_pose_sheet.png`, `4_gates/gates.json`, a
imagem de referência do asset quando o usuário deu uma, e as capturas do jogo quando o asset já
chegou lá. Abra você mesmo as imagens antes de mandar: pacote que você não olhou não vai.

**O laço**

1. Monte o pacote e chame o avaliador com o prompt de revisão. Um avaliador novo a cada tentativa;
   nunca mande o mesmo estado a dois avaliadores seguidos.
2. `approved`: siga para o jogo (`--game`), as capturas e o aceite do usuário.
3. `rework`: cada item vira primeiro um **número** (`armour_3d_diagnose`), depois **uma** mudança
   de parâmetro, depois um run novo com os gates passando (ver "Quando um gate reprova"). Só então
   nova tentativa. Item que você não conseguir medir volta ao avaliador como "não mensurável", não
   como resolvido.
4. Registre cada tentativa em `outputs/armour_3d_pipeline/gauntlet/<asset>.json`: número, run,
   veredito, itens pedidos, o que foi mudado e o que cada medida deu antes e depois.
5. Na **quinta** tentativa sem `approved`, não tente a sexta: chame o avaliador com o prompt de
   redirecionamento, levando o registro das cinco.
6. O redirecionamento abre **um** ciclo novo de até cinco tentativas no rumo indicado. Se esse
   ciclo também acabar sem `approved`, pare e leve ao usuário o registro inteiro, com o que foi
   tentado e a recomendação do avaliador. Não há terceiro ciclo sem o usuário.

O avaliador não muda limite de gate nem aprova gate reprovado. Se ele recomendar aceitar uma
limitação, pedir outro asset à Tripo ou mudar um limite, isso vai ao usuário como recomendação.

**Prompt de revisão** (preencha o que está entre `<>`)

```text
Você é o avaliador visual desta tentativa de encaixe de armadura. Avalie diretamente, sem delegar,
sem criar agentes e sem editar nenhum arquivo. Devolva o veredito à sessão principal.

Asset: <nome>. Estilo: <plate|leather>. Tentativa <n> de 5 deste ciclo.
Corpo: personagem de 2 m, muito musculoso de propósito (caso extremo).
Pacote: <run>/6_visual/ (closes em views/, folhas do encaixe, summary.txt, gates.json<, referência,
capturas do jogo>). Todos os gates numéricos passaram; não os reavalie, procure o que número não vê.
Decisões do usuário que não são defeito: <copie de "Decisões vigentes" e "Estado atual" o que se
aplica: peças terminam na articulação, pele à vista no joelho e no cotovelo, elmo na proporção do
desenho, simetria por espelho, tampas afundadas, triângulos>.
Tentativas anteriores deste ciclo: <itens pedidos e o que mudou, ou "nenhuma">.

Abra todas as imagens. Para cada peça (elmo, tronco, manoplas, botas) e cada junta (gola, ombros,
cotovelos, punhos, quadris, joelhos), responda:
1. Proporção: a peça tem tamanho coerente com o corpo e com as outras peças?
2. Forma: há amassado, entalhe, dobra, espinho, torção ou placa esticada para um lado só?
3. Bocas: o membro passa pelo meio da abertura? A borda é lisa e fecha em volta dele?
4. Simetria: esquerda e direita são iguais onde deviam ser?
5. Encontro entre peças: alguma atravessa outra, flutua longe do corpo ou deixa pele onde não devia?
6. Poses: nas folhas de pose, alguma peça sai do membro, vira ou deixa o corpo atravessar à vista?
7. Regressão: algo que estava certo na tentativa anterior piorou?

Devolva só este JSON:
{"verdict": "approved" | "rework" | "blocked",
 "seen": ["arquivos que você abriu"],
 "findings": [{"where": "peça ou junta", "view": "arquivo", "what": "o que se vê, em uma frase",
               "severity": "blocker" | "minor",
               "measure": "qual medida do armour_3d_diagnose deve mostrar isso, ou 'não mensurável'",
               "accept_when": "o que precisa estar visível ou medido para o item fechar"}],
 "kept_right": ["o que está bom e não deve ser mexido"]}

Regras: "approved" só sem nenhum achado "blocker". Não peça o que contraria uma decisão do usuário
listada acima; se discordar dela, registre em findings com severity "minor" e diga que é do usuário.
Não proponha valores de parâmetro: descreva o defeito e como reconhecer que sumiu. "blocked" só
quando o pacote não permite avaliar (imagem faltando, peça fora de quadro); diga o que falta.
```

**Prompt de redirecionamento**

```text
Você é o avaliador visual chamado para redirecionar um encaixe de armadura que não fechou em cinco
tentativas. Avalie diretamente, sem delegar, sem criar agentes e sem editar nenhum arquivo.

Asset: <nome>. Estilo: <plate|leather>. Motivo: <cinco revisões sem approved | cinco correções sem
fechar os gates: liste os gates e os números>.
Registro das tentativas: outputs/armour_3d_pipeline/gauntlet/<asset>.json (itens pedidos, o que foi
mudado, medidas antes e depois). Pacote da última tentativa: <run>/6_visual/.
Decisões do usuário em vigor: <as mesmas do prompt de revisão>.
O que já foi tentado e rejeitado nesta pipeline: <copie "Tentado e rejeitado" de "Decisões vigentes">.

Não repita nenhum pedido das cinco tentativas. Leia o registro e responda:
1. O que as cinco tentativas têm em comum: o mesmo defeito voltando, defeitos trocando de lugar
   (corrigir um piora outro), ou um defeito que nenhuma mudança moveu?
2. A causa mais provável está no asset (forma que o encaixe não conserta), na regra do perfil (a
   abordagem, não o valor), na medida ou no limite (o número não corresponde ao que se vê), ou no
   pedido (dois requisitos que não cabem juntos)?
3. Qual é o outro rumo? Escolha um e justifique com o que está no registro:
   - "other_rule": outra abordagem no perfil para a peça ou junta, descrita pelo efeito visual;
   - "accept_limitation": o estado atual serve, com a limitação descrita para o usuário decidir;
   - "new_asset": pedir outro asset à Tripo, dizendo o que precisa vir diferente;
   - "change_requirement": um requisito ou limite precisa ser revisto pelo usuário; diga qual e por quê;
   - "stop": nada disso; diga o que falta saber.

Devolva só este JSON:
{"pattern": "o que as tentativas têm em comum",
 "cause": "asset" | "rule" | "measure_or_limit" | "request",
 "direction": "other_rule" | "accept_limitation" | "new_asset" | "change_requirement" | "stop",
 "what_to_do": "o novo rumo, em até cinco frases, pelo efeito visual esperado",
 "do_not_repeat": ["o que não adianta tentar de novo"],
 "needs_user": true | false,
 "first_check": "o que olhar ou medir primeiro para saber se o novo rumo está funcionando"}

"needs_user" é true para accept_limitation, new_asset, change_requirement e stop. Você não aprova
gate reprovado nem muda limite: recomenda.
```

## O que o usuário decide

Pergunte se não estiver dito; não invente.

- **Orçamento** de triângulos por peça (tabela abaixo) e se o jogo aceita triângulos. Decisão
  vigente (2026-10-07): **triângulos são aceitos** na versão ARPG; não há gate de quads nela.
- **Proporções do desenho** quando diferem do asset (ex.: elmo com 28,736% da largura e 29,51% da
  altura do tronco).
- **Limites dos gates.** Vigentes: os do run aprovado.
- **Simetria:** espelhar sempre (pares e metades). Perde-se o que só existia de um lado.
- **Onde as peças terminam:** onde o asset desenha (`fraction: "design"`). A parte em contato com o
  corpo pode terminar na articulação. Se o usuário quiser outra coisa para um asset (cobrir até o meio
  da canela, parar antes do joelho), ele diz e a regra recebe o número.
- **O aceite**, no jogo.

## Aprovação final: no Godot

**Um asset só está aprovado depois de validado no jogo** (decisão do usuário, 2026-10-07). Os gates
da pipeline passarem é condição para chegar lá, não a aprovação: já houve defeito que render em
repouso e gate de poses não mostravam e que aparecia no jogo ao primeiro passo (panturrilha saindo
da bota, bota saindo da perna ao andar, manopla torta).

Ordem, sempre até o fim:

1. Pipeline com todos os gates por etapa em `PASS`. Com gate em `FAIL` só segue se o usuário mandar,
   sabendo do número e da causa; registre a decisão e mantenha o `FAIL` em todo relato.
2. `--game`: peças no projeto Godot (as anteriores ficam em `<run>/5_game/previous_pieces/`).
3. `tests/medieval_knight_armour_audit.gd` em `PASS` (montagem, repouso, poses, máscara, vestir e tirar).
4. Olhar a armadura **no motor, com o jogo rodando**: capturas ao vivo de frente, de trás e pela
   câmera do jogo, parado e nos clipes de movimento (andar e correr no mínimo). Abra as imagens;
   gerar sem olhar não conta.
5. O usuário testa jogando e dá o aceite.

Não diga que um asset está pronto, aprovado ou entregue antes do passo 4, e diga sempre em que
passo ele está. Se o passo 4 ou 5 achar defeito, ele volta para a pipeline com a medida do defeito
(ver "Quando um gate reprova"), e a validação recomeça do passo 1. Comandos em "No jogo (Godot KCC)".

## Decisões vigentes (todas do usuário, 2026-10-07)

Valem para placa no catálogo `slots.plate_assembled.json`. Não desfaça nenhuma sem o usuário pedir.

| Peça | Como é dimensionada | Outras regras |
|---|---|---|
| conjunto | uma escala só, pela altura do corpo (`uniform_set_scale`) | lado direito é a referência; o esquerdo repete o ajuste espelhado (`symmetry_reference`) |
| `Helmet` | fator único em todas as direções, a partir do pescoço, dado pela proporção planejada contra o tronco (`relative_to`: 28,736% da largura, 29,51% da altura), não pela cabeça | malha espelhada da metade direita (`symmetrize`); assento só no plano do meio (`seat.midline`), sem girar; tampa afundada |
| `Suit` | ajuste completo, seção por seção (`shape: "flexible"`); mangas e coxas acabam onde o asset as desenha, medido no osso do corpo (`lengthen: {"fraction": "design"}`; neste asset, cotovelo e joelho); tronco cresce pelo fator (`lengthen: true`) | malha espelhada da metade direita (`symmetrize`); gola fechada e centrada no pescoço (`collar`); perna no centro da boca de cada cano (`centre`); `seat.midline`; ponta da manga segue o antebraço |
| `Gloves` | mão: só a escala do conjunto (`isotropic_max: 0`); canhão (`cuff`): largura seção por seção e centrado no antebraço, sem assento final | melhor das duas espelhada; eixo medido na malha (`piece_axis_from`); dedos articulados um a um; tampa afundada |
| `Boots` | ajuste completo (`shape: "flexible"`, `gap_m` 1,5 cm, `max_out_m` 5 cm) | melhor das duas espelhada; acaba onde o asset a desenha (`fit_length` com `fraction: "design"` e `always`; neste asset, no joelho); sola no chão; tampa afundada |

- **Corpo de referência** é um caso extremo de propósito (muito musculoso): o que couber nele deve
  caber nos outros.
- **Entrega em assets distintos:** `Helmet`, `Suit`, `Gloves` (as duas manoplas num GLB) e `Boots`
  (as duas botas num GLB), cada um com a sua máscara de corpo, em `<run>/3_fit/pieces/`.
- **Pares** (`mirror_pairs`): olhe `2_decimate/pair_*.png` e `report.json` → `pairs`. Com as duas
  metades limpas, a escolha automática é por desempate fraco; diga isso.
- **Tampas** (`recess_lids`): malha fechada, tampa afundada até metade do comprimento da peça.
  Detalhes de borda somem na texturização; não refine além disso.
- **Elmo**: o tamanho não sai da cabeça (pedia fator 1,33 e o usuário reprovou). Vale a proporção do
  desenho contra o tronco encaixado; a cabeça inteira é mascarada, então ela atravessar por dentro
  não aparece. O pescoço fica à vista abaixo do elmo.
- **Manopla**: a mão fica só com a escala do conjunto; o canhão é a sub-parte `cuff.<lado>`, com
  largura seção por seção e boca centrada na carne do antebraço. Encurtar o canhão (88–96%) piora:
  sobra antebraço nu que entra no peitoral nos strafes.
- **Gola**: no ajuste do tronco ela só pode ser empurrada para fora (puxada, copiava a cavidade
  entre as omoplatas e ficava amassada atrás); o passo `collar` a fecha em funil e a centra.
- **Comprimento vem do osso, nunca do fator de largura**: num corpo musculoso o fator é grande e
  empurra a peça para além da articulação.
- **Simetria** é cobrada de perto. O tronco espelhado perde o que só existia num lado (fivela, bolsa).

Tentado e rejeitado (não repita): remover a tampa e criar lábio interno (sem acabamento na manopla);
bota em cone, com fatores no tornozelo, panturrilha e joelho (ficava achatada); alinhar o cano da
calça inteiro ou pela caixa da seção (12–20° de giro, cinto deformado); nenhuma peça redimensionada
(`keep_shape`: 21,7% de pele escondida); pôr o pescoço na medida do tronco para fechar a gola; tirar a
gola do ajuste do tronco (a base afunda 1,4–2,6 cm nos trapézios); dimensionar a gola pela base (fator
1,37, larga demais); dimensionar o elmo pela cabeça; encurtar o canhão da manopla.

## Sintoma → onde mexer

Tudo abaixo aconteceu no baseline; a correção está no slot indicado de `slots.plate_assembled.json`.

| Sintoma | Causa | Correção |
|---|---|---|
| manoplas nas mãos trocadas | lado medido pela forma (polegar) errou | `palm.handedness_from: "layout"` |
| manopla virada (dorso para fora ou para a palma) | `palm.piece_back` não é o lado do dorso no asset | olhe o asset cru em close e declare o eixo certo; no baseline é `[0,-1,0]` (dorso para a frente) |
| manopla subida ou descida no braço | `piece_fraction` não cai no punho | `{"from_finger_roots": true, "fallback": f}`: o punho sai das raízes dos dedos; o número só vale para dedos fundidos |
| canhão da manopla furado pelo braço ao dobrar o cotovelo | canhão mais comprido que o antebraço | `fit_length` até o cotovelo; hoje só age com `"always": true`, porque a manopla não muda de forma |
| mão da manopla apontando para o lado errado do pulso | mão segue o antebraço | sub-parte `hand.*` com `"fit": false` e `from_bones` |
| dedos da manopla fora dos dedos do corpo | palma de outro tamanho | a manopla não é redimensionada (decisão vigente), então sobra 1,5–3,7 cm; o asset precisa de mão do tamanho certo |
| dedos em degrau, descolados da palma | dedo levado até o osso, com palma mais grossa que a mão | o dedo fica na própria raiz e só toma direção e dobras do osso (é o que o código faz; não volte atrás) |
| mão vira garra fina e comprida | palma escalada demais | não escalar a mão pelo comprimento total; os dedos do corpo em repouso são curvos e enganam a medida |
| botas enormes | tampas nas aberturas e canela inclinada enganam a medida | já tratado no núcleo; confira `proportion.body` só com a canela e `max_out_m` |
| botas tortas, afundadas ou com o pé virado para fora | assento livre e pé do asset aberto | `yaw_to`, `lean` e `seat` com `rotation_deg: 0` e `lock_z` |
| vão entre coxa e bota, ou manga que não chega ao cotovelo | o ajuste completo só alarga em volta do corpo | `lengthen` na proporção: `true` no tronco (fator único), `{"fraction": "design"}` nas mangas e coxas (comprimento medido no osso do corpo, onde o asset o desenha); não volte a dar escala própria ao tronco |
| manga passa do cotovelo, placa da coxa passa do joelho | comprimento vinha do fator de largura, que num corpo muito musculoso é grande | `"lengthen": {"fraction": "design"}` na sub-parte; confira com `armour_3d_diagnose length` |
| peça cortada na articulação num asset desenhado para ir além dela (ou o contrário) | `fraction` com número fixo, ignorando o desenho | `"fraction": "design"` com `beyond_bone` e `meets`; o gate `length_as_designed_pct` acusa e o `--autofix` troca |
| asset desenhado até o meio do membro sai curto | a peça precisaria esticar mais que `max_factor` (1,5) | `length_short_cm` acusa; é limite de deformação da placa: decisão do usuário ou outro asset |
| coxa ou manga torcida em torno do próprio eixo | otimizador achou mínimo falso | `twist_weight` da sub-parte (padrão já penaliza) |
| elmo tombado para a frente | assento girou o elmo | `seat.rotation_deg: 0` no `helmet` |
| braço da armadura dentro do tronco numa pose | a animação já faz isso no corpo nu | corrigir o clipe no jogo, não o encaixe |
| dedo virando espinho numa pose | pontas de dedos fundidos esticadas ao longo do dedo inteiro | `min_length_fraction` barra isso: dedo fundido herda pesos e fecha em bloco |
| pele rasgada na raiz dos dedos ao fechar a mão | pesos mudam de uma vez na raiz | `root_smooth_iterations` e `blend_m` em `fingers` |
| fração de quads abaixo do mínimo | ângulo de junção apertado para placas curvas, ou tampa afundada antes de juntar os pares | `join_*_angle_deg` (60° no baseline); o passo 2 já junta antes de afundar; não baixe `min_quad_face_share` |
| manopla torta, canhão fora do braço, dedos ao lado dos dedos do corpo | asset em pose A e slot assumindo a peça na vertical | `"piece_axis_from": "mesh"` no `place`; confira `placement.cuff_line` |
| pele do antebraço com o `Suit` por baixo em poses de braço dobrado | manga passa do cotovelo e está presa só ao braço | `forearm.L/R` em `skin_bones` do `suit` |
| bota saindo da perna ao andar | topo passa do joelho e é rígido com a canela | `fit_length` até o joelho com `"always": true` |
| pele aparecendo no jogo onde o render em repouso está limpo | folga pequena demais naquele trecho | meça a distância pele–placa por faixa de altura e lado, depois aumente `gap_m` do slot (bota: 1,5 cm) |
| perna fora do centro do cano da calça | cano e coxa não alinhados | `"centre": true` na sub-parte; confira `centred_parts` |
| um lado "puxando mais" que o outro | malha assimétrica, ou assento deslocando para o lado | `symmetrize` no passo 2 (`mirror_error_m` zero), `seat.midline`, balanço igual das pernas no report |
| gola amassada atrás, com entalhe no meio das costas | o ajuste do tronco puxa a gola para a cavidade entre as omoplatas | já tratado no código: na gola o tronco só empurra para fora; confira com vistas de trás e de cima no Blender e `armour_3d_diagnose collar`. Não tire a gola do ajuste do tronco (a base afunda nos trapézios) |
| gola aberta, longe do pescoço | o ajuste por seções enxerga o trapézio junto com o pescoço | regra `collar` do slot (não adianta pôr o pescoço em `proportion.body`) |
| elmo grande demais para o corpo | tamanho tirado da cabeça escondida, não do desenho | `relative_to` na `proportion` do elmo (o gate `set_proportion` acusa e o `--autofix` escreve a proporção do asset) |
| antebraço atravessando o canhão da manopla, até parado | canhão estreito perto do cotovelo e fora do centro da carne | sub-parte `cuff.<lado>` com `shape: "flexible"` e `"centre": true`; `seat` do slot com limites zero |
| quatro peças paradas num teto depois de afundar tampas | fundo e parede do rebaixo lidos como parede interna | o grupo `a3d_recess` tem de chegar ao fit; ele exclui essas faces |

## O que conferir sempre

- **Manoplas:** `report.json` → `handedness`. O lado vem da posição da peça no conjunto (em `+X`
  é a esquerda); a medida pela forma (polegar) já errou e trocou as duas. O relatório não prova a
  orientação: confira sempre em close, com o corpo visível e sem máscara, que o dorso blindado
  está no dorso da mão e o polegar do lado de dentro, nas duas mãos.
- **Pés:** a bota aponta para onde o pé do corpo aponta, com a sola no chão, e segue a canela
  por cisalhamento (`lean`), não por rotação. Bota torta ou afundada indica `seat` solto.
- **Peças infladas:** as peças da Tripo têm tampas nas aberturas. Peça que cresce mais de ~40%
  em largura quase sempre é erro de medida (tampa, membro inclinado), não corpo largo.
- **Proporção:** a escala base é uma só para o conjunto inteiro, tronco incluído. Peça grande ou
  pequena demais se corrige na regra do slot, não aumentando limites. Fator perto de
  `isotropic_max` (0,45) quer dizer armadura fina demais para o corpo: avise.
- **Mangas, coxas e ombreiras:** precisam girar para o membro (`parts` do slot). Membro
  atravessando a peça indica segmentação da sub-parte errada, não falta de escala. Rotação em
  torno do eixo vertical numa perna é torção, e está errada.
- **Vista de costas e de lado:** glúteo, tríceps e panturrilha são onde o corpo aparece. Olhe
  `poke_sheet.png`, que mostra em vermelho a pele que a máscara esconde.
- **Dedos:** `report.json` → `fingers`. `articulated: true` só sai quando os quatro dedos são
  tubos inteiros na malha; com dedos fundidos vem `false` e o motivo, e a mão fecha em bloco.
  Olhe sempre três situações em close: repouso, empunhadura (`GreatSwordAttack`) e corrida, e
  meça a aresta mais esticada da manopla nas poses (até ~5 cm no baseline). Dedos articulados
  fecham em garra um pouco aberta: é limitação conhecida, diga.
- **Poses:** o gate conta só a pele que atravessa **e fica à vista**; a coberta por outra placa
  vai em `poke_area_incl_covered_cm2`. Cada pose traz a área por osso e por peça
  (`area_cm2_by_piece`, com o osso que move a placa). No baseline: 134 cm² (um salto, coxas).
  Diga os números e a pose.
- **Braço dentro do tronco numa pose:** antes de culpar o encaixe, pose o corpo sem armadura.
  Nos clipes de strafe o corpo nu já tem os antebraços dentro do tronco: é da animação.

## Diagnóstico: meça antes de mexer

```bash
python3 -m armour_3d_diagnose summary --run <run> [--against <run anterior>]
python3 -m armour_3d_diagnose length|proportion|shape|clearance|enclosed|alignment|reach|gloves|collar|weights|symmetry --run <run> [--piece NOME] [--bands N]
python3 -m armour_3d_diagnose all --run <run> --out <run>/diagnose.json
python3 -m armour_3d_diagnose views --run <run> --out <pasta nova>      # closes das juntas, para olhar
```

Não altera o run. `armour_3d_diagnose/README.pt.md` tem a tabela "sintoma → medida" e as leituras
que enganam. Regra: defeito visto no jogo vira primeiro um número de uma dessas medidas; só então
se mexe no perfil, e a mesma medida confirma depois. Não escreva script solto de medição no `/tmp`:
se faltar uma medida, acrescente-a em `checks.py`.

## Cada ferramenta sozinha

Use a pipeline no dia a dia. As ferramentas avulsas servem para inspecionar um asset novo ou
repetir um passo com outra configuração. Toda saída vai para uma pasta nova.

```bash
# 1a. inventário das ilhas do GLB (gera inventory.json e plan.template.json)
python3 -m armour_3d_split inspect --input <glb> --out <pasta>
# 1b. separação pelo plano; "backup" aceita desvio de normais medido, "strict" recusa
python3 -m armour_3d_split split --input <glb> --plan <plano.json> --normal-policy backup --out <pasta>
# 2. pares, metades, tampas e orçamento, sobre o .blend do passo 1 (budgets.plate_quads.json para malha que já vem em quads)
blender -b --factory-startup --python-exit-code 2 -P armour_3d_decimate/decimate.py -- \
  --input <1_split>/armour_split.blend --budgets armour_3d_decimate/budgets.plate_arpg.json --out <pasta>
# 3. encaixe, a partir de um job (a pipeline grava o dela em <run>/fit.job.json)
python3 -m armour_3d_fit --job <job.json> --style plate --out <pasta>
# 4. gates por etapa de um run pronto
python3 -m armour_3d_gates --run <run>
# tudo, parando num passo ou retomando um run
python3 -m armour_3d_pipeline --asset <asset.json> --out <run> [--until split|decimate|fit|gates] [--resume] [--autofix N] [--game]
```

| Arquivo que você edita | Controla |
|---|---|
| `armour_3d_split/examples/*.plan.json` | qual ilha do GLB é qual peça (só quando `"auto"` não serve) |
| `armour_3d_decimate/budgets.*.json` | orçamento por peça, quads, gates da otimização |
| `armour_3d_fit/profiles/slots.*.json` | como cada peça é posta, dobrada, proporcionada e skinada |
| `armour_3d_fit/profiles/body.*.json` | nomes dos ossos e grupos de ossos do personagem |
| `armour_3d_gates/gates.*.json` | limites dos gates por etapa e faixas dos remédios (só o usuário muda limite) |
| `armour_3d_pipeline/assets/*.asset.json` | junta os acima para um asset, e diz onde fica o projeto do jogo |

Para repetir só o encaixe depois de mexer num perfil, copie `<run>/fit.job.json`, rode o passo 3
numa pasta nova e, para olhar um clipe só, ponha `"config": {"pose_clips": ["Jump"]}` no job.

## O que pedir à Tripo

Armadura montada de pé, de frente (braços caídos ou em A, os dois são tratados); malha em quads já
perto da contagem final do orçamento; GLB; peças sem se tocar; manoplas com a mão aberta, dedos
separados até a base e do tamanho de uma mão proporcional ao corpo; boca do canhão da manopla
simples, sem placas em espiral; armadura larga o bastante para o corpo (coxas e panturrilhas grossas
no corpo de referência). Asset fora disso encaixa pior e não se conserta no encaixe.
Se o usuário substituir o arquivo do asset, confira o SHA-256: o plano do split deixa de valer.

## Orçamento do jogo (ARPG top-down / isométrico)

Definido pelo usuário. É o limite da versão final de jogo (low-poly); o `armour_3d_decimate` deve
levar cada peça a ele. Os números de `budgets.*.json` saem desta tabela, e nenhum valor é
inventado pela sessão: sem número aqui, pergunte.

| Peça | Triângulos | Quads | Regra de topologia |
|---|---:|---:|---|
| Helmet | 3.000 – 4.500 | ≥ 85% | Geometria só nas bordas da silhueta vistas de cima; detalhe fino vai para o normal map |
| Suit | 12.000 – 16.000 | ≥ 85% | Três ou mais edge loops de sustentação nas articulações: cintura, ombros, cotovelos, joelhos |
| Glove_L | 1.200 – 1.800 | ≥ 90% | Quads limpos para deformar no punho e nos dedos nos ataques |
| Glove_R | 1.200 – 1.800 | ≥ 90% | Simetria topológica e UV espelhada com a esquerda (salvo modelo assimétrico) |
| Boot_L | 1.500 – 2.200 | ≥ 85% | Simplificar a sola e as áreas baixas que a câmera isométrica não mostra |
| Boot_R | 1.500 – 2.200 | ≥ 85% | Simetria topológica e UV espelhada com a esquerda |
| **Total** | **20.400 – 28.500** | **≥ 85%** | Teto da malha do personagem na versão final |

- **Quads na versão ARPG: dispensados** (decisão do usuário, 2026-10-07: aceitar triângulos). A coluna
  de quads da tabela vale para uma versão em quads; com `budgets.plate_arpg.json` não há gate de quads
  e o resultado sai com 0–2% de quads. Diga isso quando reportar a topologia.
- O teto de cada peça é o limite superior da faixa; o total não passa de 28.500. Abaixo do mínimo
  da faixa também é desvio: reporte.
- Quads: `min_quad_face_share` do arquivo de orçamento recebe o valor da tabela (0,85 ou 0,90 por
  peça). Não baixe esse número para fazer um asset passar.
- Os arquivos `budgets.*.json` anteriores (Suit 60.000, botas 13.000, luvas e capacete 8.000)
  são de uma rodada antiga, de 5 a 10 vezes acima desta tabela, e não valem como orçamento do jogo.
- UV espelhada e normal map são do passo de UV e bake, fora desta pipeline. Aqui só a topologia
  e a contagem são verificadas.
- Conflito conhecido: reduzir uma malha de triângulos até esta faixa deixa 40–55% de quads, abaixo
  de 85%. Atingir os dois números exige malha da Tripo já em quads e perto da contagem final
  (peça à Tripo menos faces) ou retopologia. Se a malha vier fora disso, o passo 2 reprova; o
  resultado vai ao usuário, que decide entre nova geração, retopologia ou outro limite.

## Estilo

Pergunte ou deduza do pedido que tipo de peça é. O estilo muda o resultado mais que qualquer outro parâmetro.

- `plate`: placas rígidas. O conjunto recebe uma escala só; cada peça é dimensionada como diz a
  tabela de "Decisões vigentes" (fator único, ou ajuste completo em bota e tronco). O corpo que
  encosta na placa é escondido pela máscara.
- `leather`: couro, jaqueta, calça. Cada seção transversal acompanha o corpo; deforma à vontade.
- Robe ou vestido: não existe. Diga isso; não improvise com `leather`.

Bota, tronco e canhão da manopla usam o ajuste completo por decisão do usuário, então acompanham o
corpo mais de perto que o resto. Em outra peça de placa, contornar o corpo é sinal de configuração errada.

## Quads

- GLB não guarda quads. Malha de quads da Tripo chega em pares de triângulos; o passo 2 junta
  os pares (`budgets.plate_quads.json`). O `.blend` final tem quads, o `.glb` final não.
- Reduzir a contagem destrói os quads (sobram 40–55%). Com `"reduce": "over_budget"` o
  orçamento é um teto e a peça que cabe fica intacta. Se a malha vier pesada demais, o
  certo é gerar de novo na Tripo com menos faces, não decimar.
- Não diga que um asset "é de quads" sem olhar `quad_face_share` no `report.json` do passo 2.

## No jogo (Godot KCC)

Projeto `/home/ggnp/Games/OpenKCC-Locomotion` (Godot 4.7.2, sem git: faça cópia `.before_*` antes de
editar uma cena). Copie `<run>/3_fit/pieces/*` para `assets/medieval_knight/pieces/`.
`scripts/medieval_knight/knight_armour_equipment.gd` veste e tira asset por asset no esqueleto do
jogador (binds refeitos a partir do repouso do host, por nome de osso) e refaz a malha do corpo sem
as faces escondidas pelo que está vestido; `knight_armour_pickup.gd` é o item no chão
(`asset_name`), quatro na cena `locomotion_lab.tscn`.

```bash
cd /home/ggnp/Games/OpenKCC-Locomotion && godot --headless --import
godot --headless -s tests/medieval_knight_armour_audit.gd          # montagem, repouso, poses, máscara
godot -s tests/medieval_knight_armour_capture.gd -- --output-dir <pasta>   # imagens; precisa de tela
```

O script de captura fixa a orientação do personagem antes de fotografar (o olhar segue o mouse); uma
captura feita à mão sem isso sai com o personagem virado e não serve para comparar lados.

## Regras

- Os arquivos de entrada nunca são salvos. Uma saída nunca é sobrescrita.
- Não corrija a malha de entrada para destravar um passo. Normais zeradas em "bolsos planos"
  da Tripo já são tratadas pelo split; outra normal inválida bloqueia de propósito.
- O passo 2 não usa solda de vértices: nessas malhas ela cria arestas não-manifold.
- Não afrouxe um gate para obter PASS. Limite só muda com autorização de quem pediu o trabalho;
  quando mudar, diga qual, de quanto para quanto e por quê.
- Uma medição nova entra em `armour_3d_diagnose/checks.py`; um gate novo, em `armour_3d_gates` com
  limite no arquivo de limites e teste em `armour_3d_gates/tests/`. Nada de script solto.
- Gates em `PASS` não são aprovação: o aceite é no Godot, com o jogo rodando (ver "Aprovação final").
- Reporte `FAIL` como `FAIL`, com o gate e o número. Render limpo com máscara não prova que o
  corpo não atravessa: diga quanto de pele a máscara está escondendo.
- Personagem novo: perfil em `armour_3d_fit/profiles/body.*.json`. Família de armadura com outra
  anatomia (calça, mangas longas, capa): catálogo novo em `profiles/slots.*.json`. Não edite o
  catálogo de outra família para servir.
- Parâmetro novo entra no perfil ou no `config`, com teste em `armour_3d_fit/tests/` quando for
  do núcleo numérico (`core.py` não importa `bpy`). Nenhum número, nome de osso, nome de peça ou eixo
  fica solto no código: vai para a regra do slot, para o arquivo de orçamento, para o perfil do corpo
  (`mirror`) ou para um bloco `*_DEFAULTS` nomeado e comentado.

## Gates do próprio fit (etapa 7)

| Gate | Limite padrão |
|---|---|
| casca externa dentro de corpo exposto | 25 cm² |
| pele escondida por atravessar a armadura | 8% do corpo em `plate`, 5% em `leather` |
| peça parada num teto de translação ou rotação | nenhuma |
| pele à vista atravessando na pior pose | 150 cm² |
| GLB reimportado com peças, triângulos, esqueleto e skin | íntegro |

## Fora de escopo

UV, textura, bake de normal map e modelagem ou reparo do asset. A aprovação do encaixe é a
validação no Godot descrita em "Aprovação final: no Godot", não uma revisão de render. Para
aprovar a modelagem de um asset (forma, topologia, textura) use `blender-framework` e
`blender-designer-review`.

## Verificação

```bash
python3 -m unittest discover -s armour_3d_split/tests -p 'test_*.py'
python3 -m unittest discover -s armour_3d_fit/tests -p 'test_*.py'
python3 -m unittest discover -s armour_3d_pipeline/tests -p 'test_*.py'
python3 -m unittest discover -s armour_3d_diagnose/tests -p 'test_*.py'
python3 -m unittest discover -s armour_3d_gates/tests -p 'test_*.py'
python3 armour_3d_split/tests/cli_integration.py <pasta_vazia>
```

O `armour_3d_decimate` não tem testes próprios; é verificado pelos gates do seu `report.json`.
Depois de mexer em qualquer ferramenta, rode o baseline pela pipeline numa pasta nova: os gates por
etapa têm de passar e os GLBs de `3_fit/pieces/` têm de sair iguais aos de `dedos_053` (mesmo
SHA-256) quando a mudança não devia alterar o resultado.

## Estado atual

- **Baseline:** `medieval_knight_dedos` (mãos abertas, dedos separados; GLB em
  `/home/ggnp/3d_assets/`, malha de triângulos). Run de referência
  `outputs/armour_3d_pipeline/dedos_053`: todos os gates por etapa passam, 28.204 triângulos, 2,1% de
  pele escondida, pior pose 134 cm². Saída idêntica à do `dedos_050`, que está no jogo.
- **Defeitos conhecidos do baseline, dentro dos limites aprovados:** pescoço 0,9 cm através da borda
  da gola na frente; canhão da manopla 1,4 cm além do cotovelo; coxa atravessando a placa no salto
  (até 65 cm²); braço encostando no peitoral nos strafes; pele à vista no joelho e no cotovelo (o
  asset não tem joelheira nem cotoveleira; aceita pelo usuário).
- **`medieval_knight`** (dedos fundidos, malha de quads; run `knight_024`) **reprova** nas etapas 3,
  6 e 7 e o `--autofix` não resolve: tronco espelhado com 632 arestas abertas, manga 7 cm além do
  cotovelo mesmo com a regra em 85%, pior pose 202 cm², GLB não reimporta íntegro. Não está
  resolvido; diga isso.
- **A generalização não está provada:** só um asset passa. O usuário vai trazer mais.
- **Leitura do desenho** só foi exercida num asset que resolve tudo para a articulação. O caso
  "além da articulação" foi testado forçando o número (mangas a 150% chegaram ao meio do antebraço e
  encontraram a manopla curta); falta um asset de verdade desenhado assim.
- **Pendências:** região abaixo da axila; bocas ainda não centradas (saída do braço no peitoral,
  abertura do elmo no pescoço); clipe `Walking` fora das poses medidas; `armour_3d_decimate` sem
  testes próprios; couro (`leather`) sem roteiro validado.
