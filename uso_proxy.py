"""Histórico local: metadados e uso reportado, sem conteúdo nem credenciais."""
import json
import math
import os
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parent / 'uso.sqlite3'

def conectar():
    con = sqlite3.connect(DB, timeout=10)
    try: os.chmod(DB, 0o600)
    except OSError: pass
    con.execute('''CREATE TABLE IF NOT EXISTS requisicoes
        (id INTEGER PRIMARY KEY, horario REAL NOT NULL, modelo TEXT NOT NULL,
         endpoint TEXT NOT NULL, conta INTEGER, status INTEGER, duracao_ms INTEGER,
         entrada INTEGER, saida INTEGER, total INTEGER, streaming INTEGER NOT NULL)''')
    if "estimado" not in {col[1] for col in con.execute("PRAGMA table_info(requisicoes)")}:
        con.execute("ALTER TABLE requisicoes ADD COLUMN estimado INTEGER NOT NULL DEFAULT 0")
        con.commit()
    return con

def registrar(modelo, endpoint, conta, status, inicio, usage=None, streaming=False, estimativa_entrada=None, estimativa_saida=None):
    usage = usage if isinstance(usage, dict) else {}
    entrada = usage.get('prompt_tokens', usage.get('input_tokens'))
    saida = usage.get('completion_tokens', usage.get('output_tokens'))
    total = usage.get('total_tokens')
    estimado = entrada is None or saida is None
    if entrada is None: entrada = estimativa_entrada
    if saida is None: saida = estimativa_saida
    if total is None and isinstance(entrada, int) and isinstance(saida, int): total = entrada + saida
    with conectar() as con:
        con.execute('INSERT INTO requisicoes(horario,modelo,endpoint,conta,status,duracao_ms,entrada,saida,total,streaming,estimado) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (time.time(), str(modelo)[:200], str(endpoint)[:100], conta, status,
                     int((time.monotonic()-inicio)*1000), entrada, saida, total, int(streaming), int(estimado)))

def listar(limite=100):
    with conectar() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute('SELECT * FROM requisicoes ORDER BY id DESC LIMIT ?', (limite,)).fetchall()
        tot = con.execute('SELECT count(*),sum(entrada),sum(saida),sum(total),sum(CASE WHEN estimado=1 THEN 1 ELSE 0 END) FROM requisicoes').fetchone()
    return {'requisicoes':[dict(r) for r in rows], 'totais':{'chamadas':tot[0], 'entrada':tot[1], 'saida':tot[2], 'tokens':tot[3], 'sem_uso':tot[4] or 0}}

class Monitor:
    def __init__(self, resposta, modelo, endpoint, conta, inicio, streaming, payload):
        self.resposta, self.modelo, self.endpoint, self.conta = resposta, modelo, endpoint, conta
        self.inicio, self.streaming, self.usage = inicio, streaming, None
        self.buffer = b''
        componentes = {k:payload[k] for k in ('messages','input','instructions','tools') if k in payload}
        texto = json.dumps(componentes, ensure_ascii=False, separators=(',',':')) if componentes else ''
        self.entrada_estimada = math.ceil(len(texto)/4) if texto else None
        self.saida_caracteres = 0
    def __getattr__(self, name): return getattr(self.resposta, name)
    def __enter__(self): self.resposta.__enter__(); return self
    def __exit__(self, *args):
        try:
            if self.buffer: self._examinar(self.buffer)
            registrar(self.modelo, self.endpoint, self.conta, 499 if args[0] else self.resposta.status, self.inicio, self.usage, self.streaming, self.entrada_estimada, math.ceil(self.saida_caracteres/4) if self.saida_caracteres else None)
        except (OSError, sqlite3.Error, ValueError, TypeError): pass
        return self.resposta.__exit__(*args)
    def _examinar(self, data):
        data = data.strip()
        if data.startswith(b'data:'): data = data[5:].strip()
        if not data or data == b'[DONE]': return
        try: item = json.loads(data)
        except (ValueError, UnicodeError): return
        if not isinstance(item, dict): return
        usage = item.get('usage')
        if not usage and isinstance(item.get('response'), dict): usage = item['response'].get('usage')
        if isinstance(usage, dict): self.usage = usage
        if self.streaming:
            for choice in item.get('choices') or []:
                delta = choice.get('delta') or {}
                for key in ('content', 'reasoning_content', 'reasoning'):
                    if isinstance(delta.get(key), str) and not (key == 'reasoning' and delta.get('reasoning_content')):
                        self.saida_caracteres += len(delta[key])
                for call in delta.get('tool_calls') or []:
                    function = call.get('function') or {}
                    self.saida_caracteres += len(function.get('arguments') or '')
            if item.get('type') in ('response.output_text.delta','response.reasoning_summary_text.delta','response.function_call_arguments.delta'):
                self.saida_caracteres += len(item.get('delta') or '')
        else:
            choices = item.get('choices') or []
            for choice in choices:
                message = choice.get('message') or {}
                for key in ('content','reasoning_content','reasoning'):
                    if isinstance(message.get(key),str) and not (key == 'reasoning' and message.get('reasoning_content')):
                        self.saida_caracteres += len(message[key])
                for call in message.get('tool_calls') or []:
                    self.saida_caracteres += len((call.get('function') or {}).get('arguments') or '')
            if not choices and item.get('output'):
                self.saida_caracteres += len(json.dumps(item['output'],ensure_ascii=False))
    def _receber(self, data):
        if not data: return
        if self.streaming:
            self.buffer += data
            while b'\n' in self.buffer:
                linha, self.buffer = self.buffer.split(b'\n', 1)
                self._examinar(linha)
            if len(self.buffer) > 1048576: self.buffer = b''
        else: self._examinar(data)
    def read(self, *args):
        data = self.resposta.read(*args); self._receber(data); return data
    def read1(self, *args):
        data = self.resposta.read1(*args); self._receber(data); return data
    def __iter__(self):
        for line in self.resposta:
            self._receber(line)
            yield line
