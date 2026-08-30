# POC de condicionamento para o projeto Generation

Este fluxo transforma uma animação 3D em um conjunto de referências estruturais
para um modelo de geração de imagens. O modelo recebe a imagem de identidade do
personagem, a renderização 3D do frame e canais auxiliares; o resultado é
normalizado e organizado em spritesheet e GIF para inspeção.

O contrato foi desenhado para modelos Transformer de imagem. Ele não presume
ControlNet, difusão ou uma API específica. OpenAI e Google são adaptadores
intercambiáveis; o provider `dry-run` permite validar todos os arquivos e
prompts sem chamar um serviço remoto.

## O que foi implementado

| Etapa | Arquivo | Resultado |
| --- | --- | --- |
| Exportação 3D | `blender_conditioning_export.py` | beauty, silhouette, segmentation, skeleton e depth opcional |
| Pacote de referência | `conditioning_pack.py` | cópia normalizada, manifesto, prompt e montagem de inspeção |
| Contrato | `conditioning_schema.py` | validação de caminhos, frames, canais, resolução e âncora |
| Geração | `conditioning_runner.py` | uma requisição por frame para OpenAI, Google ou dry-run |
| Pós-processamento | `postprocess_conditioning.py` | remoção de fundo, escala, âncora, spritesheet e GIF |
| Métricas | `conditioning_metrics.py` | presença, bounding box, âncora, área, IoU diagnóstico e temporalidade |

## Estrutura do pacote

Depois de executar `conditioning_pack.py`, o diretório terá esta forma:

```text
pack/
├── manifest.json
├── prompt.txt
├── conditioning-pack.png
├── target-reference/target.png
├── beauty/f00.png ...
├── silhouette/f00.png ...
├── segmentation/f00.png ...
├── depth/f00.png ...
└── skeleton/f00.png ...
```

`manifest.json` é a fonte de verdade. Cada frame possui um ID estável, uma
posição, seus caminhos de canal, a resolução quadrada, FPS e `foot_anchor`.
Todos os caminhos internos são relativos e validados para impedir que uma
requisição escape do diretório do pacote.

## 1. Exportar os canais a partir do Blender

Crie um arquivo `request.json` ao lado do `.blend`. Um request mínimo para uma
ação de nove frames é:

```json
{
  "output": "/abs/path/work/generation-source/run-r1",
  "camera": "Camera",
  "resolution": [512, 512],
  "frame_start": 1,
  "frame_end": 9,
  "frame_step": 1,
  "action": "run",
  "direction": "r1",
  "fps": 10,
  "foot_anchor": [256, 440],
  "profile_id": "hero_reference_v1",
  "engine": "BLENDER_EEVEE",
  "depth": true,
  "depth_range": [0.1, 20.0]
}
```

Execute:

```bash
blender -b personagem.blend \
  --python /home/ggnp/tools/generation/sprite-lab/blender_conditioning_export.py \
  -- --request /abs/path/request.json
```

O exportador considera como geometria todos os meshes visíveis, exceto os que
possuem a propriedade customizada `conditioning_include: false`. Para melhorar
a segmentação, adicione `conditioning_role` aos objetos. Os papéis aceitos são
`head`, `torso`, `arm`, `hand`, `leg`, `weapon`, `shield`, `accessory` e
`other`. Sem a propriedade, o script tenta inferir o papel pelo nome do objeto.

O canal `skeleton` é gerado a partir das armatures visíveis. O canal `depth` é
opt-in porque o intervalo de profundidade depende da câmera e da cena; ajuste
`depth_range` para evitar que a maior parte do frame fique saturada.

## 2. Montar o pacote de referência

O segundo input é a imagem que define a identidade visual final: rosto,
silhueta de vestuário, materiais, cores e elementos reconhecíveis. Ela não é
usada para definir pose ou câmera.

```bash
python3 /home/ggnp/tools/generation/sprite-lab/conditioning_pack.py \
  /abs/path/work/generation-source/run-r1 \
  /abs/path/references/character-target.png \
  /abs/path/work/generation-packs/run-r1 \
  --action run \
  --direction r1 \
  --fps 10 \
  --foot-anchor 256,440 \
  --channels beauty,silhouette,segmentation,depth,skeleton
```

Comece com `beauty,silhouette,segmentation`. Adicione `depth` e `skeleton`
somente quando a comparação mostrar ganho real. O pacote cria
`conditioning-pack.png`, que é o artefato visual para revisão humana antes de
enviar qualquer imagem ao modelo.

## 3. Validar requests sem consumir API

O dry-run verifica se todas as imagens existem, registra o prompt e cria uma
requisição por frame:

```bash
python3 /home/ggnp/tools/generation/sprite-lab/conditioning_runner.py \
  /abs/path/work/generation-packs/run-r1/manifest.json \
  /abs/path/work/generation-runs/run-r1/requests \
  --provider dry-run \
  --model gpt-image-2 \
  --condition segmentation
```

Cada request fica como `f00.request.json`, `f01.request.json` etc. Para a
condição `segmentation`, o conjunto efetivamente enviado é:

