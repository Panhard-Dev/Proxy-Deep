import json
import io
import threading
import unittest
import urllib.request
from unittest.mock import patch

import contexto
import deepai_proxy_core as core


class ContextoTests(unittest.TestCase):
    def test_mede_componentes_sem_armazenar_conteudo(self):
        payload = {'model': 'glm-5.3-flash', 'tools': [{'type': 'function', 'name': 'ferramenta'}], 'messages': [
            {'role': 'system', 'content': 'instrucao-secreta'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'pergunta-privada'}, {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,segredo'}}]},
            {'role': 'tool', 'tool_call_id': '123', 'content': 'resultado-privado'}]}
        contexto.registrar(payload)
        result = contexto.ultimo()
        self.assertEqual(result['modelo'], 'glm-5.3-flash')
        self.assertEqual(result['imagens'], 1)
        self.assertTrue(all(result['partes'][x] > 0 for x in ('instrucoes','ferramentas','resultados','conversa')))
        self.assertEqual(result['estimativa'], sum(result['partes'].values()))
        serialized = json.dumps(result)
        for text in ('instrucao-secreta', 'pergunta-privada', 'resultado-privado', 'base64', 'segredo'):
            self.assertNotIn(text, serialized)

    def test_outputs_anthropic_e_historico_remoto(self):
        payload = {'model': 'test', 'conversation': 'id', 'system': 'instrução', 'messages': [
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'c', 'content': 'a'*100}]}]}
        result = contexto.analisar(payload)
        self.assertTrue(result['historico_remoto'])
        self.assertGreaterEqual(result['partes']['resultados'], 25)
        self.assertGreater(result['partes']['instrucoes'], 0)

    def test_responses_compactado_indica_parte_desconhecida(self):
        result = contexto.analisar({'model': 'test', 'input': [{'type': 'compaction', 'encrypted_content': 'secret'}, {'role': 'user', 'content': 'oi'}]})
        self.assertTrue(result['historico_remoto'])
        self.assertNotIn('secret', json.dumps(result))

    def test_gateway_publica_apenas_medidas_da_ultima_requisicao(self):
        class Resposta(io.BytesIO):
            status=200
            headers={'Content-Type':'application/json'}
        server=core.Server(('127.0.0.1',0),core.Gateway)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            body=json.dumps({'model':'test','messages':[{'role':'user','content':'conteudo-privado'}]}).encode()
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',data=body,headers={'Content-Type':'application/json'})
            with patch.object(core,'upstream_open',return_value=Resposta(b'{"choices":[]}')):
                with urllib.request.urlopen(req,timeout=5) as response: response.read()
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/internal/contexto',timeout=5) as response:
                data=json.load(response)
            self.assertEqual(data['contexto']['modelo'],'test')
            self.assertGreater(data['contexto']['partes']['conversa'],0)
            self.assertNotIn('conteudo-privado',json.dumps(data))
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__': unittest.main()
