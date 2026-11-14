#!/usr/bin/env python3
"""Recebe no proxy local as contas enviadas ao cofre Cloudflare."""
import getpass
import json
import sys
import time
import urllib.parse
import urllib.request

import iniciar
import sincronizar_contas


def importar(contas):
    painel = iniciar.existing_panel()
    if not painel:
        raise RuntimeError('Inicie o proxy e o painel antes de receber contas.')
    url = urllib.parse.urlsplit(painel)
    chave = urllib.parse.parse_qs(url.query).get('chave', [''])[0]
    if url.hostname != '127.0.0.1' or not chave:
        raise RuntimeError('Painel local não encontrado.')
    destino = f'http://127.0.0.1:{url.port}/api/importar-conta'
    opener = urllib.request.build_opener(sincronizar_contas.SemRedirect())
    for conta in contas:
        dados = urllib.parse.urlencode({'chave': chave, 'conta': conta['deepai_key']}).encode()
        pedido = urllib.request.Request(destino, data=dados, method='POST')
        with opener.open(pedido, timeout=10) as resposta:
            if not json.load(resposta).get('ok'):
                raise RuntimeError('Painel não confirmou a conta.')


def main():
    senha = getpass.getpass('Senha do cofre Cloudflare: ')
    if not senha:
        raise SystemExit('Senha necessária.')
    print('Recebimento ativo. Mantenha esta janela aberta; Ctrl+C encerra.', flush=True)
    while True:
        try:
            contas = sincronizar_contas.recuperar(senha)
            importar(contas)
            print(f'{len(contas)} conta(s) recebida(s) do cofre.', flush=True)
        except Exception as erro:
            print(f'Falha na sincronização ({type(erro).__name__}); nova tentativa em 2 minutos.', file=sys.stderr, flush=True)
        time.sleep(120)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nRecebimento encerrado.')