```text
target-reference + beauty + silhouette + segmentation
```

As condições disponíveis são:

| Condição | Canais estruturais adicionados |
| --- | --- |
| `rgb` | beauty |
| `silhouette` | beauty + silhouette |
| `segmentation` | beauty + silhouette + segmentation |
| `depth` | beauty + silhouette + segmentation + depth |
| `skeleton` | beauty + silhouette + segmentation + skeleton |

Em todas elas, `target-reference` é enviado separadamente como autoridade de
identidade. O prompt reforça que máscaras, cores de segmentação e linhas do
esqueleto são guias e não podem aparecer na imagem final.

## 4. Executar um provider real

Para OpenAI, configure a chave no ambiente e troque o provider:

```bash
export OPENAI_API_KEY="..."
python3 /home/ggnp/tools/generation/sprite-lab/conditioning_runner.py \
  /abs/path/work/generation-packs/run-r1/manifest.json \
  /abs/path/work/generation-runs/run-r1/openai \
  --provider openai \
  --model gpt-image-2 \
  --condition segmentation
```

Para o adaptador Google, use `GOOGLE_API_KEY` e informe o ID de modelo de imagem
vigente na sua conta. O nome comercial “Nano Banana” não é usado como contrato
interno, pois o ID de API pode mudar:

```bash
export GOOGLE_API_KEY="..."
python3 /home/ggnp/tools/generation/sprite-lab/conditioning_runner.py \
  /abs/path/work/generation-packs/run-r1/manifest.json \
  /abs/path/work/generation-runs/run-r1/google \
  --provider google \
  --model <image-model-id> \
  --condition segmentation
```

As chaves não são gravadas no manifesto. O runner salva somente o request
local, o status e o caminho do resultado. O resultado esperado pelo runner é
um PNG local por frame, com nomes iguais aos IDs do manifesto (`f00.png`,
`f01.png` etc.).

Os SDKs são opcionais e não são importados no dry-run. Instale apenas o que for
usar no provider real:

```bash
python3 -m pip install openai
python3 -m pip install google-genai
```

## 5. Normalizar e gerar spritesheet/GIF

O pós-processamento não tenta decidir a identidade do personagem. Ele executa
operações determinísticas para tornar os frames utilizáveis como uma sequência:

1. remove fundo uniforme ou fundo baseado em alpha;
2. detecta o bounding box do personagem;
3. dimensiona a altura para `target_height_ratio` da célula;
4. posiciona o centro do personagem em `foot_anchor`;
5. preserva alpha de 8 bits no PNG;
6. monta o spritesheet e o GIF de inspeção.

```bash
python3 /home/ggnp/tools/generation/sprite-lab/postprocess_conditioning.py \
  /abs/path/work/generation-packs/run-r1/manifest.json \
  /abs/path/work/generation-runs/run-r1/openai \
  /abs/path/work/generation-runs/run-r1/postprocessed
```

O diretório final inclui:

```text
postprocessed/
├── normalized/f00.png ...
├── row0_col0.png ...
├── spritesheet.png
├── animation_r1.gif
└── postprocess.json
```

O GIF serve para inspeção. Os PNGs individuais e `spritesheet.png` são os
artefatos destinados ao uso posterior no jogo.

## 6. Rodar o gate de métricas

```bash
python3 /home/ggnp/tools/generation/sprite-lab/conditioning_metrics.py \
  /abs/path/work/generation-packs/run-r1/manifest.json \
  /abs/path/work/generation-runs/run-r1/postprocessed/normalized \
  --output /abs/path/work/generation-runs/run-r1/metrics.json
```

O relatório mede presença de frames, bounding box, área, erro da âncora dos
pés, IoU de silhueta como diagnóstico e variação temporal entre frames. O gate
mínimo falha quando falta algum frame ou quando o erro máximo da âncora passa de
4 pixels. O IoU não reprova sozinho uma geração: a mudança de roupa e
silhueta é justamente parte do objetivo, então ele é uma métrica de alerta para
comparação entre condições.

## Critérios da POC

A POC deve ser considerada aprovada quando, para uma ação curta e uma direção:

- o exportador produz todos os canais solicitados com a mesma resolução;
- o pacote é validado sem caminhos absolutos nos arquivos internos;
- o dry-run reproduz exatamente o conjunto de imagens esperado por condição;
- um provider real entrega um PNG para cada frame sem inserir painéis ou guias;
- a normalização mantém escala e âncora estáveis;
- o spritesheet/GIF é legível e temporalmente coerente;
- o gate de métricas passa e a revisão visual confirma identidade, pose e
  composição.

Compare pelo menos estas três condições no mesmo pacote: `rgb`, `silhouette` e
`segmentation`. Só promova `depth` ou `skeleton` para o caminho padrão se a
revisão mostrar melhora consistente em pose, equipamento ou estabilidade entre
frames.

## Testes locais

Na raiz do Sprite Lab:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Os testes de condicionamento usam imagens sintéticas e não fazem chamadas
remotas. Eles cobrem o contrato, a montagem do pacote, o dry-run, a
normalização, a montagem de spritesheet/GIF e o gate de métricas.
