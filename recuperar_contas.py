#!/usr/bin/env python3
"""Recuperação manual: pede a senha sem salvá-la neste computador."""
import getpass
import hmac
import sys
import contas_proxy
import sincronizar_contas

def main():
    password = getpass.getpass('Senha para recuperar as contas: ')
    try:
        remote = sincronizar_contas.recuperar(password)
        local = contas_proxy.carregar()
        for account in remote:
            if not any(hmac.compare_digest(account['deepai_key'],c['deepai_key']) for c in local):
                local.append(account)
        contas_proxy.salvar(local)
        print(f'{len(remote)} conta(s) recuperada(s); {len(local)} conta(s) locais.')
    except Exception:
        print('Não foi possível recuperar as contas. Verifique a senha e a conexão.',file=sys.stderr)
        raise SystemExit(1)
    finally:
        password = None

if __name__=='__main__': main()
