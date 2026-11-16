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

    def test_pedido_extenso_e_dividido_em_quatro_e_reunido(self):
        content = 'A'*30 + 'B'*30 + 'C'*30 + 'D'*30
        request = {'model': 'test', 'messages': [{'role': 'system', 'content': 'instruções'}, {'role': 'user', 'content': content}], 'stream': True}
        calls = []
        def abrir(path, body, affinity=None):
            p=json.loads(body);calls.append(p)
            if len(calls) <= 4:
                trecho = p['messages'][1]['content'].split('\n',1)[1]
                return resposta({'choices': [{'finish_reason':'stop', 'message':{'content':trecho[0]}}]})
            return resposta({'ok':True})
        with patch.object(compactacao, 'PEDIDO_GRANDE', 100):
            compactacao.abrir_com_compactacao(abrir, '/chat/completions', request).close()
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[-1]['messages'][0], request['messages'][0])
        self.assertTrue(calls[-1]['stream'])
        for i, letter in enumerate('ABCD', 1):
            self.assertIn(f'[Parte {i}]\n{letter}', calls[-1]['messages'][-1]['content'])

    def test_parte_acima_da_janela_e_dividida_novamente(self):
        calls=[]
        def abrir(path, body, affinity=None):
            p=json.loads(body); trecho=p['messages'][1]['content'].split('\n',1)[1]; calls.append(trecho)
            if len(trecho)>10: raise erro()
            return resposta({'choices':[{'finish_reason':'stop','message':{'content':trecho}}]})
        result=compactacao._mapear_partes(abrir,'test','A'*80,None,False)
        self.assertEqual(result.count('[Parte '),4)
        self.assertEqual(len(calls),12)

    def test_pedido_com_imagem_nao_e_substituido_por_resumo_textual(self):
        request={'model':'test','messages':[{'role':'user','content':[{'type':'text','text':'a'*100}, {'type':'image_url','image_url':{'url':'imagem'}}]}]}
        with patch.object(compactacao,'PEDIDO_GRANDE',50):
            self.assertIsNone(compactacao._dividir_pedido(None,'/chat/completions',request,None))

    def test_falha_local_de_limite_e_repetida_sem_reenviar_erros_do_provedor(self):
        body=b'{}';calls=[]
        def abrir(path,request,affinity=None):
            calls.append(request)
            if len(calls)==1:
                raise urllib.error.HTTPError('x',503,'',{'Retry-After':'1'},io.BytesIO(b'{"error":{"code":"rate_guard_unavailable","retryable":true}}'))
            return resposta({'ok':True})
        with patch.object(compactacao.time,'sleep') as sleep:
            with compactacao._abrir_com_retry_local(abrir,'/chat/completions',body,None) as response:
                self.assertTrue(json.load(response)['ok'])
        self.assertEqual(calls,[body,body]);sleep.assert_called_once_with(1)
        other=urllib.error.HTTPError('x',503,'',{},io.BytesIO(b'{"error":{"code":"service_unavailable"}}'))
        with patch.object(compactacao.time,'sleep') as sleep, self.assertRaises(urllib.error.HTTPError):
            compactacao._abrir_com_retry_local(lambda *a,**k: (_ for _ in ()).throw(other),'/chat/completions',body,None)
        sleep.assert_not_called()

    def test_slot_ocupado_espera_e_retorna_sem_repetir_outros_409(self):
        calls=[]
        def abrir(path,body,affinity=None):
            calls.append(body)
            if len(calls)==1:
                raise urllib.error.HTTPError('x',409,'',{'Retry-After':'1'},io.BytesIO(b'{"error":{"code":"session_busy"}}'))
            return resposta({'ok':True})
        with patch.object(compactacao.time,'sleep') as sleep:
            with compactacao._abrir_esperando_slot(abrir,'/chat/completions',b'{}',None) as response:
                self.assertTrue(json.load(response)['ok'])
        self.assertEqual(len(calls),2);sleep.assert_called_once_with(1)
        error=urllib.error.HTTPError('x',409,'',{},io.BytesIO(b'{"error":{"code":"other"}}'))
        with patch.object(compactacao.time,'sleep') as sleep, self.assertRaises(urllib.error.HTTPError):
            compactacao._abrir_esperando_slot(lambda *a,**k:(_ for _ in ()).throw(error),'/chat/completions',b'{}',None)
        sleep.assert_not_called()
        after=urllib.error.HTTPError('x',503,'',{},io.BytesIO(b'{"error":{"code":"rate_guard_unavailable"}}'))
        with patch.object(compactacao.time,'sleep') as sleep, self.assertRaises(urllib.error.HTTPError):
            compactacao._abrir_com_retry_local(lambda *a,**k: (_ for _ in ()).throw(after),'/chat/completions',b'{}',None)
        sleep.assert_not_called()

    def test_historico_grande_e_reduzido_antes_de_chamar_o_provedor(self):
        request={'model':'test','messages':[{'role':'user','content':'antigo'*50},
                 {'role':'assistant','content':'resultado'}, {'role':'user','content':'pedido atual'}]}
        calls=[]
        def abrir(path,body,affinity=None):
            payload=json.loads(body);calls.append(payload)
            return resposta({'ok':True})
        with patch.object(compactacao,'HISTORICO_GRANDE',280), patch.object(compactacao,'_resumir',return_value='resumo') as resumir:
            compactacao.abrir_com_compactacao(abrir,'/chat/completions',request).close()
        resumir.assert_called_once()
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0]['messages'][-1]['content'],'pedido atual')
        self.assertIn('resumo', calls[0]['messages'][0]['content'])

    def test_nao_envia_instrucoes_grandes_irredutiveis_para_truncamento_silencioso(self):
        request={'model':'test','tools':[{'description':'a'*300}], 'messages':[{'role':'user','content':'oi'}]}
        with patch.object(compactacao,'HISTORICO_GRANDE',200):
            with self.assertRaisesRegex(ValueError,'instruções ou ferramentas'):
                compactacao.abrir_com_compactacao(lambda *a,**k:self.fail('não deve enviar'),'/chat/completions',request)

    def test_segmentos_preservam_todo_texto_e_limite(self):
        texto = ('linha com dados preservados\n' * 80) + 'identificador-especial'
        trechos=[]
        def abrir(path,body,affinity=None):
            p=json.loads(body);trechos.append(p['messages'][1]['content'].split('\n',1)[1])
            return resposta({'choices':[{'finish_reason':'stop','message':{'content':'nota'}}]})
        # Limitar concorrência para verificar a ordem de envio dos trechos.
        from concurrent.futures import ThreadPoolExecutor
        with patch.object(compactacao,'TAMANHO_SEGMENTO',200), patch.object(compactacao,'ThreadPoolExecutor',side_effect=lambda max_workers: ThreadPoolExecutor(max_workers=1)):
            compactacao._mapear_partes(abrir,'test',texto,None,False)
        self.assertEqual(''.join(trechos),texto)
        self.assertTrue(all(len(t)<=200 for t in trechos))

    def test_segmentos_limitam_bytes_utf8_e_escapes_json(self):
        texto = ('ação 😀 "\\\n' * 150) + 'fim'
        trechos=[]
        def abrir(path,body,affinity=None):
            p=json.loads(body);trechos.append(p['messages'][1]['content'].split('\n',1)[1])
            return resposta({'choices':[{'finish_reason':'stop','message':{'content':'nota'}}]})
        from concurrent.futures import ThreadPoolExecutor
        with patch.object(compactacao,'TAMANHO_SEGMENTO',200), patch.object(compactacao,'MAX_PARTES',128), patch.object(compactacao,'ThreadPoolExecutor',side_effect=lambda max_workers: ThreadPoolExecutor(max_workers=1)):
            compactacao._mapear_partes(abrir,'test',texto,None,False)
        self.assertEqual(''.join(trechos),texto)
        self.assertTrue(all(len(json.dumps(t,ensure_ascii=False)[1:-1].encode())<=200 for t in trechos))

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
