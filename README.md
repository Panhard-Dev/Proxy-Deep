# Proxy Deep

Proxy local com painel de modelos, contas e histórico de requisições.

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

## Recuperar contas em outro computador

Na pasta do projeto, execute `py -3 recuperar_contas.py` no Windows ou `python3 recuperar_contas.py` no Linux e macOS. Informe a senha do cofre quando solicitada. As contas recuperadas são mescladas às contas locais; a senha não é salva. Reinicie o proxy para atualizar o painel.

O aplicativo cria um identificador local de máquina na primeira consulta à DeepAI. Mantenha o arquivo `.machine-id` ao atualizar os arquivos do projeto.

## Receber contas automaticamente de outro PC

Nos dois computadores, inicie o Proxy Deep. No computador de destino, abra **Contas**, informe a senha do cofre e clique em **Ativar recebimento**. Enquanto o painel estiver em execução, o aplicativo consulta o Worker a cada dois minutos e adiciona as contas enviadas pelo outro computador, sem duplicar as existentes. A senha fica somente na memória: informe-a novamente após reiniciar o painel. A conexão usa o Worker Cloudflare já configurado; não é necessário domínio ou túnel próprio.
