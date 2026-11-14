import io
import json
import urllib.error
import unittest
from unittest.mock import patch

import deepai_proxy_core as core
import gerenciar_contas as painel


class Reply(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class ClientProtocolTests(unittest.TestCase):
    def test_checkin_nonce_is_returned_on_next_balance_request(self):
        nonce = 'a' * 32
        body = json.dumps({'balance': '3', 'check_in': {'nonce': nonce, 'seconds': 600}}).encode()
        requests = []
        def open_request(req, timeout):
            requests.append(req)
            return Reply(body)
        painel.saldo_cache.clear(); painel.checkins.clear()
        with patch('urllib.request.urlopen', side_effect=open_request), patch.object(painel.contas_proxy, 'identificador_maquina', return_value='a'*64), patch.object(painel.protecao_proxy, 'reservar'):
            painel.consultar_saldo(0, {'deepai_key': 'test-key'}, True)
            painel.consultar_saldo(0, {'deepai_key': 'test-key'}, True)
        self.assertEqual(requests[0].get_header('X-deepai-code-theos'), '6')
        self.assertEqual(requests[1].get_header('X-deepai-code-check-in'), nonce)
        self.assertTrue(requests[1].get_header('X-deepai-code-beacon').startswith('v=0.3.37;'))

    def test_stream_reasoning_metadata_is_preserved(self):
        chunk = {'choices': [{'delta': {'reasoning': 'a', 'reasoning_details': [{'type': 'reasoning.text'}]}}]}
        out = core.normalize_stream_event(chunk)
        self.assertEqual(out['choices'][0]['delta']['reasoning_content'], 'a')
        self.assertEqual(out['choices'][0]['delta']['reasoning_details'][0]['type'], 'reasoning.text')

    def test_refusal_preserves_last_balance_as_stale(self):
        body = json.dumps({'balance': '-3.14', 'granted': '2', 'refresh_at': '2026-11-15T00:00:00Z'}).encode()
        error = urllib.error.HTTPError('https://example.invalid', 403, 'recusado', {}, io.BytesIO(b'{"error":{"code":"check_in_refused"}}'))
        painel.saldo_cache.clear(); painel.checkins.clear()
        with patch('urllib.request.urlopen', side_effect=[Reply(body), error]), patch.object(painel.contas_proxy, 'identificador_maquina', return_value='a'*64), patch.object(painel.protecao_proxy, 'reservar'):
            first = painel.consultar_saldo(0, {'deepai_key':'test-key'}, True)
            second = painel.consultar_saldo(0, {'deepai_key':'test-key'}, True)
        self.assertEqual(first['saldo'], '-3.14')
        self.assertEqual(second['saldo'], '-3.14')
        self.assertTrue(second['desatualizado'])
        self.assertEqual(second['status'], 'check-in temporariamente bloqueado')

    def test_affinity_only_for_valid_values(self):
        with patch.object(core.contas_proxy, 'abrir', return_value=None) as abrir:
            core.upstream_open('/models', affinity='session-123')
            self.assertEqual(abrir.call_args.args[2]['X-Session-Affinity'], 'session-123')
            core.upstream_open('/models', affinity='invalid value')
            self.assertNotIn('X-Session-Affinity', abrir.call_args.args[2])


if __name__ == '__main__': unittest.main()
