"""Armazenamento local e seleção de contas do proxy."""
import json
import io
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import uso_proxy
import protecao_proxy
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent / 'contas.json'
LANE_VERSION = '6'
ARQUIVO_MAQUINA = ARQUIVO.with_name('.machine-id')
_lock = threading.Lock()
_proxima = 0
_bloqueadas = {}

class SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

_opener = urllib.request.build_opener(SemRedirect())

def identificador_maquina():
    try:
        fd = os.open(ARQUIVO_MAQUINA, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w', encoding='ascii') as arquivo:
            arquivo.write(secrets.token_hex(32))
    # Outro processo pode estar terminando de criar o arquivo.
    for _ in range(50):
        valor = ARQUIVO_MAQUINA.read_text(encoding='ascii').strip()
        if re.fullmatch(r'[a-f0-9]{64}', valor): return valor
        time.sleep(.02)
    raise ValueError('Identificador de máquina inválido.')

def identificador_instancia(padrao):
    """Usa no painel a mesma instância que envia requisições ao modelo."""
    try:
        data = json.loads(ARQUIVO.with_name('runtime_proxy.json').read_text(encoding='utf-8'))
        instancia = data.get('instance')
        if isinstance(instancia, str) and re.fullmatch(r'[a-f0-9]{32}', instancia): return instancia
    except (OSError, ValueError, TypeError, AttributeError): pass
    return padrao

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
        agora = time.monotonic()
        ativos = [i for i in ordem if _bloqueadas.get(contas[i]['deepai_key'], 0) <= agora]
        espera_local = min((_bloqueadas.get(contas[i]['deepai_key'], 0) - agora for i in ordem), default=1)
        ordem = ativos
    ultimo = None
    esperas = []
    if not ordem:
        segundos = max(1, math.ceil(espera_local))
        corpo = json.dumps({'error': {'code': 'temporarily_unavailable', 'message': 'Contas temporariamente indisponíveis.'}}).encode()
        raise urllib.error.HTTPError(url, 503, 'Contas temporariamente indisponíveis', {'Retry-After':str(segundos)}, io.BytesIO(corpo))
    for i in ordem:
        conta = contas[i]
        try:
            protecao_proxy.reservar(conta['deepai_key'])
        except protecao_proxy.ContaLimitada as limite:
            esperas.append(limite.segundos)
            continue
        except (OSError, sqlite3.Error) as erro:
            corpo = json.dumps({'error': {'code': 'rate_guard_unavailable', 'message': 'Não foi possível verificar o limite local de requisições.', 'retryable': True}}).encode()
            raise urllib.error.HTTPError(url, 503, 'Proteção de tráfego indisponível', {'Retry-After':'5'}, io.BytesIO(corpo)) from erro
        headers = {'X-DeepAI-Code-Theos': LANE_VERSION, 'X-DeepAI-Code-Instance': instance,
                   'X-DeepAI-Code-Machine': identificador_maquina(),
                   'Api-Key': conta['deepai_key'], **extra_headers}
        req = urllib.request.Request(url, data=body, headers=headers, method='POST' if body is not None else 'GET')
        try:
            resposta = _opener.open(req, timeout=timeout)
            if not monitorar: return resposta
            stream = 'text/event-stream' in resposta.headers.get('Content-Type', '').lower()
            return uso_proxy.Monitor(resposta, payload.get('model','desconhecido'), urllib.parse.urlsplit(url).path, i + 1, inicio_chamada, stream, payload)
        except urllib.error.HTTPError as erro:
            if erro.code not in (401, 403, 429):
                if monitorar: uso_proxy.registrar(payload.get('model','desconhecido'), urllib.parse.urlsplit(url).path, i+1, erro.code, inicio_chamada, streaming=bool(payload.get('stream')))
                raise
            if erro.code == 429:
                segundos = max(protecao_proxy.ESPERA, protecao_proxy.retry_after(erro.headers.get('Retry-After'), padrao=protecao_proxy.ESPERA))
                try:
                    protecao_proxy.bloquear(conta['deepai_key'], segundos)
                except (OSError, sqlite3.Error) as falha:
                    corpo = json.dumps({'error': {'code': 'rate_guard_unavailable', 'message': 'Não foi possível registrar a pausa da conta.'}}).encode()
                    raise urllib.error.HTTPError(url, 503, 'Proteção de tráfego indisponível', {'Retry-After':'5'}, io.BytesIO(corpo)) from falha
                esperas.append(segundos)
            else:
                segundos = protecao_proxy.retry_after(erro.headers.get('Retry-After'), padrao=60)
                with _lock: _bloqueadas[conta['deepai_key']] = time.monotonic() + segundos
            if ultimo: ultimo.close()
            ultimo = erro
    if ultimo:
        if ultimo.code == 429 and esperas:
            ultimo.headers['Retry-After'] = str(min(esperas))
        if monitorar: uso_proxy.registrar(payload.get('model','desconhecido'), urllib.parse.urlsplit(url).path, None, ultimo.code, inicio_chamada, streaming=bool(payload.get('stream')))
        raise ultimo
    if esperas:
        segundos = min(esperas)
        corpo = json.dumps({'error': {'code': 'rate_limited', 'message': 'Todas as contas estão em espera pelo limite de requisições.'}}).encode()
        raise urllib.error.HTTPError(url, 429, 'Contas em espera', {'Retry-After': str(segundos)}, io.BytesIO(corpo))
    raise OSError('Nenhuma conta disponível')
