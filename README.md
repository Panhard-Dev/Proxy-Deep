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

Para adicionar uma conta, clique em **Adicionar conta** no painel e conclua o login na janela aberta.

## Diagnóstico

Execute `python3 diagnostico_proxy.py` no Linux/macOS ou `py -3 diagnostico_proxy.py` no Windows para verificar contas e proteção de tráfego. O proxy limita cada conta a 60 requisições por segundo; ao atingir o limite, pausa a conta por cinco minutos e tenta outra. Uma resposta HTTP 429 também pausa a conta, respeitando uma espera maior quando informada pelo serviço. O controle não impõe limite diário de requisições e não garante que o provedor aceite todas as chamadas.
