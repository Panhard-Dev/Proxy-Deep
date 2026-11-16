import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import atualizador
import atualizador_worker


def blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


class AtualizadorTests(unittest.TestCase):
    def test_pacote_e_verificado_incluindo_linhas_windows(self):
        contents = {'proxy.py': b'proxy\n', 'gerenciar_contas.py': b'painel\n', 'abrir.bat': b'linha\n'}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as z:
            for name, data in contents.items():
                z.writestr('Proxy-Deep-commit/' + name, data.replace(b'\n', b'\r\n') if name.endswith('.bat') else data)
        sha, tree_sha = 'a'*40, 'b'*40
        commit = {'sha': sha, 'commit': {'tree': {'sha': tree_sha}}}
        tree = {'sha': tree_sha, 'truncated': False, 'tree': [
            {'type': 'blob', 'mode': '100644', 'path': name, 'size': len(data), 'sha': blob(data)}
            for name, data in contents.items()]}
        replies = iter([json.dumps(commit).encode(), json.dumps(tree).encode(), archive.getvalue()])
        with patch('urllib.request.urlopen', side_effect=lambda *a, **k: io.BytesIO(next(replies))):
            path = atualizador.preparar()
        self.addCleanup(__import__('shutil').rmtree, path, True)
        self.assertEqual((path/'arquivos'/'abrir.bat').read_bytes(), b'linha\r\n')
        self.assertEqual(json.loads((path/'manifest.json').read_text())['sha'], sha)

    def test_recusa_caminhos_e_dados_locais(self):
        for name in ('../fora', '/absoluto', 'a/../../fora', 'C:/fora', '.git/config', 'contas.json', 'uso.sqlite3', '.memoria-projeto.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                atualizador.caminho_seguro(name)

    def test_rollback_de_arquivos_e_preservacao_de_dados(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'app'; root.mkdir()
            stage = Path(directory)/'stage'; (stage/'arquivos').mkdir(parents=True)
            (root/'codigo.py').write_text('antigo')
            (root/'contas.json').write_text('dados privados')
            (stage/'arquivos'/'codigo.py').write_text('novo')
            with patch.object(atualizador_worker, 'ROOT', root):
                changes = atualizador_worker.instalar_zip(stage, ['codigo.py'])
                self.assertEqual((root/'codigo.py').read_text(), 'novo')
                atualizador_worker.restaurar(changes)
            self.assertEqual((root/'codigo.py').read_text(), 'antigo')
            self.assertEqual((root/'contas.json').read_text(), 'dados privados')

    def test_recusa_symlink_de_destino(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'app'; root.mkdir()
            outside = Path(directory)/'outside'; outside.write_text('original')
            try: (root/'codigo.py').symlink_to(outside)
            except OSError: self.skipTest('symlink indisponível')
            stage = Path(directory)/'stage'; (stage/'arquivos').mkdir(parents=True)
            (stage/'arquivos'/'codigo.py').write_text('novo')
            with patch.object(atualizador_worker, 'ROOT', root), self.assertRaises(ValueError):
                atualizador_worker.instalar_zip(stage, ['codigo.py'])
            self.assertEqual(outside.read_text(), 'original')


if __name__ == '__main__': unittest.main()
