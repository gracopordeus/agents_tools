# Sprite Lab — catálogo semântico

Esta é a interface canônica do Sprite Lab. Ela vive em `tools/generation` e
usa os manifests gerados em `tools/source-assets/catalog` como fonte física de
proveniência. O fluxo de análise é separado do gerador de spritesheets:

O fluxo de geração condicionado por referências está documentado em
[`POC_REFERENCE_CONDITIONING.md`](POC_REFERENCE_CONDITIONING.md). Ele conecta
exportação 3D, canais estruturais, adaptadores de modelos de imagem,
normalização, spritesheet e métricas sem depender de um mecanismo específico.

```text
assets.json + animations.json
        │
        ├── relationships.json       receita de mesh + Action + componentes
        ├── semantic_annotations.json nomes, tags, família e notas
        ├── action_annotations.json   análise semântica das Actions
        ├── composition-exports/      GLBs compostos salvos pelo usuário
        └── sprite-renders/           células + spritesheet + asset_manifest.json
```

## Executar a página

Na máquina local (o launcher de desenvolvimento seleciona automaticamente o
ambiente virtual quando ele existir):

```bash
python3 /home/ggnp/tools/generation/sprite-lab/server.py --host 127.0.0.1 --port 8002
```

Durante o desenvolvimento, use `dev_server.py` no lugar de `server.py` para
reiniciar automaticamente o servidor quando HTML, CSS, JavaScript ou Python
forem alterados:

```bash
python3 /home/ggnp/tools/generation/sprite-lab/dev_server.py --host 127.0.0.1 --port 8002
```

Se existir o ambiente virtual `.venv` do Sprite Lab, o launcher o utiliza
automaticamente para disponibilizar os SDKs opcionais, como `openai`.

Abra <http://127.0.0.1:8002/catalog>. As páginas também estão disponíveis em
`/composition` e `/sprites`, permitindo compartilhar ou recarregar cada seção
diretamente pela URL. A página permite:

- na página **Catálogo**, buscar e filtrar models, armas e texturas
  catalogadas, com o enriquecimento semântico no próprio painel do catálogo;
- salvar nome semântico, família, tags, notas e status de revisão sem misturar
  a análise do asset com a criação de conjuntos;
- na página **Composições**, selecionar a Action e montar uma receita de mesh
  base, Action e múltiplos componentes (props, armas, escudos e anexos), com
  nome, tags e notas;
- editar uma composição salva e visualizar a receita no viewport 3D;
- salvar a receita no catálogo e exportar um GLB reutilizável com personagem,
  Action e todos os componentes;
- na página **Sprites**, renderizar perfis 8×8, 8×12 ou 8×16 com resolução,
  FPS e câmera configuráveis, além de uma saída **Base IA** com 5 direções e
  9 fases;
- acompanhar renderizações de sprites e abrir o spritesheet, GIF e JSON de
  metadados do resultado.

### Providers de renderização por IA

A página **AI Render** usa OpenAI como provider padrão e oferece Gemini e Qwen
Cloud como alternativas. Para usar GPT Image 2,
instale o SDK `openai` e informe sua chave no menu de configurações ou pela
variável `OPENAI_API_KEY`:

```bash
python3 -m pip install openai
export OPENAI_API_KEY="..."
```

O provider Gemini usa `temperature=1.0` e `topK=64`, os defaults do modelo
`gemini-3.1-flash-image`, para equilibrar exploração visual e consistência
durante a geração condicionada, mantendo o valor registrado no código do
provider para que as requisições sejam reproduzíveis e auditáveis.

Para usar Qwen, instale o SDK e informe seu Token/API no menu de configurações
ou pela variável `DASHSCOPE_API_KEY`:

```bash
python3 -m pip install -r /home/ggnp/tools/generation/sprite-lab/requirements-qwen.txt
export DASHSCOPE_API_KEY="..."
```

