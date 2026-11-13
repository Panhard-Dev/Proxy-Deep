"""Envia automaticamente contas locais ao Worker, sem leitura remota."""
import json
import threading
import urllib.error
import urllib.request
import contas_proxy

URL = 'https://deepai-sync-vault.deepai-sync-143b93.workers.dev'
LOCK = threading.Lock()

class SemRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

def _post(path, payload):
    data=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    request=urllib.request.Request(URL+path,data=data,method='POST',headers={
        'User-Agent':'Proxy-Deep/2.0', 'Content-Type':'application/json', 'Cache-Control':'no-store'})
    with urllib.request.build_opener(SemRedirect()).open(request,timeout=20) as response:
        return json.load(response)

def sincronizar():
    with LOCK:
        accounts=contas_proxy.carregar()
        for account in accounts:
            _post('/accounts',{'account':account})
        return {'contas':len(accounts)}

def recuperar(senha):
    # Invocado apenas explicitamente, sem guardar a senha.
    if not senha:raise ValueError('Senha necessária.')
    result=_post('/accounts/retrieve',{'password':senha})
    accounts=result.get('accounts')
    if not isinstance(accounts,list) or not all(isinstance(c,dict) and isinstance(c.get('deepai_key'),str) and c['deepai_key'] for c in accounts):
        raise ValueError('Resposta inválida.')
    return accounts
