# POC: ChatGPT local

Selecione **ChatGPT local · POC (tarefa local)** no AI Render. O endpoint existente
`POST /api/gemini-render` aceita `provider: "chatgpt-local"` e
`model: "gpt-6-astra"`. Cada tarefa é criada explicitamente com
`model: "gpt-6-astra"` e `thinking: "ultra"`, independentemente do modelo
padrão configurado no aplicativo (por exemplo, Luna XHigh). Mantém o contrato
e a ordem das referências locais.

Esta implementação cria uma tarefa **Codex local dentro do aplicativo ChatGPT**,
com geração integrada da sessão. Não é o envio de anexos para um chat comum e não
seleciona nem garante que a ferramenta integrada seja uma versão específica de
Image 2.5. Não lê cookies nem usa a Images API.

Antes de iniciar o servidor, configure `GENERATION_CHATGPT_CALLER_THREAD` com o
ID da tarefa local que hospeda a ponte (ou herde `CODEX_THREAD_ID`). Mantenha o
aplicativo aberto, autenticado e com a geração integrada disponível. As aprovações
exigidas pelo aplicativo continuam valendo. Reinicie o servidor após mudar o ambiente.
`GENERATION_CHATGPT_PIPE` pode fixar o socket; por padrão a descoberta consulta
somente catálogos em `/tmp/codex-browser-use/*.sock`.
`GENERATION_CHATGPT_TIMEOUT` define a espera em segundos (padrão 900).
`GENERATION_SWINIR_PYTHON`, `GENERATION_SWINIR_PROFILE`,
`GENERATION_SWINIR_DEVICE`, `GENERATION_SWINIR_PRECISION` e
`GENERATION_SWINIR_TIMEOUT` controlam a etapa local de super-resolução. O padrão
usa `/home/ggnp/pose-venv/bin/python`, perfil `swinir_m_classical_df2k_x2`,
dispositivo automático, FP32 e 900 segundos.

O job salva o pedido em `gemini_output.request.json` e um recibo
`chatgpt_bridge.json`. A tarefa devolve a imagem no resultado da conversa e a
ponte grava esse retorno como `chatgpt_original.png`.
O arquivo recebido é reduzido com Lanczos para `chatgpt_1024.png` e passa pelo
SwinIR 2×, persistido como `chatgpt_swinir_2048.png`, antes de ser promovido a
`gemini_output.png`. Isso **não significa geração nativa em 2048×2048**.
O recibo registra o modelo GPT da tarefa (`task_model`), o esforço
(`task_thinking`), dimensões originais, dimensões finais e `resized`.
Imagens não quadradas são rejeitadas para não deformar o grid. A POC solicita
fundo limegreen `#00FF00`, como o Gemini; o fundo é parte do contrato visual e
não transparência.

Se o envio expirar, o estado é `dispatch_uncertain`; não há reenvio automático.
Confira a tarefa no aplicativo antes de criar outro job. Timeout de geração não
cancela a tarefa remota. O recibo e o original ficam disponíveis para diagnóstico.
Esta POC não implementa recuperação automática de jobs após reiniciar o servidor,
cancelamento remoto ou acompanhamento de aprovações. O protocolo local é interno
ao aplicativo e pode mudar. O uso depende dos limites e capacidades da sessão.

Verificação: `python3 -m unittest discover -s sprite-lab/tests -p 'test_chatgpt_bridge.py'`
a partir da raiz do projeto.
