import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

import compactacao
import deepai_proxy_core as core
import memoria


class MemoriaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'memoria.sqlite3'
        self.db = patch.object(memoria, 'ARQUIVO', self.path); self.db.start(); self.addCleanup(self.db.stop)
        self.config = patch.object(memoria, 'CONFIG', Path(self.temp.name)/'.memoria-projeto.json'); self.config.start(); self.addCleanup(self.config.stop)
        self.env = patch.dict(os.environ, {'DEEP_PROJECT_ID':''}); self.env.start(); self.addCleanup(self.env.stop)

    def scope(self, chat='chat-1', projeto='projeto-1'):
        return memoria.escopo({'Session-Id':chat, 'X-Project-Id':projeto}, {})

    def test_escopo_automatico_com_sessao(self):
        self.assertIsNone(memoria.escopo({}, {}))
        self.assertEqual(memoria.escopo({'Session-Id':'chat'}, {}), memoria.escopo({'Session-Id':'chat'}, {}))
        self.assertNotEqual(memoria.escopo({'Session-Id':'chat'}, {}), memoria.escopo({'Session-Id':'outro'}, {}))
        self.assertNotEqual(self.scope(), self.scope(chat='chat-2'))
        self.assertNotEqual(self.scope(), self.scope(projeto='projeto-2'))
        self.assertNotIn('chat-1', self.scope())

    def test_projeto_padrao_e_persistido_e_separa_escopos(self):
        memoria.configurar_projeto('projeto a')
        scope_a=memoria.escopo({'Session-Id':'chat'}, {})
        memoria.configurar_projeto('projeto b')
        scope_b=memoria.escopo({'Session-Id':'chat'}, {})
        self.assertNotEqual(scope_a,scope_b)
        self.assertEqual(memoria.estatisticas()['projeto'],'projeto b')
        memoria.configurar_projeto('')
        self.assertNotEqual(scope_b,memoria.escopo({'Session-Id':'chat'}, {}))

    def test_historico_automatico_e_ambiguidade(self):
        inicio={'messages':[{'role':'user','content':'Meu projeto se chama Nebulosa e usa FastAPI.'}]}
        scope=memoria.escopo({},inicio)
        memoria.aplicar(inicio,scope)
        historia={'messages':inicio['messages']+[{'role':'assistant','content':'ok'},{'role':'user','content':'continue'}]}
        self.assertEqual(memoria.escopo({},historia),scope)
        outro=memoria.escopo({},inicio)
        self.assertNotEqual(scope,outro)
        self.assertIsNone(memoria.escopo({},historia))
        memoria.limpar()
        novo=memoria.escopo({},historia)
        self.assertNotIn(novo,(scope,outro))

    def test_mensagens_isoladas_sem_identidade_nao_misturam_chats(self):
        a={'messages':[{'role':'user','content':'Meu projeto se chama Nebulosa e usa FastAPI.'}]}
        b={'messages':[{'role':'user','content':'Como configurar FastAPI no projeto Nebulosa?'}]}
        memoria.aplicar(a,memoria.escopo({},a))
        self.assertEqual(memoria.aplicar(b,memoria.escopo({},b)),b)

    def test_limpar_remove_memorias_de_todos_os_escopos(self):
        memoria.guardar_parte(self.scope(),'modelo','instrucao','conteudo','resumo')
        self.assertEqual(memoria.estatisticas()['notas'],1)
        memoria.limpar()
        self.assertEqual(memoria.estatisticas()['notas'],0)

    def test_preferencia_relevante_sobrevive_e_nao_vaza_entre_escopos(self):
        payload = {'messages':[{'role':'user','content':'Meu projeto se chama Nebulosa e usa FastAPI.'}]}
        self.assertEqual(memoria.aplicar(payload, self.scope()), payload)
        query = {'messages':[{'role':'user','content':'Como configurar FastAPI no projeto Nebulosa?'}]}
        recalled = memoria.aplicar(query, self.scope())
        self.assertIn('Nebulosa e usa FastAPI', recalled['messages'][-1]['content'])
        self.assertIn('Pedido atual:', recalled['messages'][-1]['content'])
        self.assertEqual(memoria.aplicar(query, self.scope(chat='chat-2')), query)
        self.assertEqual(memoria.aplicar(query, self.scope(projeto='projeto-2')), query)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM notas WHERE escopo=?', (self.scope(),)).fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM notas WHERE escopo=?', (self.scope(chat='chat-2'),)).fetchone()[0],1)
        if os.name != 'nt': self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_nota_persiste_apos_novo_processo(self):
        scope=self.scope()
        memoria.guardar_parte(scope,'modelo','instrucao','trecho persistente','resumo persistente')
        code=('import memoria,sys; from pathlib import Path; memoria.ARQUIVO=Path(sys.argv[1]); '
              'print(memoria.buscar_parte(sys.argv[2],"modelo","instrucao","trecho persistente") == "resumo persistente")')
        result=subprocess.run([sys.executable,'-c',code,str(self.path),scope],capture_output=True,text=True,timeout=10,check=True)
        self.assertEqual(result.stdout.strip(),'True')

    def test_retomada_curta_recupera_notas_recentes_no_mesmo_escopo(self):
        memoria.aplicar({'messages':[{'role':'user','content':'Meu projeto se chama Nebulosa e usa FastAPI.'}]},self.scope())
        pedido={'messages':[{'role':'user','content':'continua isso'}]}
        self.assertIn('Nebulosa',memoria.aplicar(pedido,self.scope())['messages'][-1]['content'])
        self.assertEqual(memoria.aplicar(pedido,self.scope(chat='outro')),pedido)

    def test_busca_encontra_termo_unico_sem_acento_e_mantem_isolamento(self):
        original={'messages':[{'role':'user','content':'Meu projeto se chama Nebulosa e usa autenticação por certificados.'}]}
        memoria.aplicar(original,self.scope())
        pedido={'messages':[{'role':'user','content':'Como melhorar autenticacao?'}]}
        self.assertIn('Nebulosa', memoria.aplicar(pedido,self.scope())['messages'][-1]['content'])
        self.assertEqual(memoria.aplicar(pedido,self.scope(projeto='outro')),pedido)

    def test_busca_valoriza_termo_raro_e_nao_recupera_termo_comum_isolado(self):
        rows=[('mensagem','configuracao comum '+str(i)) for i in range(5)]
        rows.append(('mensagem','configuracao especial Nebulosa'))
        self.assertEqual(memoria._relevantes({'configuracao'}, rows), [])
        self.assertEqual(memoria._relevantes({'configuracao','nebulosa'}, rows)[0][1], 'mensagem')
        self.assertIn('Nebulosa',memoria._relevantes({'configuracao','nebulosa'}, rows)[0][2])

    def test_listagem_paginada_mostra_texto_e_grupos_sem_identificadores(self):
        memoria.guardar_parte(self.scope(),'m','i','a','primeiro')
        memoria.guardar_parte(self.scope(chat='outro'),'m','i','b','segundo')
        primeira=memoria.listar(0,1); segunda=memoria.listar(1,1)
        self.assertTrue(primeira['mais']); self.assertFalse(segunda['mais'])
        self.assertEqual({primeira['notas'][0]['texto'],segunda['notas'][0]['texto']},{'primeiro','segundo'})
        self.assertNotEqual(primeira['notas'][0]['escopo'],segunda['notas'][0]['escopo'])
        self.assertNotIn('chat-1', str(primeira))

    def test_sem_identidade_ou_com_segredo_nao_salva(self):
        p={'messages':[{'role':'user','content':'Lembre que api_key: sk-12345678901234567890'}]}
        memoria.aplicar(p, self.scope())
        memoria.aplicar({'messages':[{'role':'user','content':'Prefiro usar Python no projeto'}]}, None)
        self.assertFalse(self.path.exists())

    def test_cache_exato_reusa_partes_apenas_no_mesmo_escopo_e_modelo(self):
        texto='A'*30+'B'*30+'C'*30+'D'*30
        calls=[]
        def abrir(path,body,affinity=None):
            data=json.loads(body);part=data['messages'][1]['content'].split('\n',1)[1];calls.append(part)
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'nota-'+part[0]}}]}).encode())
        first=compactacao._mapear_partes(abrir,'modelo',texto,None,False,self.scope())
        self.assertEqual(len(calls),4)
        calls.clear()
        second=compactacao._mapear_partes(abrir,'modelo',texto,None,False,self.scope())
        self.assertEqual(first,second);self.assertEqual(calls,[])
        compactacao._mapear_partes(abrir,'modelo',texto,None,False,self.scope(projeto='outro'))
        self.assertEqual(len(calls),4)
        calls.clear()
        compactacao._mapear_partes(abrir,'outro-modelo',texto,None,False,self.scope())
        self.assertEqual(len(calls),4)

    def test_cache_rejeita_segredos_e_resumos_incompletos(self):
        scope=self.scope()
        memoria.guardar_parte(scope,'modelo','instrucao','api_key: sk-abcdefghijklmnopqrst','nota')
        memoria.guardar_parte(scope,'modelo','instrucao','texto','password=12345')
        self.assertFalse(self.path.exists())

    def test_cache_reutiliza_prefixo_quando_entrada_cresce(self):
        texto=''.join(chr(ord('A')+i)*200 for i in range(8))
        calls=[]
        def abrir(path,body,affinity=None):
            data=json.loads(body);part=data['messages'][1]['content'].split('\n',1)[1];calls.append(part)
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'nota-'+part[0]}}]}).encode())
        with patch.object(compactacao,'TAMANHO_SEGMENTO',200):
            compactacao._mapear_partes(abrir,'modelo',texto,None,False,self.scope())
            first=len(calls);calls.clear()
            compactacao._mapear_partes(abrir,'modelo',texto+'Z'*100,None,False,self.scope())
        self.assertEqual(first,8)
        self.assertEqual(calls,['Z'*100])

    def test_historico_completo_nao_recebe_memoria_duplicada(self):
        memoria.aplicar({'messages':[{'role':'user','content':'Meu projeto se chama Nebulosa e usa FastAPI.'}]},self.scope())
        payload={'messages':[{'role':'user','content':'Meu projeto se chama Nebulosa e usa FastAPI.'},{'role':'assistant','content':'ok'},
                             {'role':'user','content':'Como configurar FastAPI no projeto Nebulosa?'}]}
        self.assertEqual(memoria.aplicar(payload,self.scope()),payload)

    def test_gateway_recupera_memoria_somente_na_sessao_e_projeto_corretos(self):
        class Reply(io.BytesIO):
            status=200
            headers={'Content-Type':'application/json'}
        enviados=[]
        def abrir(path,body,timeout=930,affinity=None):
            enviados.append(json.loads(body))
            return Reply(b'{"choices":[{"message":{"content":"ok"}}]}')
        server=core.Server(('127.0.0.1',0),core.Gateway)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def requisitar(project,text):
            body=json.dumps({'model':'test','messages':[{'role':'user','content':text}]}).encode()
            headers={'Content-Type':'application/json','Session-Id':'chat-1','X-Project-Id':project}
            req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',data=body,headers=headers)
            with urllib.request.urlopen(req,timeout=5) as response: response.read()
        try:
            with patch.object(core,'upstream_open',side_effect=abrir):
                requisitar('projeto-1','Meu projeto se chama Nebulosa e usa FastAPI.')
                requisitar('projeto-1','Como configurar FastAPI no projeto Nebulosa?')
                requisitar('projeto-2','Como configurar FastAPI no projeto Nebulosa?')
            self.assertIn('Notas locais desta sessão',enviados[1]['messages'][-1]['content'])
            self.assertNotIn('Notas locais desta sessão',enviados[2]['messages'][-1]['content'])
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_gateway_salva_memoria_sem_configurar_cabecalhos(self):
        class Reply(io.BytesIO):
            status=200
            headers={'Content-Type':'application/json'}
        def abrir(path,body,timeout=930,affinity=None):
            return Reply(b'{"choices":[{"message":{"content":"ok"}}]}')
        server=core.Server(('127.0.0.1',0),core.Gateway)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            primeira={'role':'user','content':'Meu projeto se chama Nebulosa e usa FastAPI.'}
            conversas=[[primeira],[primeira,{'role':'assistant','content':'ok'},
                         {'role':'user','content':'Prefiro Python para fazer testes automaticos.'}]]
            with patch.object(core,'upstream_open',side_effect=abrir):
                for messages in conversas:
                    body=json.dumps({'model':'test','messages':messages}).encode()
                    req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions',data=body,headers={'Content-Type':'application/json'})
                    with urllib.request.urlopen(req,timeout=5) as response: response.read()
            with sqlite3.connect(self.path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM notas').fetchone()[0],2)
                self.assertEqual(db.execute('SELECT COUNT(DISTINCT escopo) FROM notas').fetchone()[0],1)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__ == '__main__': unittest.main()
