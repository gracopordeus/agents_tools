# armour_3d_split

Separa uma armadura estática (GLB, glTF ou `.blend`) em peças nomeadas, sem alterar a
geometria: cada face da entrada vai para exatamente uma peça, e a ferramenta prova isso
antes de publicar o resultado.

Não decima, não encaixa no corpo, não cria UV, não corrige normais e não adivinha
nomes de peças. O arquivo de entrada nunca é salvo nem sobrescrito.

## Requisitos

- Python 3.10+ (só para o launcher; sem dependências externas).
- Blender 5.2.2 LTS no `PATH` como `blender`, ou indicado com `--blender /caminho/blender`.
- Rodar da pasta `blender_procedural`, ou chamar `armour_3d_split/run.py` pelo caminho absoluto.

## Fluxo em três passos

```text
1. inspect  → inventory.json + plan.template.json     (o que existe no arquivo)
2. você edita o plano                                   (quais ilhas formam cada peça)
3. split    → armour_split.blend + report.json         (peças separadas e auditadas)
```

### 1. Inspecionar

```bash
python3 -m armour_3d_split inspect \
  --input "assets_models/medieval knight armor 3d model.glb" \
  --out outputs/armour_3d_split/knight_inspect_001
```

Gera dois arquivos na pasta de saída:

- `inventory.json`: o SHA-256 da entrada e, por objeto, vértices, triângulos, materiais,
  UVs, bounding box e a lista de **componentes** (ilhas desconectadas).
- `plan.template.json`: um plano inicial com uma peça por componente (`Part_001`, `Part_002`…).

Cada componente traz `index`, `triangles`, `bbox_min` e `bbox_max`. São esses dados que
permitem reconhecer qual ilha é qual peça. Exemplo do arquivo acima:

| `index` | Triângulos | Bounding box (x, z) | Peça |
|---:|---:|---|---|
| 0 | 56.216 | centro, do joelho ao pescoço | Suit |
| 1 | 11.500 | x > 0, do chão ao joelho | Boot_L |
| 2 | 11.142 | x < 0, do chão ao joelho | Boot_R |
| 3 | 7.288 | x < 0, ao lado do quadril | Glove_R |
| 4 | 6.983 | centro, no topo | Helmet |
| 5 | 6.604 | x > 0, ao lado do quadril | Glove_L |

A armadura vem montada, de frente para `-Y`: `+X` é o lado esquerdo do personagem. Ilhas de
poucos triângulos (fragmentos) vão para a peça cuja bounding box as contém.

Os componentes são ordenados por número de vértices (decrescente). Os índices valem
só para o arquivo inspecionado.

A inspeção não gera imagens. Para conferir visualmente, abra o arquivo no Blender ou
renderize à parte.

### 2. Escrever o plano

Copie `plan.template.json`, dê nomes às peças e agrupe os componentes:

```json
{
  "version": 1,
  "input_sha256": "7c957e93…  (copiado do inventory.json)",
  "dependency_hashes": {},
  "note": "texto livre, opcional",
  "parts": [
    {"name": "Helmet", "selectors": [{"object": "tripo_node_85e98b80", "component": 4}]},
    {"name": "Suit",   "selectors": [{"object": "tripo_node_85e98b80", "component": 0}]}
  ]
}
```

Um selector escolhe faces de um objeto de origem. Há quatro formas:

| Selector | Seleciona |
|---|---|
| `{"object": "Nome"}` | o objeto inteiro |
| `{"object": "Nome", "component": 2}` | uma ilha desconectada, pelo `index` do inventário |
| `{"object": "Nome", "material_slot": 0}` | as faces de um slot de material |
| `{"object": "Nome", "faces": [0, 1, 2]}` | faces por ID |

Regras do plano (qualquer violação recusa o split):

- toda face dos objetos selecionados pertence a exatamente uma peça: nada sobra, nada repete;
- `input_sha256` e `dependency_hashes` precisam bater com o arquivo atual;
- nomes de peça: letra inicial, depois letras, dígitos ou `_`, até 63 caracteres, sem repetição;
- só os campos `version`, `input_sha256`, `dependency_hashes`, `parts` e `note` são aceitos.

Planos prontos em `examples/`: `medieval_knight_dedos.plan.json` (referência atual, com nove
fragmentos atribuídos ao elmo e ao tronco) e `medieval_knight.plan.json` (o do exemplo acima).

### 3. Separar

```bash
python3 -m armour_3d_split split \
  --input "assets_models/medieval knight armor 3d model.glb" \
  --plan armour_3d_split/examples/medieval_knight.plan.json \
  --normal-policy backup \
  --out outputs/armour_3d_split/knight_split_001
```

Saída no terminal:

```text
PASS_WITH_NORMAL_LIMITATION: …/knight_split_001/report.json
```

Quando a malha tem bolsos planos (ver abaixo), sai também uma linha `WARNING:` com a contagem.
O asset de referência não tem nenhum.

## Opções

