# Pós-processamento GPU e POC de super-resolução

Implementação local em 7 de setembro de 2026. Hardware: GTX 1070 Ti, 8 GB VRAM. O fluxo Blender e a geração de poses não foram alterados.

## Alterações entregues

- Real-ESRGAN e BiRefNet passam a usar CUDA quando disponível. `--device cuda` exige GPU e falha explicitamente quando indisponível; `auto` permite CPU em outros computadores; `cpu` permite comparação/regressão.
- FP32 é o padrão. FP16 é opção explícita, não uma otimização presumida para Pascal. Não foi incluído na tabela porque os ensaios publicados usam FP32.
- O servidor respeita `SPRITE_LAB_PYTHON`; sem configuração explícita, prefere `~/pose-venv/bin/python` instalado nesta máquina, depois o ambiente legado e o Python do servidor.
- `SPRITE_LAB_POSTPROCESS_DEVICE=cuda` pode exigir GPU para os jobs do servidor. Sem variável, usa `auto`. Os metadados das etapas registram o dispositivo realmente usado; máscaras recuperadas de cache continuam sendo reutilizadas.
- Backend de SR comum baseado em Spandrel: mantém o checkpoint Anime 6B, RGB/BGR, escala final 2× e alpha externo. Elimina a dependência de importação do BasicSR antigo, cujo empacotamento falhou no Python 3.14.
- SwinIR Lightweight 2×, SwinIR-M ClassicalSR DF2K 2×, Real-CUGAN 2× sem denoise e Real-CUGAN 2× conservador estão no catálogo e no seletor de upscale final, marcados como POC. O padrão continua Anime 6B; não foi substituído silenciosamente.
- O passe de máscara continua Anime 6B + BiRefNet. Alternar o modelo final não deve alterar a máscara aprovada.
- Quando habilitado, o lineart não é desenhado no spritesheet de cores. O passe final exporta `lineart/row*_col*.png`, `lineart/spritesheet.png` e GIFs correspondentes como uma camada RGBA separada, branca e transparente, limitada pela interseção com o alpha aprovado. O atlas de cores permanece inalterado e os dois atlas têm a mesma grade/pivô para composição no runtime.
- O pós-processamento agora oferece `lineart_mode`: `blender` reutiliza o lineart estrutural do Blender, `lineart_standard` executa `LineartDetector(coarse=False)` do `controlnet_aux` em cada frame final 512×512, `lineart_coarse` executa o mesmo detector com `coarse=True` para um traço mais denso, `lineart_anime` executa `LineartAnimeDetector`, e `none` desliga o estágio. Os três detectores aplicam o mapa dentro do alpha aprovado; não usam SD1.5 nem condicionam o SwinIR, sendo reforços finais por frame.
- Uma trava de arquivo serializa as inferências CUDA de pós-processamento entre workers. Ela não coordena processos externos; não executar benchmarks simultâneos que disputem a mesma GPU.
- Limpeza de chroma, máscaras/alpha, montagem de atlas, GIFs e variantes de cor continuam em CPU. São operações distintas da inferência; não foram reescritas de forma arriscada apenas para chamar tudo de GPU.

Os argumentos `--realesrgan-repo` e `--realesrgan-python` são mantidos por compatibilidade com os chamadores existentes. O novo loader não exige um clone de Real-ESRGAN.

## Reprodução

Usar a raiz de `sprite-lab`. Ambiente atual: `/home/ggnp/pose-venv`, reutilizando dependências de `/home/ggnp/sd-venv` por `.pth`. As dependências adicionais foram instaladas apenas no ambiente de trabalho. [requirements-postprocess-gpu.txt](../../requirements-postprocess-gpu.txt) registra as versões utilizadas; uma instalação limpa desse arquivo não foi testada.

```bash
/home/ggnp/pose-venv/bin/python experiments/postprocess_gpu/benchmark.py \
  --output work/postprocess-benchmark/nova-execucao

/home/ggnp/pose-venv/bin/python experiments/postprocess_gpu/validate_pipeline.py \
  --output work/postprocess-benchmark/nova-validacao

/home/ggnp/pose-venv/bin/python -m unittest discover \
  -s tests -p 'test_postprocess_runtime.py' -v
```

