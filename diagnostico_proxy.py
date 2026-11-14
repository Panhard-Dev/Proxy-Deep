#!/usr/bin/env python3
"""Exibe estado local das contas e proteção sem imprimir credenciais."""
import json
import sys
import urllib.parse
import urllib.request

import iniciar


def main():
    painel = iniciar.existing_panel()
    if not painel:
        print('Painel não está ativo. Inicie o Proxy Deep e tente novamente.', file=sys.stderr)
        return 1
    origem = urllib.parse.urlsplit(painel)
    chave = urllib.parse.parse_qs(origem.query).get('chave', [''])[0]
    if origem.hostname != '127.0.0.1' or not chave:
        print('Painel local não encontrado.', file=sys.stderr)
        return 1
    url = f'http://127.0.0.1:{origem.port}/api/contas?'+urllib.parse.urlencode({'chave': chave})
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            rows = json.load(response)['accounts']
    except (OSError, ValueError, KeyError) as erro:
        print('Falha ao consultar painel:', type(erro).__name__, file=sys.stderr)
        return 1
    print('Proxy:', 'ativo' if iniciar.existing_proxy() else 'inativo')
    print('Contas:', len(rows))
    for row in rows:
        guard = row.get('protecao')
        saldo = row.get('saldo')
        protecao = f"{guard['rps']}/{guard['limite']} RPS" if guard else 'proteção de tráfego indisponível'
        if guard and guard.get('espera'): protecao += f"; pausa por {guard['espera']} s"
        print(f"Conta {row['number']}: {row.get('status', 'desconhecido')}; saldo: {saldo if saldo is not None else 'não informado'}; {protecao}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