| Opção | Comando | Efeito |
|---|---|---|
| `--input ARQ` | ambos | `.glb`, `.gltf` ou `.blend` existente (obrigatório) |
| `--out PASTA` | ambos | pasta nova ou vazia (obrigatório); nunca sobrescreve um run |
| `--plan ARQ` | `split` | plano JSON; exclui `--preset` |
| `--preset medieval_plate` | `split` | plano embutido, preso a um GLB específico (ver abaixo) |
| `--preset assembled` | `split` | nomeia as peças de um conjunto montado de pé pela posição (ver abaixo) |
| `--normal-policy strict\|backup` | `split` | `strict` (padrão) recusa desvio de normais; `backup` entrega com limitação medida |
| `--objects NOME…` | ambos | restringe às meshes citadas; use para excluir corpo e proxies |
| `--scene NOME` | ambos | obrigatório em `.blend` com mais de uma cena |
| `--blender EXEC` | ambos | executável do Blender (padrão `blender`) |

## Resultado e códigos de saída

| Status | Exit | Significado |
|---|---:|---|
| `PASS` | 0 | partição exata e normais dentro da tolerância |
| `PASS_WITH_NORMAL_LIMITATION` | 0 | partição exata; normais fora da tolerância, aceitas por `--normal-policy backup` |
| `FAIL: …` | ≠ 0 | nada foi publicado; a causa está na mensagem e em `failure.json` |

Arquivos de um split bem-sucedido:

| Arquivo | Conteúdo |
|---|---|
| `armour_split.blend` | as peças separadas, uma mesh por peça (é a entrega) |
| `armour_split_v001.blend` | cópia versionada, idêntica |
| `report.json` | status, avisos, auditoria antes de salvar e depois de reabrir |
| `inventory.json` | inventário da entrada usado neste run |
| `plan.resolved.json` | o plano efetivamente aplicado |
| `source_provenance.npz` | posições, normais e polígonos da entrada, sem perda |
| `request.json`, `blender.log` | parâmetros do run e log do Blender |

Um run que falha deixa `request.json`, `blender.log`, `inventory.json` e `failure.json`,
sem `.blend` e sem `report.json`.

### Como ler o `report.json`

- `status` e `warnings`: o resumo.
- `after_reopen.parts[]`: por peça, triângulos, vértices, erro de posição e de normal,
  origem e estratégia de normais (`native_preserved` ou `restored_lower_error`).
- `after_reopen.triangles`: precisa ser igual ao total da entrada.
- `after_reopen.world_position_max_error`: deslocamento máximo de um vértice (esperado ~1e-8).
- `after_reopen.normal_vector_max_error` e `worst_normal`: pior desvio de normal e onde ocorreu.
- `flat_pocket_normals`: vértices e corners sem normal definida (ver "Bolsos planos").

A auditoria roda duas vezes: antes de salvar e depois de reabrir o `.blend`.

## O que é preservado

Em cada peça, a ferramenta confere contra a entrada:

- partição exata das faces, ordem dos vértices e dos corners de cada polígono (winding);
- posições em world space (tolerância: diagonal da entrada × 1e-6);
- UVs, material por face, atributos POINT/EDGE/FACE/CORNER, flags seam/sharp e smooth;
- contagem de triângulos.

Cada peça sai com rotação e escala aplicadas e origem no centro da própria bounding
box; os vértices não se movem no mundo. Vértices na fronteira entre duas peças são
duplicados, um para cada lado.

As meshes de saída carregam atributos de proveniência `_a3s_*` (IDs de vértice, aresta,
face e corner da entrada, mais os vetores de normal originais). Eles permitem rastrear
cada elemento até a fonte.

## Normais

As normais customizadas do Blender são quantizadas, então a ferramenta audita o erro
vetorial por corner (tolerância `0,0002`) em vez de exigir identidade de bits.

- `--normal-policy strict` falha se algum corner passar da tolerância.
- `--normal-policy backup` entrega `PASS_WITH_NORMAL_LIMITATION`, com o erro e o pior
  corner no report. Os vetores da entrada ficam guardados sem perda no NPZ e em
  atributos CORNER. Isso não aprova o shading; só registra a diferença.

Quando a separação nativa passa da tolerância, a ferramenta testa regravar as normais
numa cópia e fica com a versão de menor erro. Uma regravação que marque arestas como
sharp é descartada: as flags da entrada prevalecem.

### Bolsos planos

Malhas da Tripo trazem "bolsos": vértices cujas faces ao redor são coplanares e apontam
para os dois lados (triângulos de fração de milímetro dobrados sobre si). Ali a normal
do vértice se cancela e o valor que o Blender avalia é ruído numérico: sai zerado em
alguns pontos e muda quando os vértices são só reordenados pela separação.

Esses corners ficam **fora da auditoria de normais**, e o report avisa:

- o critério é geométrico: todas as normais de face do leque com `|cos| ≥ 0,99` entre si,
  as duas orientações presentes e normal média com comprimento `≤ 0,05`;
- os vetores ficam como o Blender os avalia; nada é recalculado, removido ou soldado;
- `inventory.json` informa em `imported_corner_normals` os campos `splittable`,
  `folded_fans` e `unexplained_invalid_count`.

