# Proxy Deep

Proxy local com painel de modelos, contas e histórico de requisições.

APIs compatíveis: OpenAI Chat Completions e Responses, e Anthropic Messages.

## Requisitos

- Windows: o iniciador usa Python instalado ou baixa uma cópia isolada oficial automaticamente.
- Linux e macOS: Python 3.9 ou mais recente.
- Conexão com a internet para instalar dependências e utilizar os modelos.

## Instalação e inicialização

Baixe ou clone o repositório e abra a pasta do projeto.

**Windows:** extraia todos os arquivos do ZIP e execute `Iniciar Proxy.bat`. Se o Python não estiver instalado, o primeiro início precisa de internet para baixá-lo.

**Linux:** execute no terminal:

```sh
./"Iniciar Proxy.sh"
```

**macOS:** execute no terminal:

```sh
./"Iniciar Proxy.command"
```

O inicializador verifica o ambiente, prepara os arquivos locais e abre o painel no navegador. Para iniciar o login temporário, é necessário Microsoft Edge, Brave, Google Chrome ou Chromium. A URL do proxy aparece na página inicial, em **Conexões**.

A URL padrão da API é `http://127.0.0.1:8791/v1` e permanece igual após reiniciar. Se a porta estiver ocupada, encerre o programa que a utiliza ou defina outra porta fixa na variável de ambiente `DEEP_PORT`.

Para adicionar uma conta, clique em **Adicionar conta** no painel e conclua o login na janela aberta.

Quando houver uma atualização, o painel oferece o botão **Atualizar**. Ele baixa e confere os arquivos publicados no GitHub, reinicia o proxy e mantém contas e histórico locais. Em pastas clonadas com Git, alterações locais em arquivos rastreados devem ser salvas antes da atualização. Em instalações extraídas de ZIP, cópias dos arquivos substituídos ficam em `.atualizacao-backup`.

O catálogo mostra a janela de contexto e o limite de saída informados pela DeepAI para cada modelo. Esses valores são fornecidos pelo serviço e podem mudar.

A seção **Contexto**, inspirada no [Magpie](https://github.com/yetone/magpie), mostra uma estimativa da última requisição, separada em instruções, ferramentas, resultados e conversa. A estimativa usa caracteres e não é a contagem do provedor; imagens e histórico mantido fora da requisição não entram no cálculo. O painel guarda apenas os totais em memória, não o texto da conversa.

## Diagnóstico

Execute `python3 diagnostico_proxy.py` no Linux/macOS ou `py -3 diagnostico_proxy.py` no Windows para verificar contas e proteção de tráfego. O proxy limita cada conta a 60 requisições por segundo; ao atingir o limite, pausa a conta por cinco minutos e tenta outra. Uma resposta HTTP 429 também pausa a conta, respeitando uma espera maior quando informada pelo serviço. O controle não impõe limite diário de requisições e não garante que o provedor aceite todas as chamadas.

Quando a DeepAI rejeita uma conversa por excesso de contexto, o proxy tenta resumir o histórico anterior com o mesmo modelo e repetir a requisição. A recuperação funciona em Chat Completions, Responses e Messages, preservando as instruções, o turno atual e, quando possível, turnos recentes completos. O resumo pode perder detalhes e gera chamadas adicionais ao provedor. Se a redução não for possível ou a entrada reduzida ainda exceder a janela, o erro é retornado ao cliente; imagens antigas não são resumidas como texto.

Pedidos de texto muito grandes são divididos em segmentos processados em até quatro chamadas paralelas; o proxy reúne as notas e solicita a resposta final. Históricos textuais extensos também podem ser resumidos antes do envio, pois foi observado truncamento silencioso em algumas entradas. Isso permite trabalhar com entradas maiores que a janela nativa, mas não amplia a janela do modelo: os resumos podem perder detalhes, exigem chamadas adicionais e não garantem resposta correta. O limite de partes é 64; entradas multimodais e instruções ou ferramentas maiores que a janela não são reduzidas por esse mecanismo.

## Memória local

A memória reutiliza resumos de trechos idênticos e guarda mensagens selecionadas do usuário e preferências declaradas explicitamente. Quando o cliente não envia o histórico anterior, o proxy pode acrescentar até três notas relevantes da mesma sessão e projeto. A seleção normaliza acentos e prioriza termos mais específicos no mesmo chat e projeto; para pedidos como “continue”, pode recorrer às notas recentes. A busca não entende sinônimos nem garante correspondência semântica. Ela pode não encontrar ou conservar todos os detalhes; não é uma garantia de memória integral nem armazena automaticamente todas as respostas do modelo.

A memória identifica a conversa automaticamente a partir do identificador enviado pelo cliente ou, na ausência dele, do primeiro pedido de um histórico completo. Projetos informados pelo cliente são separados; sem essa informação, a sessão é usada como limite. Para evitar misturas, históricos com o mesmo primeiro pedido ficam ambíguos e não são associados automaticamente. Um cliente que envia apenas mensagens isoladas sem identificador não permite ao proxy reconhecer com segurança a continuação da conversa; nesse caso as notas não podem ser recuperadas entre requisições. O indicador em **Contexto** mostra se a requisição pôde ser associada a uma conversa.

As notas ficam em `memoria.sqlite3` neste computador até serem apagadas pelo botão **Limpar memória** no painel. Em **Contexto → Ver notas salvas**, é possível consultar as notas locais agrupadas por identificador anônimo de chat e projeto. O arquivo pode conter informação privada em texto; filtros evitam salvar algumas credenciais comuns, mas não detectam todos os segredos. As notas são enviadas ao modelo quando usadas numa requisição.
