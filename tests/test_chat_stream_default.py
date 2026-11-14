import io
import json
import threading
import unittest
import urllib.request
from unittest.mock import patch

import deepai_proxy_core as core


class Upstream(io.BytesIO):
    status = 200
    headers = {'Content-Type': 'application/json'}


class ChatTests(unittest.TestCase):
    def test_missing_stream_requests_json(self):
        seen = []
        def open_upstream(path, body, timeout=930, affinity=None):
            seen.append(json.loads(body))
            return Upstream(b'{"choices":[{"message":{"content":"OK"}}]}')
        server = core.Server(('127.0.0.1', 0), core.Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(core, 'upstream_open', side_effect=open_upstream):
                body = json.dumps({'model': 'test', 'messages': [{'role': 'developer', 'content': 'instruções'}, {'role': 'user', 'content': 'oi'}]}).encode()
                req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions', data=body, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    self.assertEqual(json.load(response)['choices'][0]['message']['content'], 'OK')
            self.assertIs(seen[0]['stream'], False)
            self.assertEqual(seen[0]['messages'][0]['role'], 'system')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_responses_model_route_uses_catalog(self):
        seen = []
        def open_upstream(path, body, timeout=930, affinity=None):
            seen.append(path)
            return Upstream(b'{"object":"response","output":[]}')
        server = core.Server(('127.0.0.1', 0), core.Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.object(core, 'upstream_open', side_effect=open_upstream), patch.object(core.Gateway, 'model_by_id', return_value={'api':'openai-responses'}):
                body = json.dumps({'model':'new-model','input':'oi'}).encode()
                req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/responses', data=body, headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req, timeout=5) as response: self.assertEqual(response.status, 200)
            self.assertEqual(seen, ['/responses'])
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
