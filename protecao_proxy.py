"""Limites e espera por conta, compartilhados entre processos locais."""
import hashlib
import math
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parent / '.limite.sqlite3'
MAX_REQUISICOES = 60
ESPERA = 300


class ContaLimitada(Exception):
    def __init__(self, segundos, motivo='rps'):
        self.segundos = max(1, math.ceil(segundos))
        self.motivo = motivo
        super().__init__('Conta em espera.')


def _identidade(chave):
    return hashlib.sha256(chave.encode('utf-8')).hexdigest()


def _conectar():
    fd = os.open(ARQUIVO, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)
    db = sqlite3.connect(str(ARQUIVO), timeout=10, isolation_level=None)
    db.execute('CREATE TABLE IF NOT EXISTS requisicoes (conta TEXT NOT NULL, instante REAL NOT NULL)')
    db.execute("CREATE TABLE IF NOT EXISTS bloqueios (conta TEXT PRIMARY KEY, ate REAL NOT NULL, motivo TEXT NOT NULL DEFAULT 'rps')")
    return db


def reservar(chave):
    """Reserva chamada e aplica tetos por conta, incluindo tentativas sem sucesso."""
    conta = _identidade(chave)
    with closing(_conectar()) as db:
        db.execute('BEGIN IMMEDIATE')
        agora = time.time()
        row = db.execute('SELECT ate, motivo FROM bloqueios WHERE conta = ?', (conta,)).fetchone()
        if row and row[0] > agora:
            db.execute('COMMIT')
            raise ContaLimitada(row[0] - agora, row[1])
        db.execute('DELETE FROM bloqueios WHERE ate <= ?', (agora,))
        db.execute('DELETE FROM requisicoes WHERE instante <= ? OR instante > ?', (agora - 1, agora + 1))
        count = db.execute('SELECT COUNT(*) FROM requisicoes WHERE conta = ?', (conta,)).fetchone()[0]
        if count >= MAX_REQUISICOES:
            db.execute('INSERT OR REPLACE INTO bloqueios(conta, ate) VALUES (?, ?)', (conta, agora + ESPERA))
            db.execute('COMMIT')
            raise ContaLimitada(ESPERA)
        db.execute('INSERT INTO requisicoes(conta, instante) VALUES (?, ?)', (conta, agora))
        if count + 1 >= MAX_REQUISICOES:
            db.execute("INSERT INTO bloqueios(conta, ate, motivo) VALUES (?, ?, 'rps') ON CONFLICT(conta) DO UPDATE SET motivo = CASE WHEN excluded.ate >= ate THEN excluded.motivo ELSE motivo END, ate = MAX(ate, excluded.ate)", (conta, agora + ESPERA))
        db.execute('COMMIT')


def bloquear(chave, segundos=ESPERA, motivo='rps', minimo=ESPERA):
    conta = _identidade(chave)
    with closing(_conectar()) as db:
        db.execute('BEGIN IMMEDIATE')
        ate = time.time() + max(minimo, segundos)
        db.execute('INSERT INTO bloqueios(conta, ate, motivo) VALUES (?, ?, ?) ON CONFLICT(conta) DO UPDATE SET motivo = CASE WHEN excluded.ate >= ate THEN excluded.motivo ELSE motivo END, ate = MAX(ate, excluded.ate)', (conta, ate, motivo))
        db.execute('COMMIT')


def situacao(chave):
    conta = _identidade(chave)
    with closing(_conectar()) as db:
        agora = time.time()
        quantidade = db.execute('SELECT COUNT(*) FROM requisicoes WHERE conta = ? AND instante > ? AND instante <= ?', (conta, agora - 1, agora + 1)).fetchone()[0]
        bloqueio = db.execute('SELECT ate FROM bloqueios WHERE conta = ?', (conta,)).fetchone()
    espera = max(0, math.ceil(bloqueio[0] - agora)) if bloqueio else 0
    return {'rps': quantidade, 'limite': MAX_REQUISICOES, 'espera': espera}


def retry_after(valor, padrao=60):
    """Aceita Retry-After em segundos ou data HTTP."""
    try:
        segundos = float(valor)
        if not math.isfinite(segundos): raise ValueError()
    except (TypeError, ValueError):
        try:
            data = parsedate_to_datetime(valor)
            if data.tzinfo is None: data = data.replace(tzinfo=timezone.utc)
            segundos = (data - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError): segundos = padrao
    return max(1, math.ceil(segundos))