Os diretórios devem ser novos para preservar os ensaios anteriores. Pesos alternativos são baixados das releases oficiais e verificados por SHA256; Anime 6B e BiRefNet usam revisões fixadas. O download inicial usa internet, mas a inferência é local. BiRefNet utiliza o código remoto da revisão fixada, como na pipeline original.

## Metodologia e resultados

- [Tabela e metodologia de inferência](../../work/postprocess-benchmark/v1/RESULTS.md).
- [POC SwinIR-M DF2K x2: tabela e metodologia](../../work/postprocess-benchmark/swinir-m-df2k-v1/RESULTS.md).
- [POC SwinIR-M DF2K x2: comparação visual](../../work/postprocess-benchmark/swinir-m-df2k-v1/comparison.png).
- [POC SwinIR-M DF2K x2: dados brutos](../../work/postprocess-benchmark/swinir-m-df2k-v1/results.json).
- [Dados brutos, hashes, repetições e tempos de carga](../../work/postprocess-benchmark/v1/results.json).
- [Comparação visual](../../work/postprocess-benchmark/v1/comparison.png): referência, ESRGAN CPU, ESRGAN GPU, SwinIR, CUGAN sem denoise, CUGAN conservador.
- [Validação da pipeline e comparação das máscaras](../../work/postprocess-benchmark/endtoend-v1/validation.json).
- [Comparação limpa DF2K + lineart](../../work/postprocess-benchmark/swinir-m-lineart-clean-v2/comparison.png): frame único sem os artefatos temporais da sequência.
- [Metadados da comparação limpa](../../work/postprocess-benchmark/swinir-m-lineart-clean-v2/metadata.json).
- [POC de integração SwinIR-M + lineart_standard](../../work/postprocess-benchmark/swinir-m-lineart-standard-e2e-v1/spritesheet.png).
- [Atlas separado de lineart_standard](../../work/postprocess-benchmark/swinir-m-lineart-standard-e2e-v1/lineart/spritesheet.png).
- [Metadados da integração SwinIR-M + lineart_standard](../../work/postprocess-benchmark/swinir-m-lineart-standard-e2e-v1/render_metadata.json).
- [POC de integração SwinIR-M + lineart_anime](../../work/postprocess-benchmark/swinir-m-lineart-anime-e2e-v1/spritesheet.png).
- [Atlas separado de lineart_anime](../../work/postprocess-benchmark/swinir-m-lineart-anime-e2e-v1/lineart/spritesheet.png).
- [Metadados da integração SwinIR-M + lineart_anime](../../work/postprocess-benchmark/swinir-m-lineart-anime-e2e-v1/render_metadata.json).
- [POC de integração SwinIR-M + lineart_coarse](../../work/postprocess-benchmark/swinir-m-lineart-coarse-e2e-v1/spritesheet.png).
- [Atlas separado de lineart_coarse](../../work/postprocess-benchmark/swinir-m-lineart-coarse-e2e-v1/lineart/spritesheet.png).
- [Metadados da integração SwinIR-M + lineart_coarse](../../work/postprocess-benchmark/swinir-m-lineart-coarse-e2e-v1/render_metadata.json).

Pelo Sprite Lab remoto, os mesmos artefatos podem ser abertos no navegador em `https://cachyos.taildc6762.ts.net/postprocess-benchmark/v1/comparison.png`, `https://cachyos.taildc6762.ts.net/postprocess-benchmark/v1/RESULTS.md`, `https://cachyos.taildc6762.ts.net/postprocess-benchmark/swinir-m-lineart-clean-v2/comparison.png`, `https://cachyos.taildc6762.ts.net/postprocess-benchmark/swinir-m-lineart-standard-e2e-v1/spritesheet.png`, `https://cachyos.taildc6762.ts.net/postprocess-benchmark/swinir-m-lineart-standard-e2e-v1/lineart/spritesheet.png`, `https://cachyos.taildc6762.ts.net/postprocess-benchmark/swinir-m-lineart-coarse-e2e-v1/spritesheet.png`, `https://cachyos.taildc6762.ts.net/postprocess-benchmark/swinir-m-lineart-coarse-e2e-v1/lineart/spritesheet.png`, `https://cachyos.taildc6762.ts.net/postprocess-benchmark/swinir-m-lineart-anime-e2e-v1/spritesheet.png` e `https://cachyos.taildc6762.ts.net/postprocess-benchmark/swinir-m-lineart-anime-e2e-v1/lineart/spritesheet.png`. O endpoint é somente leitura e limitado a `work/postprocess-benchmark`.

