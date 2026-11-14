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
        def open_upstream(path, body, timeout=930):
            seen.append(json.loads(body))
            return Upstream(b'{"choices":[{"message":{"content":"OK"}}]}')
        server = core.Server(('127.0.0.1', 0), core.Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(core, 'upstream_open', side_effect=open_upstream):
                body = json.dumps({'model': 'test', 'messages': [{'role': 'user', 'content': 'oi'}]}).encode()
                req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/chat/completions', data=body, headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    self.assertEqual(json.load(response)['choices'][0]['message']['content'], 'OK')
            self.assertIs(seen[0]['stream'], False)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
