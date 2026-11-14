import io
import unittest

import responses_bridge


class Handler:
    def __init__(self): self.wfile = io.BytesIO(); self.error = None
    def fail(self, status, message): self.error = (status, message)
    def send_response(self, status): pass
    def send_header(self, *args): pass
    def end_headers(self): pass


class ResponsesTests(unittest.TestCase):
    def test_broken_nonstream_returns_error(self):
        handler = Handler()
        responses_bridge.relay(handler, io.BytesIO(b'data: [DONE]\n\n'), {'model': 'test'}, (set(), {}))
        self.assertEqual(handler.error[0], 502)

    def test_empty_stream_reports_failure(self):
        handler = Handler()
        upstream = io.BytesIO(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        responses_bridge.relay(handler, upstream, {'model': 'test', 'stream': True}, (set(), {}))
        self.assertIn(b'event: response.failed', handler.wfile.getvalue())
        self.assertNotIn(b'event: response.completed', handler.wfile.getvalue())


if __name__ == '__main__': unittest.main()
