import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import compactacao
import deepai_proxy_core as core


def erro(code=400, error_code='context_length_exceeded'):
    body = json.dumps({'error': {'code': error_code, 'message': 'erro original'}}).encode()
    return urllib.error.HTTPError('http://example.invalid', code, 'error', {}, io.BytesIO(body))


def resposta(obj):
    return io.BytesIO(json.dumps(obj).encode())


class CompactacaoTests(unittest.TestCase):
    def test_retry_preserva_ferramentas_instrucoes_e_turno(self):
        request = {'model': 'test', 'stream': True, 'tools': [{'name': 'x'}], 'messages': [
            {'role': 'system', 'content': 'instruções'},
            {'role': 'user', 'content': 'objetivo antigo'},
            {'role': 'assistant', 'content': 'andamento'},
            {'role': 'user', 'content': 'pedido atual'},
            {'role': 'assistant', 'tool_calls': [{'id': 'a'}, {'id': 'b'}]},
            {'role': 'tool', 'tool_call_id': 'a', 'content': '1'},
            {'role': 'tool', 'tool_call_id': 'b', 'content': '2'}]}
        calls = []
        def abrir(path, body, affinity=None):
            calls.append((path, json.loads(body), affinity))
            if len(calls) == 1: raise erro()
            if len(calls) == 2:
                return resposta({'choices': [{'finish_reason': 'stop', 'message': {'content': 'resumo'}}]})
            return resposta({'ok': True})
        with compactacao.abrir_com_compactacao(abrir, '/chat/completions', request, 'sessao') as result:
            self.assertTrue(json.load(result)['ok'])
        final = calls[2][1]
        self.assertEqual(final['messages'][0], request['messages'][0])
        self.assertEqual(final['messages'][-4:], request['messages'][-4:])
        self.assertEqual(final['tools'], request['tools'])
        self.assertTrue(final['stream'])
        self.assertTrue(all(call[2] == 'sessao' for call in calls))
        self.assertIn('objetivo antigo', calls[1][1]['messages'][1]['content'])

    def test_nativo_responses(self):
        request = {'model': 'test', 'input': [{'role': 'user', 'content': 'antigo'}, {'role': 'assistant', 'content': 'feito'}, {'role': 'user', 'content': 'atual'}]}
        calls = []
        def abrir(path, body, affinity=None):
            calls.append((path, json.loads(body)))
            if len(calls) == 1: raise erro()
            if len(calls) == 2: return resposta({'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'resumo'}]}]})
            return resposta({'ok': True})
        compactacao.abrir_com_compactacao(abrir, '/responses', request).close()
        self.assertEqual([c[0] for c in calls], ['/responses'] * 3)
        self.assertEqual(calls[2][1]['input'][-1], request['input'][-1])

    def test_erros_outros_e_falha_do_resumo_preservam_erro(self):
        request = {'model': 'test', 'messages': [{'role': 'user', 'content': 'velho'}, {'role': 'user', 'content': 'novo'}]}
        for original in (erro(429), erro(400, 'other_error')):
            with self.subTest(code=original.code), self.assertRaises(urllib.error.HTTPError) as caught:
                compactacao.abrir_com_compactacao(lambda *a, **k: (_ for _ in ()).throw(original), '/chat/completions', request)
            self.assertEqual(json.load(caught.exception)['error']['message'], 'erro original')
        calls = iter([erro(), resposta({'choices': [{'finish_reason': 'length', 'message': {'content': 'incompleto'}}]})])
        def abrir(*args, **kwargs):
            result = next(calls)
            if isinstance(result, Exception): raise result
            return result
        with self.assertRaises(urllib.error.HTTPError) as caught:
            compactacao.abrir_com_compactacao(abrir, '/chat/completions', request)
        self.assertEqual(json.load(caught.exception)['error']['code'], 'context_length_exceeded')

    def test_imagem_antiga_nao_e_resumida_como_texto(self):
        request = {'model': 'test', 'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,abc'}}]}, {'role': 'user', 'content': 'novo'}]}
        with patch('compactacao._resumir', wraps=compactacao._resumir) as resumo:
            with self.assertRaises(urllib.error.HTTPError):
                compactacao.abrir_com_compactacao(lambda *a, **k: (_ for _ in ()).throw(erro()), '/chat/completions', request)
        resumo.assert_called_once()

    def test_preserva_varios_turnos_recentes_e_reconhece_mensagem_do_provedor(self):
        request = {'model': 'test', 'messages': [
            {'role': 'user', 'content': 'antigo'}, {'role': 'assistant', 'content': 'primeira resposta'},
            {'role': 'user', 'content': 'recente'}, {'role': 'assistant', 'content': 'segunda resposta'},
            {'role': 'user', 'content': 'atual'}]}
        calls = []
        def abrir(path, body, affinity=None):
            calls.append(json.loads(body))
            if len(calls) == 1:
                raise urllib.error.HTTPError('x', 400, '', {}, io.BytesIO(b'{"error":{"message":"This conversation exceeds the context window"}}'))
            if len(calls) == 2:
                return resposta({'choices': [{'finish_reason': 'stop', 'message': {'content': 'resumo'}}]})
            return resposta({'ok': True})
        compactacao.abrir_com_compactacao(abrir, '/chat/completions', request).close()
        self.assertEqual(calls[-1]['messages'][-3:], request['messages'][-3:])

    def test_tres_protocolos_usam_mesmo_recuperador(self):
        handler = object.__new__(core.Gateway)
        handler.headers = {}
        request = {'model': 'test', 'messages': [{'role': 'user', 'content': 'oi'}]}
        with patch.object(core.compactacao, 'abrir_com_compactacao', side_effect=urllib.error.HTTPError('x', 400, '', {}, io.BytesIO(b'{}'))) as abrir, patch.object(handler, 'upstream_error'):
            handler.proxy_chat(request.copy())
            with patch.object(handler, 'model_by_id', return_value=None):
                handler.proxy_responses({'model': 'test', 'input': 'oi'})
            with self.assertRaises(urllib.error.HTTPError):
                handler.proxy_anthropic(request.copy())
        self.assertEqual(abrir.call_count, 3)

    def test_gateway_recupera_erro_em_cada_protocolo(self):
        class Upstream(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'text/event-stream'}
        requests = [
            ('/v1/chat/completions', {'model': 'test', 'messages': [{'role': 'user', 'content': 'antigo'}, {'role': 'assistant', 'content': 'ok'}, {'role': 'user', 'content': 'novo'}]}),
            ('/v1/responses', {'model': 'test', 'input': [{'role': 'user', 'content': 'antigo'}, {'role': 'assistant', 'content': 'ok'}, {'role': 'user', 'content': 'novo'}]}),
            ('/v1/messages', {'model': 'test', 'max_tokens': 40, 'messages': [{'role': 'user', 'content': 'antigo'}, {'role': 'assistant', 'content': 'ok'}, {'role': 'user', 'content': 'novo'}]}),
        ]
        server = core.Server(('127.0.0.1', 0), core.Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            for route, payload in requests:
                calls = []
                def abrir(path, body, affinity=None):
                    calls.append(json.loads(body))
                    if len(calls) == 1: raise erro()
                    if len(calls) == 2: return resposta({'choices': [{'finish_reason': 'stop', 'message': {'content': 'resumo'}}]})
                    stream = b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                    return Upstream(stream)
                with self.subTest(route=route), patch.object(core, 'upstream_open', side_effect=abrir), patch.object(core.Gateway, 'model_by_id', return_value=None):
                    req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{route}', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req, timeout=5) as result:
                        self.assertEqual(result.status, 200)
                        self.assertIn(b'OK', result.read())
                    self.assertEqual(len(calls), 3)
                    self.assertEqual(calls[-1]['messages'][-1]['content'], 'novo')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