### Integração medida: oito frames de 256→512 px

| Passe final completo em GPU | Tempo com inicialização, pré-limpeza, SR, alpha e arquivos | Ganho vs Anime 6B GPU |
|---|---:|---:|
| Anime 6B | 9,14 s | 1,00× |
| SwinIR Lightweight | 11,34 s | 0,81× |
| Real-CUGAN sem denoise | 8,37 s | 1,09× |
| Real-CUGAN conservador | 8,16 s | 1,12× |

Esses tempos são uma execução por perfil e não incluem o passe comum de máscara. Não têm a mesma robustez estatística das três repetições do benchmark de inferência. A GPU muda de modelo em subprocessos novos, portanto a carga de dependências/pesos dilui bastante a diferença de inferência.

O passe comum de máscara em GPU (Anime 6B + BiRefNet + limpeza e gravação) levou 16,98 s. BiRefNet isolado, nos mesmos oito RGBs de 512 px e entrada do modelo de 1024 px:

### POC SwinIR-M ClassicalSR DF2K x2

Checkpoint oficial `001_classicalSR_DF2K_s64w8_SwinIR-M_x2.pth`, SHA256 `2032ebf8f401dd3ce2fae5f3852117cb72101ec6ed8358faa64c2a3fa09ed4ac`, 11.752.487 parâmetros. Na mesma comparação de quatro imagens e três repetições, obteve **1,302 s/célula**, **740 MiB** de VRAM alocada, **31,88 dB PSNR** e **0,9807 SSIM**. O SwinIR Lightweight anterior obteve 0,364 s, 392 MiB, 31,10 dB e 0,9778 SSIM.

Ganho do SwinIR-M sobre o Lightweight: **+0,78 dB PSNR**, **+0,0029 SSIM**, ao custo de **3,58× no tempo** e **1,89× na VRAM**. Em relação ao ESRGAN atual na GPU, o SwinIR-M é **8,47× mais lento** no benchmark sintético, mas teve **+7,60 dB PSNR** e **+0,3291 SSIM** nessa métrica de reconstrução. O resultado visual sugere linhas finas/hachuras mais preservadas; ainda é uma avaliação de reconstrução artificial, não prova de detalhe artístico correto.

A integração com a máscara aprovada também processou oito células em 18,473 s totais, com o SR medido em 12,885 s. O alpha aprovado foi mantido. O comparativo limpo seguinte usa um único frame exportado pelo Blender, reduzido para 256 e novamente ampliado para 512.

| BiRefNet FP32 | Processamento das oito imagens | Processo completo | Pico de VRAM alocada |
|---|---:|---:|---:|
| CPU | 105,126 s | 109,828 s | — |
| GPU | 2,767 s | 7,802 s | 1.690 MiB |

Ganho: **37,99×** no processamento ou **14,08×** contando inicialização. Máscaras CPU/GPU: **IoU 1,0 e zero pixels diferentes em 2.097.152 pixels**; repetição GPU também idêntica. Os quatro perfis de SR preservaram exatamente o alpha aprovado nos oito frames. Isso comprova equivalência nesta amostra, não para qualquer imagem possível.

A função de orquestração usada pelo servidor também foi executada com Real-CUGAN conservador, GPU e oito frames. Concluiu em **32,507 s**, produzindo as quatro variantes (`original`, `frame_adjustment`, `color_cohesion_256`, `color_cohesion_128`), em `work/postprocess-benchmark/full-orchestration-v1`. Não foi medido um equivalente completo em CPU: não extrapolar o ganho da inferência como ganho de toda a orquestração.

