"""Verificação de arquivos publicados no GitHub."""
import hashlib
import json
import time
import urllib.request
from pathlib import Path

VERSAO = '0.0.1'
RAIZ = Path(__file__).resolve().parent
URL = 'https://api.github.com/repos/Panhard-Dev/Proxy-Deep/git/trees/main?recursive=1'
_cache = None
_consultado = 0.0


def verificar():
    global _cache, _consultado
    agora = time.monotonic()
    if _cache is not None and agora - _consultado < 600:
        return _cache
    resultado = {'versao': VERSAO, 'status': 'indisponivel'}
    try:
        requisicao = urllib.request.Request(URL, headers={'User-Agent': 'Proxy-Deep', 'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(requisicao, timeout=8) as resposta:
            arvore = json.load(resposta)
        if arvore.get('truncated') or not isinstance(arvore.get('tree'), list):
            raise ValueError('Árvore incompleta')
        arquivos = [item for item in arvore['tree'] if item.get('type') == 'blob']
        if not arquivos:
            raise ValueError('Árvore vazia')
        diferente = False
        for item in arquivos:
            caminho = (RAIZ / item['path']).resolve()
            if RAIZ not in caminho.parents:
                raise ValueError('Caminho inválido')
            try:
                dados = caminho.read_bytes()
            except FileNotFoundError:
                diferente = True
                break
            if b'\0' not in dados:
                dados = dados.replace(b'\r\n', b'\n')
            digest = hashlib.sha1(b'blob ' + str(len(dados)).encode() + b'\0' + dados).hexdigest()
            if digest != item['sha']:
                diferente = True
                break
        resultado['status'] = 'atualizacao' if diferente else 'atualizado'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    _cache, _consultado = resultado, agora
    return resultado
