import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request


SOURCE = Path(__file__).resolve().parent.parent


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def request(url, body=None):
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=2) as response:
        return json.load(response)


def wait(fn):
    for _ in range(100):
        try:
            return fn()
        except (OSError, ValueError):
            time.sleep(.1)
    raise RuntimeError('Serviço de teste não iniciou.')


class AtualizadorIntegracao(unittest.TestCase):
    def test_atualiza_reinicia_e_preserva_dados(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'app'
            shutil.copytree(SOURCE, root, ignore=shutil.ignore_patterns('.git', '.deps', 'worker-app', '__pycache__', '*.sqlite3*',
                '*.log', 'contas.json', '.machine-id', '.memoria-projeto.json', 'runtime_proxy.json', '.atualizacao-status.json', '.atualizacao-backup'))
            (root/'contas.json').write_text('[]')
            (root/'.machine-id').write_text('teste-preservado')
            stage = Path(directory)/'stage'; (stage/'arquivos').mkdir(parents=True)
            source = (root/'atualizacao.py').read_text(encoding='utf-8')
            (stage/'arquivos'/'atualizacao.py').write_text(re.sub(r"VERSAO = '[^']+'", "VERSAO = '9.9.9-test'", source, count=1), encoding='utf-8')
            (stage/'manifest.json').write_text(json.dumps({'sha': 'a'*40, 'files': ['atualizacao.py']}))
            panel_port, proxy_port, token = port(), port(), 'teste-atualizacao'
            while panel_port == proxy_port: proxy_port = port()
            env = dict(os.environ, DEEPAI_PAINEL_TOKEN=token, DEEPAI_PAINEL_PORTA=str(panel_port),
                       DEEP_PORT=str(proxy_port), DEEPAI_PROXY_INTERNAL='1')
            panel = proxy = None
            try:
                proxy = subprocess.Popen([sys.executable, 'proxy.py', 'serve', '--porta', str(proxy_port)], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                panel = subprocess.Popen([sys.executable, 'gerenciar_contas.py', '--sem-navegador'], cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                health = wait(lambda: request(f'http://127.0.0.1:{proxy_port}/health'))
                wait(lambda: request(f'http://127.0.0.1:{panel_port}/api/painel?chave={token}'))
                env.update(DEEP_UPDATE_PANEL_PORT=str(panel_port), DEEP_UPDATE_PROXY_PORT=str(proxy_port), DEEP_UPDATE_PROXY_INSTANCE=health['instance'])
                completed = subprocess.run([sys.executable, 'atualizador_worker.py', str(stage)], cwd=root, env=env,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
                self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors='replace'))
                status = json.loads((root/'.atualizacao-status.json').read_text())
                self.assertEqual(status['status'], 'concluida', status)
                self.assertEqual(request(f'http://127.0.0.1:{proxy_port}/health')['version'], '9.9.9-test')
                self.assertIn('accounts', request(f'http://127.0.0.1:{panel_port}/api/painel?chave={token}'))
                self.assertEqual((root/'contas.json').read_text(), '[]')
                self.assertEqual((root/'.machine-id').read_text(), 'teste-preservado')
                self.assertTrue(any((root/'.atualizacao-backup').rglob('atualizacao.py')))
            finally:
                try:
                    request(f'http://127.0.0.1:{panel_port}/api/atualizacao/desligar', urllib.parse.urlencode({'chave':token}).encode())
                except OSError:
                    if panel and panel.poll() is None: panel.terminate()
                try:
                    health = request(f'http://127.0.0.1:{proxy_port}/health')
                    request(f'http://127.0.0.1:{proxy_port}/internal/shutdown', json.dumps({'instance':health['instance']}).encode())
                except OSError:
                    if proxy and proxy.poll() is None: proxy.terminate()
                if proxy: proxy.wait(timeout=5)
                if panel: panel.wait(timeout=5)
                time.sleep(.5)


if __name__ == '__main__': unittest.main()