Normal zerada ou não finita **fora** de um bolso recusa o split com qualquer política.
A decimação posterior elimina os bolsos.

## Erros comuns

| Mensagem | Causa | O que fazer |
|---|---|---|
| `Output must be a new or empty directory` | a pasta de saída já tem arquivos | usar outra pasta |
| `Plan version/input hash does not match` | o plano é de outro arquivo, ou a entrada mudou | reinspecionar e atualizar `input_sha256` |
| `Unassigned/orphan source faces` | sobrou componente sem peça | atribuir todos os componentes, inclusive fragmentos |
| `Face duplicated` | dois selectors pegam a mesma face | remover a sobreposição |
| `Preservation failed: … normal=…` | desvio de normal com `strict` | rodar com `--normal-policy backup` e ler o report |
| `Invalid imported corner normals` | normal zerada ou não finita fora de bolso plano | corrigir a fonte; a ferramenta não repara |
| `Static split only` | modifiers, shape keys, skin, rig ou animação | remover numa cópia ou usar outro adapter |
| `Loose edges/unreferenced vertices` | arestas soltas ou vértices sem face | limpar numa cópia |
| `Unsupported mesh attribute` | tipo ou domínio de atributo não coberto | remover o atributo numa cópia |
| `Multiple scenes` | `.blend` com várias cenas | passar `--scene` |

## Entradas `.gltf` e `.blend`

- **`.gltf`:** buffers e imagens externos entram no plano por `dependency_hashes`; se o
  conteúdo mudar, o plano deixa de valer. URIs remotos são recusados.
- **`.blend`:** objetos ou meshes de libraries vinculadas precisam ser tornados locais
  antes. Texturas existentes são empacotadas na saída.

## Preset `assembled`

Para um conjunto montado como figura de pé, de frente para `-Y` (o que a pipeline usa com
`split.plan: "auto"`). Sem plano escrito à mão:

- componente com pelo menos 2% dos triângulos é peça; têm de ser seis;
- o maior é o `Suit`; o que cruza o meio do conjunto acima dele é o `Helmet`;
- dos quatro restantes, os dois mais baixos são as botas e os outros dois as manoplas;
- esquerda é `+X`; as duas peças de um par têm de estar em lados opostos e ter tamanho parecido;
- fragmento pequeno vai para a peça cuja caixa contém o centro dele.

Qualquer coisa fora disso (sétima peça, par do mesmo lado, fragmento em nenhuma ou em duas caixas) é
erro, com a causa na mensagem; aí o plano é escrito à mão depois de `inspect`. Os padrões estão em
`ASSEMBLED_DEFAULTS` (`partition.py`). Nos dois assets de referência o resultado é igual ao plano
escrito à mão.

## Preset `medieval_plate`

Plano embutido que vale só para o GLB de SHA-256
`e422e03e5c4dd798eedd6eb6c7f56b474dda5a3bc6c73a233ce1145c6e258733`, um asset de teste antigo
que foi removido de `assets_models/`. O código do preset continua na ferramenta, sem uso.

## Lateralidade

`_L` e `_R` são só os nomes que o plano dá. No plano de referência eles seguem a anatomia
(`+X` é a esquerda do personagem). A ferramenta não espelha nem reposiciona nada.

## Próximo passo: decimação

O `.blend` separado mantém a malha como veio. Um GLB não guarda quads, então uma malha de
quads chega aqui em pares de triângulos; quem junta os pares de volta e, se preciso, reduz aos
orçamentos por peça é o `armour_3d_decimate`. O split com entrada já em quads (um `.blend`)
segue as mesmas regras, mas não foi testado.

```bash
blender -b --factory-startup --python-exit-code 2 -P armour_3d_decimate/decimate.py -- \
  --input outputs/armour_3d_split/knight_split_001/armour_split.blend \
  --budgets armour_3d_decimate/budgets.plate_quads.json \
  --out outputs/armour_3d_decimate/knight_001
```

## Testes

```bash
python3 -m unittest discover -s armour_3d_split/tests -p 'test_*.py' -v
python3 armour_3d_split/tests/cli_integration.py /caminho/pasta_vazia
```

O primeiro cobre as regras do plano no Python do sistema. O segundo roda no Blender:
cria um fixture com transformações, fronteiras compartilhadas, dois UVs, cores e
atributos, executa o split, e confere recusa de sobrescrita, de faces duplicadas e de
normais inválidas, além do tratamento de bolsos planos.

## Estrutura

| Arquivo | Papel |
|---|---|
| `run.py` | launcher: valida argumentos, calcula hashes, inicia o Blender isolado |
| `blender_entry.py` | ponto de entrada dentro do Blender; grava `failure.json` em erro |
| `mesh_split.py` | inventário, separação, proveniência, normais e auditoria |
| `partition.py` | regras do plano em Python puro, mais o preset |
| `examples/` | planos prontos |
| `tests/` | testes unitários, fixture e integração |
