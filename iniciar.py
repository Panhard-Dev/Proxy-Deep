#!/usr/bin/env python3
"""Inicia o proxy depois de verificar o ambiente e recuperar as contas."""
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
STEPS = [{'name': n, 'status': 'pending', 'detail': 'Aguardando'} for n in (
    'Python e arquivos do aplicativo', 'Bibliotecas necessárias',
    'Dados locais', 'Contas sincronizadas', 'Proxy e painel')]
STATE = {'steps': STEPS, 'done': False, 'panel': None, 'message': ''}
LOCK = threading.Lock()

def update(index, status, detail):
    with LOCK: STEPS[index].update(status=status, detail=detail)

def private_file(path, mode='w'):
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    return os.fdopen(fd, mode, encoding='utf-8')

def child_env():
    return os.environ.copy()

def ensure_dependencies():
    import ssl, sqlite3
    if not ssl.OPENSSL_VERSION or not sqlite3.sqlite_version:
        raise RuntimeError('Bibliotecas SSL ou SQLite não estão disponíveis no Python.')


def start(args, log_name, environment=None):
    env = child_env(); env.update(environment or {})
    flags = {'start_new_session': True} if os.name != 'nt' else {'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)}
    with private_file(ROOT / log_name) as output:
        return subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT, close_fds=True, **flags)

def existing_proxy():
    try:
        data = json.loads((ROOT / 'runtime_proxy.json').read_text())
        port = int(data['port'])
        if not 1 <= port <= 65535: return False
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
            return response.status == 200 and json.load(response).get('provider') == 'DeepAI'
    except (OSError, ValueError, KeyError, TypeError): return False

def existing_panel():
    try:
        lines = (ROOT / '.painel.log').read_text(encoding='utf-8').splitlines()
        url = next(line.split('Gerenciador DeepAI: ',1)[1] for line in lines if line.startswith('Gerenciador DeepAI: '))
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.port is None: return None
        with urllib.request.urlopen(url,timeout=1) as response:
            if response.status == 200 and b'Proxy Deep' in response.read(250000): return url.replace('/?chave=', '/painel?chave=')
    except (OSError, ValueError, StopIteration): pass
    return None

def prepare():
    stage=0
    try:
        update(0,'running','Conferindo instalação...')
        if sys.version_info < (3,9): raise RuntimeError('É necessário Python 3.9 ou mais recente.')
        for name in ('proxy.py','gerenciar_contas.py','sincronizar_contas.py','contas_proxy.py','uso_proxy.py','anthropic_bridge.py','painel.html','painel.js','painel.css','verificacao.html','inicio.css','inicio.js','inicio_painel.js','marca.png'):
            if not (ROOT/name).is_file(): raise RuntimeError('Arquivo necessário ausente: '+name)
        update(0,'ok','Python e arquivos encontrados.')
        stage=1;update(1,'running','Verificando bibliotecas do Python...');ensure_dependencies();update(1,'ok','Bibliotecas SSL e SQLite prontas.')
        stage=2;update(2,'running','Verificando contas e histórico local...')
        accounts=ROOT/'contas.json'
        if not accounts.exists():
            with private_file(accounts) as f: json.dump([],f)
        with accounts.open(encoding='utf-8') as f: data=json.load(f)
        if not isinstance(data,list) or any(not isinstance(c,dict) or not isinstance(c.get('deepai_key'),str) or not c['deepai_key'] for c in data):
            raise RuntimeError('O arquivo de contas é inválido. Seus dados foram preservados.')
        try: os.chmod(accounts,0o600)
        except OSError: pass
        env=child_env();check=subprocess.run([sys.executable,'-c','import uso_proxy; c=uso_proxy.conectar(); assert c.execute("PRAGMA quick_check").fetchone()[0]=="ok"; c.close()'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        if check.returncode: raise RuntimeError('Não foi possível abrir o histórico local. Seus dados foram preservados.')
        update(2,'ok','Arquivos de contas e histórico disponíveis.')
        stage=3;update(3,'running','Enviando contas locais com segurança...')
        try:
            result=subprocess.run([sys.executable,'-c','import sincronizar_contas as s; r=s.sincronizar(); print(r["contas"])'],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=55)
            if result.returncode: raise RuntimeError('Não foi possível acessar a sincronização.')
            count=int(result.stdout.strip().splitlines()[-1]);update(3,'ok',f'{count} conta(s) enviada(s).' if count else 'Nenhuma conta local; adicione uma no painel.')
        except (subprocess.TimeoutExpired,OSError,ValueError,RuntimeError):
            update(3,'warning','Sincronização indisponível. O painel tentará novamente automaticamente.')
        stage=4;update(4,'running','Iniciando serviços locais...')
        if not existing_proxy():
            port=os.environ.get('DEEP_PORT','0')
            if not port.isdigit() or not 0<=int(port)<=65535: port='0'
            proc=start(['proxy.py','serve','--porta',port],'.proxy.log',{'DEEPAI_PROXY_INTERNAL':'1'})
            for _ in range(100):
                if existing_proxy(): break
                if proc.poll() is not None: raise RuntimeError('O proxy encerrou ao iniciar. Confira .proxy.log.')
                time.sleep(.1)
            else: raise RuntimeError('O proxy demorou para iniciar. Confira .proxy.log.')
        panel=existing_panel()
        if not panel:
            proc=start(['gerenciar_contas.py','--sem-navegador'],'.painel.log')
            for _ in range(100):
                panel=existing_panel()
                if panel: break
                if proc.poll() is not None: raise RuntimeError('O painel encerrou ao iniciar. Confira .painel.log.')
                time.sleep(.1)
            else: raise RuntimeError('O painel demorou para iniciar. Confira .painel.log.')
        update(4,'ok','Proxy e painel iniciados.')
        with LOCK: STATE.update(done=True,panel=panel)
    except (Exception, SystemExit) as exc:
        update(stage,'error',str(exc) or 'Falha durante a verificação.')
        with LOCK: STATE.update(done=True,message='Corrija o erro e inicie novamente.')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        url=urllib.parse.urlsplit(self.path);qs=urllib.parse.parse_qs(url.query)
        if url.path in ('/','/estado') and not secrets.compare_digest(qs.get('chave',[''])[0],TOKEN):
            self.send_error(404);return
        if url.path=='/': data=(ROOT/'verificacao.html').read_bytes();mime='text/html; charset=utf-8'
        elif url.path=='/inicio.js': data=(ROOT/'inicio.js').read_bytes();mime='application/javascript; charset=utf-8'
        elif url.path=='/inicio.css': data=(ROOT/'inicio.css').read_bytes();mime='text/css; charset=utf-8'
        elif url.path=='/marca.png': data=(ROOT/'marca.png').read_bytes();mime='image/png'
        elif url.path=='/estado':
            with LOCK: data=json.dumps(STATE,ensure_ascii=False).encode()
            mime='application/json; charset=utf-8'
        else:self.send_error(404);return
        self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'");self.end_headers();self.wfile.write(data)

class Server(ThreadingHTTPServer):
    daemon_threads=True

def main():
    server=Server(('127.0.0.1',0),Handler)
    url=f'http://127.0.0.1:{server.server_port}/?chave={TOKEN}'
    print('Verificação:',url,flush=True)
    threading.Thread(target=prepare,daemon=True).start()
    if '--sem-navegador' not in sys.argv: webbrowser.open(url)
    try: server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt: pass
    finally: server.server_close()
if __name__=='__main__': main()
