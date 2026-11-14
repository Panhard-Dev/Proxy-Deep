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

## Diagnóstico

Execute `python3 diagnostico_proxy.py` no Linux/macOS ou `py -3 diagnostico_proxy.py` no Windows para verificar contas e proteção de tráfego. O proxy limita cada conta a 60 requisições por segundo; ao atingir o limite, pausa a conta por cinco minutos e tenta outra. Uma resposta HTTP 429 também pausa a conta, respeitando uma espera maior quando informada pelo serviço. O controle não impõe limite diário de requisições e não garante que o provedor aceite todas as chamadas.

Quando a DeepAI rejeita uma conversa por excesso de contexto, o proxy tenta resumir o histórico anterior com o mesmo modelo e repetir a requisição. A recuperação funciona em Chat Completions, Responses e Messages, preservando as instruções, o turno atual e, quando possível, turnos recentes completos. O resumo pode perder detalhes e gera chamadas adicionais ao provedor. Se não houver histórico anterior, o histórico anterior contiver imagens, o resumo falhar ou o turno atual sozinho ultrapassar o limite, o erro continua sendo retornado ao cliente.
