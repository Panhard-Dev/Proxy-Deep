import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import contas_proxy
import protecao_proxy as protecao


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(protecao, 'ARQUIVO', Path(self.tmp.name) / 'protecao.sqlite3')
        self.path_patch.start()
        self.max_patch = patch.object(protecao, 'MAX_REQUISICOES', 3)
        self.max_patch.start()

    def tearDown(self):
        self.max_patch.stop(); self.path_patch.stop(); self.tmp.cleanup()
        contas_proxy._bloqueadas.clear(); contas_proxy._proxima = 0

    def test_rate_is_per_account_and_expires_after_five_minutes(self):
        with patch.object(protecao.time, 'time', return_value=1000):
            for _ in range(3): protecao.reservar('conta-a')
            with self.assertRaises(protecao.ContaLimitada) as caught: protecao.reservar('conta-a')
            self.assertEqual(caught.exception.segundos, 300)
            protecao.reservar('conta-b')
        with patch.object(protecao.time, 'time', return_value=1301):
            protecao.reservar('conta-a')
        with sqlite3.connect(str(protecao.ARQUIVO)) as db:
            keys = [row[0] for row in db.execute('SELECT conta FROM requisicoes')]
        self.assertNotIn('conta-a', keys)

    def test_concurrent_requests_do_not_exceed_limit(self):
        def reserve(_):
            try: protecao.reservar('conta-a'); return True
            except protecao.ContaLimitada: return False
        with patch.object(protecao.time, 'time', return_value=1000), ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(24)))
        self.assertEqual(sum(results), 3)

    def test_exactly_sixty_in_one_second_triggers_pause(self):
        with patch.object(protecao, 'MAX_REQUISICOES', 60), patch.object(protecao.time, 'time', return_value=1000):
            for _ in range(60): protecao.reservar('conta-a')
            with self.assertRaises(protecao.ContaLimitada) as caught: protecao.reservar('conta-a')
            self.assertEqual(caught.exception.segundos, 300)

    def test_limit_is_shared_across_processes(self):
        code = "import sys, pathlib, protecao_proxy as p; p.ARQUIVO=pathlib.Path(sys.argv[1]); p.MAX_REQUISICOES=3; p.time.time=lambda:1000\ntry: p.reservar('conta-a'); print('ok')\nexcept p.ContaLimitada: print('pausada')"
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(subprocess.run, [sys.executable, '-c', code, str(protecao.ARQUIVO)], capture_output=True, text=True, check=True) for _ in range(8)]
            outputs = [f.result().stdout.strip() for f in futures]
        self.assertEqual(outputs.count('ok'), 3)
        self.assertEqual(outputs.count('pausada'), 5)

    def test_no_daily_request_cap(self):
        clock = iter(1000 + i * 2 for i in range(500))
        with patch.object(protecao.time, 'time', side_effect=lambda: next(clock)):
            for _ in range(491): protecao.reservar('conta-a')

    def test_429_pauses_account_and_rotates(self):
        accounts = [{'deepai_key':'conta-a'}, {'deepai_key':'conta-b'}]
        selected = []
        def request(req, timeout):
            key=req.get_header('Api-key'); selected.append(key)
            if key=='conta-a':
                raise urllib.error.HTTPError(req.full_url,429,'limit',{'Retry-After':'10'},io.BytesIO(b'{}'))
            return io.BytesIO(b'ok')
        with patch.object(contas_proxy, 'carregar', return_value=accounts), patch.object(contas_proxy, 'identificador_maquina', return_value='a'*64), patch.object(contas_proxy._opener, 'open', side_effect=request):
            result=contas_proxy.abrir('https://example.invalid/models',None,{},'instance')
        self.assertEqual(result.read(),b'ok')
        self.assertEqual(selected,['conta-a','conta-b'])
        self.assertGreaterEqual(protecao.situacao('conta-a')['espera'],299)

    def test_local_rate_pause_rotates_to_next_account(self):
        accounts = [{'deepai_key':'conta-a'}, {'deepai_key':'conta-b'}]
        with patch.object(protecao.time, 'time', return_value=1000):
            for _ in range(3): protecao.reservar('conta-a')
            with patch.object(contas_proxy, 'carregar', return_value=accounts), patch.object(contas_proxy, 'identificador_maquina', return_value='a'*64), patch.object(contas_proxy._opener, 'open', return_value=io.BytesIO(b'ok')) as req:
                result=contas_proxy.abrir('https://example.invalid/models',None,{},'instance')
            self.assertEqual(result.read(),b'ok')
            self.assertEqual(req.call_args.args[0].get_header('Api-key'), 'conta-b')

    def test_longer_provider_retry_after_is_respected(self):
        with patch.object(protecao.time, 'time', return_value=1000):
            protecao.bloquear('conta-a', protecao.retry_after('600'))
            with self.assertRaises(protecao.ContaLimitada) as caught: protecao.reservar('conta-a')
        self.assertEqual(caught.exception.segundos, 600)

    def test_all_cooling_down_returns_429(self):
        accounts = [{'deepai_key':'conta-a'}, {'deepai_key':'conta-b'}]
        for c in accounts: protecao.bloquear(c['deepai_key'])
        with patch.object(contas_proxy, 'carregar', return_value=accounts), patch.object(contas_proxy._opener, 'open') as req:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                contas_proxy.abrir('https://example.invalid/models',None,{},'instance')
        self.assertEqual(caught.exception.code,429)
        self.assertGreaterEqual(int(caught.exception.headers['Retry-After']),299)
        req.assert_not_called()


if __name__ == '__main__': unittest.main()