Os modelos disponíveis são `gpt-image-2` para OpenAI e
`qwen-image-3.0-pro` (padrão) ou `qwen-image-3.0` para Qwen. O formulário permite escolher quais referências Blender
(`beauty`, `bones` e `lineart`) serão enviadas. A referência de identidade é
enviada separadamente; no Qwen, podem ser selecionadas até duas referências
Blender para respeitar o limite de três imagens por chamada. O output retornado
pela URL temporária da Qwen Cloud é baixado imediatamente e validado como PNG
2048×2048.

Se o token começar com `sk-sp-` (Token Plan), o provider seleciona
automaticamente `https://token-plan.ap-southeast-1.maas.aliyuncs.com/api/v1`.
Para outra modalidade, é possível informar explicitamente
`QWEN_API_BASE_URL` ou `DASHSCOPE_HTTP_BASE_URL`.

## Contrato de dados

`relationship_catalog.py index` recompõe o índice semântico sem modificar os
manifests físicos. O arquivo `semantic_annotations.json` é editável pelo
projeto; `relationships.json` é derivado dos manifests e preserva apenas
relacionamentos ainda válidos.

```bash
python3 /home/ggnp/tools/generation/sprite-lab/relationship_catalog.py index
python3 /home/ggnp/tools/generation/sprite-lab/relationship_catalog.py validate
```

O contrato de renderização é `sprite_lab.sprite_render/v1`. Cada resultado
declara bounds, Action, FPS, frames amostrados, componentes e as dimensões do
spritesheet. Isso permite que um cliente SaaS consuma a exportação sem
depender de uma engine específica.

### Manifesto agnóstico de asset

Todo novo render começa com `asset_type`, `representation` e
`capabilities`. O resultado cria `asset_manifest.json`, seguindo
`sprite_lab.asset_manifest/v1` (o schema está em
`schemas/asset_manifest_v1.json`). O manifesto é a fonte única para o
contrato de conteúdo, origem, geração, layout, animação, posicionamento,
capacidades de gameplay, runtime, artefatos e validação. Os PNGs/GIFs não são
embutidos em base64: ficam como arquivos normais, referenciados por caminho
relativo, tamanho e SHA-256.

Os tipos previstos são `actor`, `prop_static`, `prop_animated`, `tile` e
`vfx`. O renderer pode ser especializado por tipo, mas o formato de saída não
muda; um adaptador do jogo escolhe como importar a seção `runtime`. Nesta
primeira integração, a rota de composição de sprites usa diretamente o worker
Blender de personagens para `actor` e `prop_animated`; os demais tipos já
estão definidos no contrato e aparecem como workers pendentes, sem permitir
que um render de personagem seja gravado acidentalmente como árvore, tile ou
VFX.

### Enquadramento consistente

Todo render iniciado pela interface exige um manifesto
`sprite_lab.render_profile/v1`, armazenado em `state/render-profiles/`. O perfil
trava `cell_size`, `ortho_scale`, `foot_anchor`, elevação, azimute, direções,
fases e `ground_z`; esses valores substituem os controles livres do job e são
copiados para `request.json`, `render_metadata.json` e `render.json`.

