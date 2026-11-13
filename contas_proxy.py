"""Armazenamento local e seleção de contas do proxy."""
import json
import os
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uso_proxy
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent / 'contas.json'
_lock = threading.Lock()
_proxima = 0
_bloqueadas = {}

def carregar():
    if not ARQUIVO.exists(): return []
    with ARQUIVO.open(encoding='utf-8') as arquivo:
        contas = json.load(arquivo)
    if not isinstance(contas, list) or not all(isinstance(c, dict) and c.get('deepai_key') for c in contas):
        raise ValueError('Arquivo de contas inválido')
    return contas

def salvar(contas):
    temporario = ARQUIVO.with_name('contas.json.tmp')
    fd = os.open(temporario, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as arquivo:
            json.dump(contas, arquivo, ensure_ascii=False)
        os.replace(temporario, ARQUIVO)
        try: os.chmod(ARQUIVO, 0o600)
        except OSError: pass
    finally:
        if temporario.exists(): temporario.unlink()

def abrir(url, body, extra_headers, instance, timeout=930):
    global _proxima
    inicio_chamada = time.monotonic()
    try:
        payload = json.loads(body) if body else {}
    except (ValueError, TypeError): payload = {}
    monitorar = body is not None and isinstance(payload, dict)
    contas = carregar()
    if not contas: raise ValueError('Nenhuma conta configurada')
    with _lock:
        inicio = _proxima % len(contas)
        _proxima = (_proxima + 1) % len(contas)
        ordem = list(range(inicio, len(contas))) + list(range(inicio))
        ativos = [i for i in ordem if _bloqueadas.get(contas[i]['deepai_key'], 0) <= time.monotonic()]
        ordem = ativos or ordem[:1]
    ultimo = None
    for i in ordem:
        conta = contas[i]
        headers = {'X-DeepAI-Code-Theos': '4', 'X-DeepAI-Code-Instance': instance,
                   'Api-Key': conta['deepai_key'], **extra_headers}
        if conta.get('machine'): headers['X-DeepAI-Code-Machine'] = conta['machine']
        req = urllib.request.Request(url, data=body, headers=headers, method='POST' if body is not None else 'GET')
        try:
            resposta = urllib.request.urlopen(req, timeout=timeout)
            if not monitorar: return resposta
            stream = 'text/event-stream' in resposta.headers.get('Content-Type', '').lower()
            return uso_proxy.Monitor(resposta, payload.get('model','desconhecido'), urllib.parse.urlsplit(url).path, i + 1, inicio_chamada, stream, payload)
        except urllib.error.HTTPError as erro:
            if erro.code not in (401, 403, 429):
                if monitorar: uso_proxy.registrar(payload.get('model','desconhecido'), urllib.parse.urlsplit(url).path, i+1, erro.code, inicio_chamada, streaming=bool(payload.get('stream')))
                raise
            try: segundos = min(3600, max(30, int(erro.headers.get('Retry-After', '60'))))
            except (ValueError, TypeError): segundos = 60
            with _lock: _bloqueadas[conta['deepai_key']] = time.monotonic() + segundos
            if ultimo: ultimo.close()
            ultimo = erro
    if ultimo:
        if monitorar: uso_proxy.registrar(payload.get('model','desconhecido'), urllib.parse.urlsplit(url).path, None, ultimo.code, inicio_chamada, streaming=bool(payload.get('stream')))
        raise ultimo
    raise OSError('Nenhuma conta disponível')
