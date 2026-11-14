"""Instala a versão publicada preservando dados locais e reinicia os serviços."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

import atualizacao

ROOT = Path(__file__).resolve().parent
STATUS = ROOT / '.atualizacao-status.json'
LOCK = threading.Lock()
_ocupado = False
PROTEGIDOS = {'contas.json', 'runtime_proxy.json', '.machine-id', '.painel.log', '.proxy.log', '.atualizacao-status.json'}


def estado(status, mensagem):
    temp = STATUS.with_suffix('.tmp')
    temp.write_text(json.dumps({'status': status, 'mensagem': mensagem}, ensure_ascii=False), encoding='utf-8')
    os.replace(temp, STATUS)


def ler_estado():
    try:
        data = json.loads(STATUS.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {'status': 'ocioso'}
    except (OSError, ValueError):
        return {'status': 'ocioso'}


def requisicao(url):
    return urllib.request.Request(url, headers={'User-Agent': 'Proxy-Deep', 'Accept': 'application/vnd.github+json'})


def caminho_seguro(nome):
    if not nome or '\\' in nome or '\0' in nome or ':' in nome:
        raise ValueError('Caminho inválido no pacote.')
    path = PurePosixPath(nome)
    if path.is_absolute() or any(p in ('', '.', '..', '.git') for p in path.parts):
        raise ValueError('Caminho inválido no pacote.')
    if path.parts[0] in PROTEGIDOS or any(p.endswith(('.sqlite3', '.sqlite3-wal', '.sqlite3-shm')) for p in path.parts):
        raise ValueError('O pacote contém dados locais protegidos.')
    return path


def preparar():
    """Baixa árvore e arquivo do mesmo commit e confere cada blob Git."""
    with urllib.request.urlopen(requisicao('https://api.github.com/repos/Panhard-Dev/Proxy-Deep/commits/main'), timeout=20) as response:
        commit = json.load(response)
    sha = commit.get('sha', '')
    if len(sha) != 40 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('Commit inválido.')
    tree_sha = commit.get('commit', {}).get('tree', {}).get('sha', '')
    if len(tree_sha) != 40 or any(c not in '0123456789abcdef' for c in tree_sha):
        raise ValueError('Árvore inválida.')
    url_tree = f'https://api.github.com/repos/Panhard-Dev/Proxy-Deep/git/trees/{tree_sha}?recursive=1'
    with urllib.request.urlopen(requisicao(url_tree), timeout=20) as response:
        tree = json.load(response)
    if tree.get('truncated') or tree.get('sha') != tree_sha:
        raise ValueError('Árvore do GitHub incompleta.')
    manifest = {}
    for item in tree.get('tree', []):
        if item.get('type') != 'blob':
            continue
        name = str(caminho_seguro(item['path']))
        if item.get('mode') not in ('100644', '100755') or name in manifest:
            raise ValueError('Arquivo não suportado no pacote.')
        manifest[name] = item
    if not manifest or 'proxy.py' not in manifest or 'gerenciar_contas.py' not in manifest:
        raise ValueError('Pacote de atualização incompleto.')
    directory = Path(tempfile.mkdtemp(prefix='proxy-deep-atualizacao-'))
    try:
        url = f'https://codeload.github.com/Panhard-Dev/Proxy-Deep/zip/{sha}'
        with urllib.request.urlopen(requisicao(url), timeout=60) as response:
            data = response.read(50_000_001)
        if len(data) > 50_000_000:
            raise ValueError('Pacote de atualização grande demais.')
        archive = directory / 'pacote.zip'
        archive.write_bytes(data)
        output = directory / 'arquivos'; output.mkdir()
        encontrados = set()
        tamanho_total = 0
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                parts = PurePosixPath(info.filename).parts
                if len(parts) < 2 or info.is_dir():
                    continue
                name = str(caminho_seguro('/'.join(parts[1:])))
                if name not in manifest or name in encontrados or stat.S_ISLNK(info.external_attr >> 16) or info.file_size > 20_000_000:
                    raise ValueError('Arquivo inesperado no pacote.')
                tamanho_total += info.file_size
                if tamanho_total > 100_000_000:
                    raise ValueError('Pacote descompactado grande demais.')
                content = z.read(info)
                item = manifest[name]
                digest = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
                if digest != item.get('sha') or len(content) != item.get('size'):
                    # O GitHub aplica atributos de fim de linha ao gerar o ZIP.
                    normalizado = content.replace(b'\r\n', b'\n') if b'\0' not in content else content
                    digest = hashlib.sha1(b'blob ' + str(len(normalizado)).encode() + b'\0' + normalizado).hexdigest()
                    if digest != item.get('sha') or len(normalizado) != item.get('size'):
                        raise ValueError('Falha na verificação do arquivo: ' + name)
                target = output.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                if item['mode'] == '100755': target.chmod(0o755)
                encontrados.add(name)
        if encontrados != set(manifest):
            raise ValueError('Pacote de atualização incompleto.')
        (directory / 'manifest.json').write_text(json.dumps({'sha': sha, 'files': list(manifest)}), encoding='utf-8')
        return directory
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def iniciar(porta_painel, token, porta_proxy, instancia):
    global _ocupado
    with LOCK:
        if _ocupado:
            return False
        estado('baixando', 'Baixando e verificando a atualização...')
        _ocupado = True
    def executar():
        global _ocupado
        pasta = None
        iniciado = False
        try:
            pasta = preparar()
            env = os.environ.copy()
            env.update({'DEEPAI_PAINEL_TOKEN': token, 'DEEP_UPDATE_PANEL_PORT': str(porta_painel),
                        'DEEP_UPDATE_PROXY_PORT': str(porta_proxy), 'DEEP_UPDATE_PROXY_INSTANCE': instancia})
            flags = {'start_new_session': True} if os.name != 'nt' else {'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)}
            with (ROOT / '.atualizacao.log').open('a', encoding='utf-8') as log:
                processo = subprocess.Popen([sys.executable, str(ROOT / 'atualizador_worker.py'), str(pasta)], cwd=ROOT, env=env,
                                            stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True, **flags)
                iniciado = True
                processo.wait()
        except Exception as exc:
            if pasta is not None and not iniciado:
                shutil.rmtree(pasta, ignore_errors=True)
            estado('erro', 'Não foi possível preparar a atualização: ' + str(exc))
        finally:
            with LOCK: _ocupado = False
    threading.Thread(target=executar, daemon=True).start()
    return True