O worker não recalcula o zoom para cada Action quando há um perfil ativo. Com
`cell_size_mode: "fit"` (opt-in), ele calcula uma célula quadrada comum para o
maior limite projetado de todas as direções/fases e ajusta `ortho_scale` na mesma
proporção, preservando a densidade visual do perfil. O cálculo considera o maior
envelope de uma célula individual; limites de poses diferentes não são somados.
As células resultantes são
uniformes e continuam sendo colocadas lado a lado na grade; não há sobreposição
entre frames. O modo padrão é `fixed`, mantendo a célula definida no perfil —
atualmente 256×256. `ortho_scale_mode: "fit"` é independente: mantém a célula
fixa e calcula o menor `ortho_scale` que acomoda o envelope inteiro com as
margens configuradas, respeitando o valor do perfil como limite inferior. Os
presets de câmera 2D (isométrico, plataforma, frontal, 3/4, diagonal e
top-down) selecionam manifestos derivados, cada um com elevação, azimute e
escala ortográfica calibrados para a mesma composição. A saída também inclui um GIF de
inspeção 2× usando a diagonal `c1r8 → c2r7 → … → c8r1`. Com
`dynamic_x: true`, ele projeta cada pose e desloca somente o eixo horizontal
quando personagem, arma ou efeito ultrapassaria a borda. `dynamic_y: true` faz
o mesmo no eixo vertical, inclusive para conteúdo que ultrapassa a parte
superior. Os deslocamentos e os limites projetados ficam registrados em
`render_metadata.json`, junto do `foot_anchor` efetivo de cada célula. Esse
ajuste não altera `ortho_scale` nem a grade; a âncora do perfil continua sendo
a referência canônica. Se a projeção for maior que a célula em qualquer eixo,
o job falha com um pedido explícito para aumentar `ortho_scale` em vez de
encolher silenciosamente o personagem. O perfil deve ser calibrado com a ação
de maior alcance do conjunto e depois reutilizado por `idle`, `run` e `attack`.

### Saída Base IA

A opção **Base IA** renderiza 9 fases reais da Action para as cinco direções
canônicas únicas `r1`, `r2`, `r5`, `r6` e `r7`. Cada direção gera uma imagem
RGBA de 2048×2048, com as 9 células de 672×672 organizadas em uma grade 3×3
centralizada. As linhas espelhadas continuam definidas pelo contrato (`r3 ← r1`,
`r4 ← r2` e `r8 ← r6`), mas não são duplicadas na entrada da IA. O job mantém
também o `spritesheet.png` bruto e os GIFs por direção para inspeção.

O manifesto `hero_reference_v1.json` é a configuração isométrica. As outras
visões têm manifestos derivados, selecionados automaticamente pela câmera:
`hero_reference_v1_platform.json`, `hero_reference_v1_frontal.json`,
`hero_reference_v1_three_quarter.json`, `hero_reference_v1_diagonal.json` e
`hero_reference_v1_top_down.json`. O `ortho_scale` de cada um foi medido no
Blender sobre a composição de referência, percorrendo as 8 direções e 8 fases;
por isso não é calculado como fator relativo ao isométrico. Os valores atuais
são: isométrico `2.5770567`, plataforma `2.4759975`, frontal `2.4759971`,
3/4 `2.5102688`, diagonal `2.4759973` e top-down `2.6732592`.

```json
{
  "schema": "sprite_lab.render_profile/v1",
  "id": "hero_reference_v1",
  "cell_size": [256, 256],
  "cell_size_mode": "fixed",
  "ortho_scale_mode": "fixed",
  "cell_size_quantum": 16,
  "ortho_scale": 2.57705670238966,
  "dynamic_x": true,
  "horizontal_margin_px": 2.0,
  "dynamic_y": true,
  "vertical_margin_px": 2.0,
  "foot_anchor": [128, 220],
  "camera_elevation": 35.264,
  "camera_azimuth": 45.0,
  "directions": 8,
  "phases": 8,
  "ground_z": 0.0,
  "camera_preset": "isometric"
}
```

### Receita de composição v2

O campo `components` é a fonte de verdade para objetos adicionais. Cada item
tem `id`, `asset_id`, `role`, `parent`, `attach_to`,
`attach_to_secondary`, `two_hand_axis`, `transform`, `fit` e `visible`.
`parent` pode ser `character`, `scene` ou outro componente; quando `attach_to`
é informado, ele identifica um osso/socket ou um nó nomeado. Para armas de
duas mãos, informe `attach_to: "hand_r"` e
`attach_to_secondary: "hand_l"`; `two_hand_axis` define qual eixo local do
asset representa o cabo. A rotação é expressa em graus e o `fit` opcional
`character_height` normaliza o maior eixo do componente pela altura do
personagem.