Validação automatizada: **23 testes passaram, sem skips**, cobrindo runtime, propagação dos comandos existentes, alpha, limpeza chroma, paletas e pós-processamento de atlas. Os testes de máscara CPU/GPU e execução neural são ensaios reais adicionais aos testes unitários.

Benchmark SR: quatro imagens de referência em 512 px, reduzidas a 256 px por bicubic; três repetições após aquecimento. Tempos sincronizados, incluindo transferências e resize, excluindo PNG e inicialização. A baseline CPU usa os mesmos pesos do ESRGAN atual, no mesmo backend, com quatro threads. Não é o tempo histórico de uma execução antiga completa.

PSNR/SSIM são métricas de reconstrução nesse teste sintético, favorecendo modelos treinados para essa degradação. Não demonstram ganho percentual de qualidade artística nem generalização para qualquer sprite. O PSNR de foreground reduz a influência do fundo escuro, usando a mesma seleção de pixels para todos os modelos.

A validação de integração usa oito frames reais de 256 px exportados pelo Blender. Eles são usados como entradas do pós-processamento; super-resolução não altera pose nem geometria.

## Leitura visual e recomendação

SwinIR reteve melhor as linhas finas/hachuras e gradientes da amostra sintética; custa mais tempo de GPU que Anime 6B. Real-CUGAN é a opção rápida para teste artístico, mas simplifica detalhes; a versão conservadora suaviza mais alguns contornos. Anime 6B torna linhas e cores mais fortes, porém altera mais a referência de alta resolução. Esses efeitos são preferências de tratamento, não uma única escala de qualidade.

**Recomendação:** experimentar SwinIR quando a prioridade for conservar o desenho e Real-CUGAN conservador quando a prioridade for vazão. Revisar a animação completa no tamanho final do jogo antes de mudar o padrão do projeto.

Limites: uma GPU, uma família visual, poucos exemplos e nenhum teste de estabilidade temporal compensada por movimento. Real-CUGAN usa contexto global: o backend processa o frame inteiro para preservar suas estatísticas; não promete tiling equivalente nem memória limitada para imagens arbitrariamente grandes. Os testes de VRAM se referem às resoluções medidas.

O lineart do Blender ou inferido pelo detector é exportado como uma camada branca separada. Ele é útil como reforço estrutural, mas não contém automaticamente os detalhes da armadura gerada. Caso o alinhamento entre geração e render estrutural divirja, reduzir `--lineart-strength` ou revisar a célula é necessário; não se deve considerar a camada como substituto da validação visual.

Na POC de `lineart_standard`, o detector foi executado por frame em CUDA depois do upscale e antes da gravação do atlas separado. Oito frames 256→512 com SwinIR-M levaram **19,741 s** totais, sendo **12,921 s** do SR; o spritesheet de cores não recebeu pixels de lineart e o alpha permaneceu byte a byte igual ao passe sem lineart. O lineart inferido acompanha o frame gerado, enquanto o lineart estático do conceito não deve ser colado em poses diferentes.

Na POC de `lineart_anime`, o mesmo contrato foi executado em **19,539 s** totais, sendo **12,905 s** do SR, com CUDA e alpha limitado ao mesmo passe aprovado. O modo permanece disponível para comparação, mas o mapa original do conceito apresentou textura espúria no fundo preto; revisar visualmente antes de adotá-lo como padrão.

Na POC de `lineart_coarse`, o detector `LineartDetector(coarse=True)` também foi executado por frame em CUDA depois do upscale e exportado apenas no atlas separado. Os oito frames 256→512 com SwinIR-M levaram **19,838 s** totais, sendo **12,904 s** do SR; o pico informado pelo SwinIR-M foi de aproximadamente **740 MiB** e o alpha aprovado foi preservado. O modo produz um mapa mais denso/agressivo que `lineart_standard`, portanto fica disponível para comparação, mas `lineart_standard` continua sendo a escolha inicial para preservar lineart sem engrossar excessivamente os contornos.
