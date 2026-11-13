#!/usr/bin/env python3
import ast, html, hmac, json, os, secrets, shutil, subprocess, sys, tempfile, threading, urllib.error, urllib.parse, urllib.request, webbrowser, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import contas_proxy
import uso_proxy
import sincronizar_contas
import proxy
import iniciar
import socket
import time
from concurrent.futures import ThreadPoolExecutor
BASE="https://api.deepai.org/deepai_code"
UI=os.environ.get('DEEPAI_PAINEL_TOKEN') or secrets.token_urlsafe(32)
trava=threading.Lock()

def abrir_login_temporario(url):
    candidatos = ['chromium', 'chromium-browser', 'google-chrome', 'chrome', 'brave-browser', 'microsoft-edge', 'msedge']
    navegador = next((shutil.which(nome) for nome in candidatos if shutil.which(nome)), None)
    if not navegador:
        achados = sorted((Path.home()/'.cache/ms-playwright').glob('chromium-*/chrome-linux64/chrome'), reverse=True)
        navegador = str(achados[0]) if achados else None
    if not navegador:
        raise RuntimeError('Chromium não encontrado')
    perfil = tempfile.mkdtemp(prefix='deepai-login-')
    try:
        processo = subprocess.Popen([navegador, '--user-data-dir='+perfil, '--no-first-run',
                                     '--no-default-browser-check', '--new-window', url],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        shutil.rmtree(perfil, ignore_errors=True)
        raise
    def limpar():
        processo.wait()
        shutil.rmtree(perfil, ignore_errors=True)
    threading.Thread(target=limpar, daemon=True).start()

def carregar():
    return contas_proxy.carregar()

def salvar(contas):
    contas_proxy.salvar(contas)
    threading.Thread(target=sincronizar_em_fundo,daemon=True).start()

sync_lock=threading.Lock()
sync_state={'status':'não configurada','mensagem':'Sincronização não configurada.'}
def sincronizar_em_fundo():
    global sync_state
    with sync_lock:
        sync_state={'status':'sincronizando','mensagem':'Sincronizando contas...'}
        try:
            with trava: resultado=sincronizar_contas.sincronizar()
            sync_state={'status':'ok','mensagem':f"Sincronizado: {resultado['contas']} conta(s)."}
        except Exception:
            sync_state={'status':'erro','mensagem':'Não foi possível sincronizar; tente novamente.'}

def ciclo_sincronizacao():
    while True:
        sincronizar_em_fundo()
        time.sleep(120)

saldo_cache={}
saldo_lock=threading.Lock()
def consultar_saldo(index, conta, force=False):
    # Cache por credencial sem enviar ou mostrar a chave ao navegador.
    import hashlib
    fingerprint=hashlib.sha256(conta['deepai_key'].encode()).hexdigest()
    with saldo_lock:
        entry=saldo_cache.get(fingerprint)
        if not force and entry and time.monotonic()-entry[0]<120:
            return {'number':index+1, **entry[1]}
    headers={'Api-Key':conta['deepai_key'],'X-DeepAI-Code-Theos':'4',
             'X-DeepAI-Code-Instance':UI[:24]}
    if conta.get('machine'): headers['X-DeepAI-Code-Machine']=conta['machine']
    value={'status':'indisponível','saldo':None,'concessao':None,'renova':None}
    try:
        req=urllib.request.Request(BASE+'/theos',headers=headers)
        with urllib.request.urlopen(req,timeout=12) as response:
            payload=json.load(response)
        def numero(x):
            from decimal import Decimal, InvalidOperation
            try:
                n=Decimal(str(x))
                if not n.is_finite() or n<0 or n>1000000000:return None
                return str(n)
            except (InvalidOperation, ValueError, TypeError):return None
        value={'status':'conectada','saldo':numero(payload.get('balance')),
               'concessao':numero(payload.get('granted')),
               'renova':str(payload.get('refresh_at') or '')[:64]}
    except urllib.error.HTTPError as e:
        value['status']='limite' if e.code==429 else 'falha de autenticação' if e.code in (401,403) else 'indisponível'
    except (OSError, ValueError, TypeError):pass
    with saldo_lock:saldo_cache[fingerprint]=(time.monotonic(),value)
    return {'number':index+1, **value}

verificacao_lock=threading.Lock()
verificacao_ativa=False

def iniciar_verificacao():
    global verificacao_ativa
    with verificacao_lock:
        if verificacao_ativa: return
        verificacao_ativa=True
        with iniciar.LOCK:
            for passo in iniciar.STEPS: passo.update(status='pending',detail='Aguardando')
            iniciar.STATE.update(done=False,panel=None,message='')
    def executar():
        global verificacao_ativa
        try: iniciar.prepare()
        finally:
            with verificacao_lock: verificacao_ativa=False
    threading.Thread(target=executar,daemon=True).start()

class SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def enviar(self,status,conteudo, content_type='text/html; charset=utf-8'):
        dados=conteudo.encode('utf-8');self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(dados)));self.end_headers();self.wfile.write(dados)
    def json(self,status,valor):
        return self.enviar(status,json.dumps(valor,ensure_ascii=False),'application/json; charset=utf-8')
    def pagina(self,mensagem=''):
        return (Path(__file__).resolve().parent/'painel.html').read_text(encoding='utf-8')
    def do_GET(self):
        u=urllib.parse.urlsplit(self.path);q=urllib.parse.parse_qs(u.query)
        if u.path == '/marca.png':
            dados=(Path(__file__).resolve().parent/'marca.png').read_bytes()
            self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(dados)));self.send_header('Cache-Control','max-age=3600');self.end_headers();self.wfile.write(dados);return
        if u.path.startswith('/logos/') and u.path.endswith('.svg'):
            name=u.path.removeprefix('/logos/')
            if name not in {'anthropic.svg','google.svg','deepseek.svg','qwen.svg','xiaomi.svg','nvidia.svg','mistralai.svg','alibabacloud.svg','huggingface.svg','meta.svg','github.svg','openai.svg','zhipu.svg','minimax.svg','moonshot.svg','kimi.svg','upstage.svg','deepai.svg','mistral.svg'}:
                return self.enviar(404,'Não encontrado.')
            try: return self.enviar(200,(Path(__file__).resolve().parent/'logos'/name).read_text(encoding='utf-8'),'image/svg+xml; charset=utf-8')
            except OSError:return self.enviar(404,'Não encontrado.')
        if u.path in ('/painel.css','/painel.js','/marca.svg','/inicio.css','/inicio_painel.js'):
            mime=('text/css; charset=utf-8' if u.path.endswith('.css') else 'image/svg+xml; charset=utf-8' if u.path.endswith('.svg') else 'application/javascript; charset=utf-8')
            return self.enviar(200,(Path(__file__).resolve().parent/u.path[1:]).read_text(encoding='utf-8'),mime)
        autorizado=hmac.compare_digest(q.get('chave',[''])[0],UI)
        if u.path=='/' and autorizado:
            tela=(Path(__file__).resolve().parent/'verificacao.html').read_text(encoding='utf-8')
            return self.enviar(200,tela.replace('/inicio.js','/inicio_painel.js'))
        if u.path=='/painel' and autorizado: return self.enviar(200,self.pagina())
        if u.path=='/api/verificacao/estado' and autorizado:
            with iniciar.LOCK:
                estado=json.loads(json.dumps(iniciar.STATE))
            if estado.get('panel'):
                estado['panel']=f'http://127.0.0.1:{self.server.server_port}/painel?chave={UI}'
            return self.json(200,estado)
        if u.path=='/api/painel' and autorizado:
            with trava: contas=carregar()
            runtime={}
            try:
                runtime=json.loads((Path(__file__).resolve().parent/'runtime_proxy.json').read_text())
                port=int(runtime.get('port',0))
                with socket.create_connection(('127.0.0.1',port),timeout=.4): pass
                runtime={'url':f'http://127.0.0.1:{port}/v1','online':True}
            except (OSError,ValueError,TypeError,AttributeError): runtime={'online':False}
            return self.json(200,{'accounts':[{'number':i+1} for i in range(len(contas))],'usage':uso_proxy.listar(),'runtime':runtime})
        if u.path=='/api/contas' and autorizado:
            with trava: contas=carregar()
            with ThreadPoolExecutor(max_workers=min(6,max(1,len(contas)))) as executor:
                rows=list(executor.map(lambda item:consultar_saldo(*item,force=q.get('forcar',[''])[0]=='1'),enumerate(contas)))
            return self.json(200,{'accounts':rows})
        if u.path=='/api/modelos' and autorizado:
            try: return self.json(200,{'models':[proxy._core.public_model(m) for m in proxy._core.load_catalog()]})
            except (OSError,ValueError,KeyError): return self.json(502,{'error':'Catálogo indisponível'})
        if u.path!='/auth-callback': return self.enviar(404,'Não encontrado.')
        state=q.get('state',[''])[0];code=q.get('code',[''])[0]
        if not code or len(code)>4096: return self.enviar(400,'Login inválido ou expirado.')
        with trava:
            esperado=getattr(self.server,'estado_login','')
            if not esperado or not hmac.compare_digest(state,esperado): return self.enviar(400,'Login inválido ou expirado.')
            self.server.estado_login=''
        try:
            req=urllib.request.Request(BASE+'/desktop_auth/exchange/',data=json.dumps({'code':code}).encode(),headers={'Content-Type':'application/json'},method='POST')
            with urllib.request.build_opener(SemRedirect()).open(req,timeout=20) as resposta: dados=json.load(resposta)
            key=dados.get('api_key')
            if not isinstance(key,str) or not key or '\n' in key or '\r' in key: raise ValueError()
            with trava:
                contas=carregar();campo='deepai_key'
                duplicada=any(hmac.compare_digest(str(c.get(campo,'')),key) for c in contas)
                if not duplicada:
                    nova={campo:key}
                    if contas and contas[0].get('machine'):nova['machine']=contas[0]['machine']
                    contas.append(nova)
                salvar(contas)
            titulo='Conta já adicionada.' if duplicada else 'Conta adicionada.'
            return self.enviar(200,'<!doctype html><meta charset="utf-8"><h2>'+titulo+'</h2><p>Conta disponível no proxy imediatamente.</p><p>Para adicionar outra conta, volte ao gerenciador e entre com outra conta no navegador.</p><a href="/?chave='+UI+'">Voltar ao gerenciador</a>')
        except (OSError,ValueError,KeyError,RuntimeError,subprocess.TimeoutExpired): return self.enviar(502,'Não foi possível concluir o login ou atualizar o proxy. Reinicie o gerenciador e tente novamente.')
    def do_POST(self):
        path=urllib.parse.urlsplit(self.path).path
        if path not in ('/iniciar','/api/verificacao/iniciar'): return self.enviar(404,'Não encontrado.')
        if self.headers.get('Origin') not in (None,'null',f'http://127.0.0.1:{self.server.server_port}'):
            return self.enviar(403,'Não autorizado.')
        try: size=int(self.headers.get('Content-Length','0'))
        except ValueError:return self.enviar(400,'Inválido.')
        if size<1 or size>4096:return self.enviar(400,'Inválido.')
        q=urllib.parse.parse_qs(self.rfile.read(size).decode('utf-8','replace'))
        if not hmac.compare_digest(q.get('chave',[''])[0],UI):
            return self.enviar(403,'Não autorizado.')
        if path=='/api/verificacao/iniciar':
            iniciar_verificacao()
            return self.json(200,{'started':True})
        base=BASE.removesuffix('/deepai_code')
        with trava:
            self.server.estado_login=secrets.token_hex(32)
            estado=self.server.estado_login
        destino=base+'/deepai_code/desktop_auth/?'+urllib.parse.urlencode({'state':estado,'port':self.server.server_port})
        try:
            abrir_login_temporario(destino)
        except (OSError, RuntimeError):
            return self.json(503,{'message':'Não foi possível abrir o navegador temporário.'})
        return self.json(200,{'message':'Login aberto em uma janela temporária. Conclua o login nela.'})

def main():
    threading.Thread(target=ciclo_sincronizacao,daemon=True).start()
    port=int(os.environ.get('DEEPAI_PAINEL_PORTA','0'))
    servidor=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    url=f'http://127.0.0.1:{servidor.server_port}/?chave={UI}'
    print('Gerenciador DeepAI: '+url,flush=True)
    print('Feche este terminal para encerrar.',flush=True)
    if '--sem-navegador' not in sys.argv: webbrowser.open(url)
    try:servidor.serve_forever()
    except KeyboardInterrupt:pass
    finally:servidor.server_close()
if __name__=='__main__':main()