Exemplo mínimo:

```json
{
  "components": [
    {
      "id": "sword",
      "asset_id": "asset-da-espada",
      "role": "weapon",
      "parent": "character",
      "attach_to": "hand_r",
      "attach_to_secondary": "hand_l",
      "two_hand_axis": "-z",
      "fit": {"mode": "character_height", "ratio": 0.8},
      "transform": {
        "position": [0, -0.1, 0],
        "rotation": [0, 0, 90],
        "scale": [1, 1, 1]
      }
    },
    {
      "id": "lantern",
      "asset_id": "asset-da-lanterna",
      "role": "prop",
      "parent": "scene",
      "transform": {"position": [1, 0, 0], "rotation": [0, 30, 0], "scale": [1, 1, 1]}
    }
  ]
}
```

`composition_schema.py` valida IDs únicos, parents existentes, ausência de
ciclos, vetores finitos e escalas positivas. As chaves antigas
`weapon_asset_id` e `shield_asset_id` continuam sendo aceitas e são
convertidas para componentes compatíveis; elas também são derivadas ao salvar
uma relação v2 para não quebrar clientes legados.

## Suporte e limites atuais

- FBX é o caminho validado com Blender headless; GLB/GLTF também são aceitos
  pelo worker.
- Arquivos que não são objetos 3D renderizáveis, como PNG e JPG, permanecem
  fora da fila de análise pendente e não entram nas receitas de composição.
- Componentes podem ser anexados a `hand_r`, `hand_l`, outros sockets/ossos ou
  nós nomeados, e podem receber transform explícito, fit pela altura do
  personagem e visibilidade. A receita é renderizada tanto no viewer Three.js
  quanto no worker Blender.
- GIFs de inspeção dos sprites usam FPS configurável para facilitar a leitura
  da animação.
- O catálogo físico atual continua filtrando FBX e textures conforme a política
  do projeto. Novos formatos precisam de um adapter de importação e de um
  probe antes de serem considerados confiáveis para o SaaS.

## API mínima

- `GET /api/catalog`, `/api/animations`, `/api/relationships`
- `GET /api/sprite-jobs`
- `GET /api/render-profiles`, `/api/camera-presets`
- `GET /api/asset-contract`
- `POST /api/annotate`
- `POST /api/annotate-action`
- `POST /api/relationships`
- `GET /composition-exports/<arquivo>.glb`
- `POST /api/sprite-render`
- `GET /api/sprite-jobs/<job-id>/download`
- `POST /api/reindex`

As renderizações de sprites ficam em
`tools/generation/sprite-lab/work/sprite-renders/<job-id>/` e incluem as células,
o spritesheet final, `asset_manifest.json` e um GIF de inspeção para cada linha (`animation_r1.gif` até
`animation_r8.gif`). A ordem canônica de câmera é `r1: South`, `r2:
South-east`, `r3: East`, `r4: North-east`, `r5: North`, `r6: North-west`,
`r7: West` e `r8: South-west`. Essa ordem é registrada em
`direction_contract/v1` no `render_metadata.json` e no `render.json`; o GIF
giratório percorre as mesmas oito posições, usando uma fase correspondente de
cada linha. O GIF legado `animation.gif` continua apontando para a primeira
linha renderizada.
O GIF é apenas uma prévia de inspeção: o formato suporta transparência binária,
enquanto os PNGs individuais e o spritesheet preservam o alpha de 8 bits para
uso no jogo.

### Importação de spritesheet com TEED

