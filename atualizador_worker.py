"""Processo separado para atualizar os arquivos enquanto o painel está parado."""
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import atualizador

ROOT = Path(__file__).resolve().parent


def executar(*args):
    return subprocess.run(args, cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=90)


def post(url, data):
    request = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status == 200


def aguardar_porta(porta, aberta):
    for _ in range(120):
        try:
            with socket.create_connection(('127.0.0.1', porta), timeout=.2): ativo = True
        except OSError:
            ativo = False
        if ativo == aberta: return
        time.sleep(.1)
    raise RuntimeError('O serviço local não respondeu como esperado.')


def iniciar_servicos(porta_painel, porta_proxy, token):
    import iniciar
    env = {'DEEP_PORT': str(porta_proxy), 'DEEPAI_PROXY_INTERNAL': '1'}
    iniciar.start(['proxy.py', 'serve', '--porta', str(porta_proxy)], '.proxy.log', env)
    aguardar_porta(porta_proxy, True)
    env = {'DEEPAI_PAINEL_PORTA': str(porta_painel), 'DEEPAI_PAINEL_TOKEN': token, 'DEEP_PORT': str(porta_proxy)}
    iniciar.start(['gerenciar_contas.py', '--sem-navegador'], '.painel.log', env)
    aguardar_porta(porta_painel, True)
    with urllib.request.urlopen(f'http://127.0.0.1:{porta_proxy}/health', timeout=5) as response:
        if json.load(response).get('provider') != 'DeepAI': raise RuntimeError('O proxy não iniciou corretamente.')
    url = f'http://127.0.0.1:{porta_painel}/api/painel?' + urllib.parse.urlencode({'chave': token})
    with urllib.request.urlopen(url, timeout=5) as response:
        if response.status != 200: raise RuntimeError('O painel não iniciou corretamente.')


def instalar_zip(pasta, files):
    backup = pasta / 'backup'; backup.mkdir()
    alterados = []
    try:
        for name in files:
            relative = atualizador.caminho_seguro(name)
            dest = ROOT.joinpath(*relative.parts)
            if ROOT.resolve() not in dest.resolve().parents:
                raise ValueError('Destino inválido.')
            if dest.is_symlink() or (dest.exists() and not dest.is_file()):
                raise ValueError('Arquivo local não pode ser substituído: ' + name)
            saved = backup.joinpath(*relative.parts)
            exists = dest.exists()
            if exists:
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, saved)
            alterados.append((dest, saved, exists))
            dest.parent.mkdir(parents=True, exist_ok=True)
            temp = dest.with_name(dest.name + '.atualizando')
            shutil.copy2((pasta / 'arquivos').joinpath(*relative.parts), temp)
            os.replace(temp, dest)
        return alterados
    except Exception:
        restaurar(alterados)
        raise


def restaurar(alterados):
    for dest, saved, exists in reversed(alterados):
        if exists: shutil.copy2(saved, dest)
        elif dest.exists(): dest.unlink()


def parar_novos(porta_painel, porta_proxy, token):
    try:
        data = urllib.parse.urlencode({'chave': token}).encode()
        post(f'http://127.0.0.1:{porta_painel}/api/atualizacao/desligar', data)
        aguardar_porta(porta_painel, False)
    except (OSError, RuntimeError):
        pass
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{porta_proxy}/health', timeout=3) as response:
            health = json.load(response)
        if health.get('provider') == 'DeepAI' and health.get('instance'):
            req = urllib.request.Request(f'http://127.0.0.1:{porta_proxy}/internal/shutdown',
                                         data=json.dumps({'instance': health['instance']}).encode(),
                                         headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=3): pass
            aguardar_porta(porta_proxy, False)
    except (OSError, ValueError, RuntimeError):
        pass


def main(pasta):
    porta_painel = int(os.environ['DEEP_UPDATE_PANEL_PORT'])
    porta_proxy = int(os.environ['DEEP_UPDATE_PROXY_PORT'])
    token = os.environ['DEEPAI_PAINEL_TOKEN']
    instance = os.environ.get('DEEP_UPDATE_PROXY_INSTANCE', '')
    manifest = json.loads((pasta / 'manifest.json').read_text(encoding='utf-8'))
    sha = manifest['sha']
    git = (ROOT / '.git').exists()
    old_head = None
    panel_stopped = False
    proxy_stopped = False
    alterados = []
    try:
        if git:
            if executar('git', 'status', '--porcelain', '--untracked-files=no').stdout.strip():
                raise RuntimeError('Há alterações locais no projeto. Salve-as antes de atualizar.')
            old_head = executar('git', 'rev-parse', 'HEAD').stdout.strip()
            executar('git', 'fetch', '--no-tags', 'https://github.com/Panhard-Dev/Proxy-Deep.git', 'main')
            if executar('git', 'rev-parse', 'FETCH_HEAD').stdout.strip() != sha:
                raise RuntimeError('A versão mudou durante o download. Tente novamente.')
            executar('git', 'merge-base', '--is-ancestor', old_head, sha)
        atualizador.estado('reiniciando', 'Instalando e reiniciando o proxy...')
        if instance:
            body = json.dumps({'instance': instance}).encode()
            req = urllib.request.Request(f'http://127.0.0.1:{porta_proxy}/internal/shutdown', data=body, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status != 200: raise RuntimeError('Não foi possível parar o proxy.')
            aguardar_porta(porta_proxy, False)
            proxy_stopped = True
        data = urllib.parse.urlencode({'chave': token}).encode()
        if not post(f'http://127.0.0.1:{porta_painel}/api/atualizacao/desligar', data):
            raise RuntimeError('Não foi possível parar o painel.')
        aguardar_porta(porta_painel, False)
        panel_stopped = True
        if git:
            executar('git', 'merge', '--ff-only', sha)
        else:
            alterados = instalar_zip(pasta, manifest['files'])
        iniciar_servicos(porta_painel, porta_proxy, token)
        if alterados:
            backup_destino = ROOT / '.atualizacao-backup' / str(time.time_ns())
            backup_destino.parent.mkdir(exist_ok=True)
            shutil.copytree(pasta / 'backup', backup_destino)
        atualizador.estado('concluida', 'Atualização concluída. Proxy reiniciado.')
    except Exception as exc:
        if panel_stopped:
            try:
                parar_novos(porta_painel, porta_proxy, token)
                if git and old_head:
                    executar('git', 'reset', '--keep', old_head)
                elif alterados:
                    restaurar(alterados)
                iniciar_servicos(porta_painel, porta_proxy, token)
            except Exception:
                pass
        elif proxy_stopped:
            try:
                import iniciar
                iniciar.start(['proxy.py', 'serve', '--porta', str(porta_proxy)], '.proxy.log', {'DEEPAI_PROXY_INTERNAL': '1'})
            except Exception:
                pass
        atualizador.estado('erro', 'Não foi possível atualizar: ' + str(exc))
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


if __name__ == '__main__':
    main(Path(sys.argv[1]))