Para folhas geradas por IA com fundo uniforme (preto, magenta ou outra cor
dominante nas bordas), o importador pode usar o [TEED
(Tiny and Efficient Edge Detector)](https://github.com/xavysp/TEED) para
localizar a área principal de cada célula antes de remover o fundo. A cor é
estimada automaticamente nas bordas de cada célula, e somente a região
conectada ao exterior é removida. O detector é executado em CPU e os mapas de
borda ficam preservados em `teed_edges/` para auditoria.

O ambiente usado na PoC fica em `work/teed-venv/`, e o checkout mínimo do
modelo em `work/teed-min/`. Com esses artefatos disponíveis:

```bash
work/teed-venv/bin/python jpeg_sheet_import.py \
  /abs/path/spritesheet.jpeg \
  work/teed-import/run \
  --rows 8 \
  --phases 8 \
  --fps 10 \
  --edge-detector teed \
  --teed-python work/teed-venv/bin/python \
  --teed-repo work/teed-min \
  --teed-checkpoint work/teed-min/checkpoints/BIPED/7/7_model.pth \
  --teed-threshold 90 \
  --teed-padding 6
```

O denoise neural opcional roda antes do TEED e da remoção do fundo. A
combinação validada usa a implementação oficial `nagadomi/nunif`, CUNet para
arte, método `noise` (1×), nível 1, CPU e FP32. O processamento é feito por
célula para não contaminar quadros vizinhos e preserva os PNGs intermediários
em `waifu2x_denoised/`:

```bash
python3 jpeg_sheet_import.py /abs/path/spritesheet.jpeg work/teed-import/run \
  --rows 8 --phases 8 --fps 10 \
  --denoiser waifu2x-cunet \
  --waifu2x-python work/teed-venv/bin/python \
  --waifu2x-repo work/nunif \
  --waifu2x-noise-level 1 \
  --edge-detector teed \
  --teed-python work/teed-venv/bin/python \
  --teed-repo work/teed-min \
  --teed-checkpoint work/teed-min/checkpoints/BIPED/7/7_model.pth
```

O `render_metadata.json` registra o dispositivo efetivo, disponibilidade de
CUDA, checkpoint, número de parâmetros e tempo da inferência. O modo padrão
`--edge-detector none` permanece disponível para comparar o resultado legado.
Para uma máscara mais restrita, o importador usa por padrão uma faixa de
chroma-key detectada nas bordas (`--background-key auto`). Em folhas magenta,
`--background-key magenta` força a remoção da faixa, inclusive do spill escuro
de anti-aliasing, numa faixa exterior configurável por
`--magenta-spill-radius` (8 px por padrão). Os padrões do modo TEED ignoram 8 px da borda do mapa, usam
limiar 180 e padding 2; eles podem ser ajustados com `--teed-border-margin`,
`--teed-threshold` e `--teed-padding`. Para folhas pretas, componentes internos
quase pretos grandes também são removidos quando o TEED confirma a borda que os
enclausura; o limite de área é ajustável com `--teed-enclosed-min-area`.

### Importação com BiRefNet-Lite

O caminho alternativo ao TEED usa o checkpoint oficial
`ZhengPeng7/BiRefNet_lite` para segmentar cada célula separadamente em CPU. A
máscara 1024×1024 retorna ao tamanho da célula com nearest-neighbor, recebe um
limiar configurável e produz alpha estritamente binário. Antes da composição,
as cores da faixa de contorno são decontaminadas usando a cor de fundo estimada
nas bordas. Não há alpha matting nesse modo.

```bash
python3 jpeg_sheet_import.py /abs/path/spritesheet.jpeg work/birefnet-import/run \
  --rows 8 --phases 8 --fps 10 \
  --foreground-extractor birefnet-lite \
  --birefnet-python work/teed-venv/bin/python \
  --birefnet-threshold 0.50 \
  --birefnet-input-size 1024 \
  --output-cell-size 256 \
  --edge-detector none \
  --denoiser none
```

O código remoto e os pesos são fixados na revisão registrada em
`render_metadata.json`. As máscaras binárias ficam em `birefnet_masks/`, e os
frames RGBA decontaminados em `birefnet_foreground/`. O spritesheet final mantém
uma célula quadrada fixa para preservar padding e origem entre todos os frames.

### Pipeline oficial Real-ESRGAN → BiRefNet em 512 px

Para fechar a PoC em alta resolução, use o orquestrador abaixo sobre a nova
spritesheet. Ele divide a entrada, amplia cada célula com Real-ESRGAN e só
depois calcula a máscara BiRefNet-Lite diretamente na célula 512×512. O alpha
final é binário e o `foot_anchor` é escalado junto com a célula.

```bash
work/teed-venv/bin/python realesrgan_birefnet_pipeline.py \
  /abs/path/spritesheet.jpeg \
  work/birefnet-import/official-final \
  --rows 8 --phases 8 --fps 10 \
  --realesrgan-python work/teed-venv/bin/python \
  --realesrgan-repo work/Real-ESRGAN \
  --birefnet-python work/teed-venv/bin/python \
  --birefnet-threshold 0.50 \
  --birefnet-input-size 1024 \
  --realesrgan-tile-size 256 \
  --realesrgan-tile-pad 32 \
  --foot-anchor 128 220
```

O resultado terá células 512×512, atlas 4096×4096, máscaras em
`birefnet_masks/`, intermediários Real-ESRGAN em `realesrgan_512/` e todos os
GIFs na pasta de saída. Por padrão, o orquestrador executa uma limpeza
generativa automática após o BiRefNet: se detectar chroma saturado, aplica o
perfil agressivo de foreground; sem chroma detectável, preserva o resultado do
BiRefNet sem alterar a máscara.

### Polimento final com chroma despill

Quando o fundo de geração usa chroma key saturado, o alpha do BiRefNet pode
estar correto e ainda restar contaminação RGB nos pixels opacos do contorno.
O estágio `chroma_despill.py` estima a cor do fundo por frame, neutraliza apenas
o canal dominante dentro de uma faixa interna da máscara, preserva o alpha
binário e faz alpha bleeding antes de reconstruir o atlas e os GIFs.

```bash
work/teed-venv/bin/python chroma_despill.py \
  work/birefnet-import/official-final \
  work/birefnet-import/official-final-despill \
  --rows 8 --phases 8 --fps 10 \
  --edge-radius 6 --tolerance 2 --strength 1 \
  --bleed-radius 8
```

Os parâmetros acima são o perfil aprovado para o fundo verde da POC
`ss_test3`. A saída registra a cor estimada, quantidade de pixels corrigidos e
a preservação da máscara em `render_metadata.json`.

Para uma saída generativa que ainda tenha ilhas verdes opacas dentro da
máscara, usar o modo agressivo de foreground. Ele remove componentes pequenos
próximos ao chroma key e aplica o despill em todo o foreground; deve ser
habilitado somente quando a cor não for um material legítimo do personagem.

```bash
work/teed-venv/bin/python chroma_despill.py \
  work/birefnet-import/official-final \
  work/birefnet-import/official-final-foreground-cleanup \
  --rows 8 --phases 8 --fps 10 \
  --scope foreground --remove-key-islands \
  --key-distance 96 --max-island-size 2048 \
  --edge-radius 6 --tolerance 2 --strength 1 \
  --bleed-radius 8
```

### Upscale 2× pós-segmentação

Quando o personagem precisa aparecer maior na tela, a saída RGBA de 256×256
pode ser ampliada offline com alpha bleeding e Waifu2x CUNet. O alpha original
do BiRefNet é ampliado por nearest-neighbor e reaplicado depois do Waifu2x;
assim, o modelo reconstrói apenas a imagem e não pode alterar a silhueta.

```bash
work/teed-venv/bin/python waifu2x_cunet_scale.py \
  work/birefnet-import/ss_test2-lite-t050/birefnet_foreground \
  work/birefnet-import/ss_test2-lite-t050-waifu2x-2x \
  --nunif-repo work/nunif \
  --rows 8 --phases 8 --scale 2 \
  --bleed-radius 8 --tile-size 256 \
  --foot-anchor 128 220
```

O resultado tem células 512×512, atlas 4096×4096 e `foot_anchor` `[256,440]`.
Os intermediários do bleeding ficam em `alpha_bleed/`, e o relatório registra
que o alpha de origem foi preservado.

### Upscale 2× com Real-ESRGAN

Como alternativa ao Waifu2x, `realesrgan_anime_scale.py` usa o modelo oficial
`RealESRGAN_x4plus_anime_6B` em CPU/FP32. O modelo trabalha internamente em 4×
e entrega 2× por redimensionamento Lanczos4. A máscara da saída BiRefNet é
reaplicada no final; o RGB transparente recebe alpha bleeding antes da
inferência. O filtro Lanczos da máscara é opcional e suaviza o serrilhado
produzido pelo aumento de uma máscara binária.

```bash
work/teed-venv/bin/python realesrgan_anime_scale.py \
  work/birefnet-import/ss_test2-lite-t050/birefnet_foreground \
  work/birefnet-import/ss_test2-lite-t050-realesrgan-anime-2x \
  --realesrgan-repo work/Real-ESRGAN \
  --rows 8 --phases 8 --scale 2 \
  --bleed-radius 8 --tile-size 256 --tile-pad 32 \
  --alpha-filter lanczos --foot-anchor 128 220
```

## Páginas e workspace 3D no navegador

O workspace possui três páginas no mesmo servidor:

1. **Catálogo**: análise semântica, revisão e inspeção 3D interativa dos
   objetos renderizáveis. O relacionamento não é salvo nessa tela.
2. **Composições**: editor de receitas semânticas reutilizáveis. Uma receita
   combina um mesh principal, uma Action e componentes opcionais; ela não
   altera os arquivos físicos de origem. Ao clicar em **Salvar**, o
   relacionamento é catalogado e a receita é exportada como GLB, com as
   texturas embutidas e a Action disponível para uso posterior.
3. **Sprites**: renderização determinística da composição em direções × fases,
   com células individuais, spritesheet, GIF de inspeção e metadados.

Ao salvar uma composição editada com um nome diferente, o Sprite Lab cria uma
nova receita e preserva a original. A atualização só reutiliza o registro
quando o nome permanece o mesmo. Antes de qualquer exportação ou renderização
no Blender, os canais X/Y de deslocamento do osso root são fixados no primeiro
frame; rotação, pose e deslocamento vertical continuam disponíveis.

O viewport 3D interativo carrega FBX, GLB e GLTF diretamente no navegador, com
órbita, zoom manual, animações, controle de velocidade e montagem de múltiplos
componentes.

A interface segue o conceito de um studio de criação: navegação lateral
persistentemente disponível, modos de trabalho separados e foco no viewport
central. A navegação pode ser recolhida para liberar espaço para o canvas.

O fluxo é dividido em três camadas:

1. o navegador tenta abrir o asset diretamente, sem Blender;
2. quando o FBX não é compatível com o loader do navegador, o servidor converte
   o asset uma única vez para GLB e reutiliza o arquivo em
   `tools/source-assets/catalog/web-model-cache/`;
3. na página **Composições**, os seletores de mesh base, Action e componentes
   atualizam a receita no viewport.

As rotas de suporte são `GET /api/assets/<id>/viewer`,
`GET /assets/<id>/source/<path>` e `GET /assets/<id>/model`. A conversão sob
demanda continua sendo um fallback; GIFs e spritesheets permanecem no worker
offline, porque são exportações e não inspeção interativa.

### Renderização de sprites

A página **Sprites** consome uma composição salva e cria uma grade determinística
de direções × fases. O renderer usa câmera ortográfica, remove root motion,
centraliza o personagem pelo movimento dos quadris e preserva os componentes
anexados na composição. O contrato `sprite_lab.sprite_render/v1` não depende de
uma engine de jogo; adaptadores podem consumir as células ou o manifesto
gerado.
