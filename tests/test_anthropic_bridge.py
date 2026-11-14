import io
import json
import unittest

import anthropic_bridge as bridge


class Handler:
    def __init__(self): self.wfile=io.BytesIO(); self.response=None
    def send_response(self, status): self.status=status
    def send_header(self, *args): pass
    def end_headers(self): pass
    def write_json(self, status, body): self.status=status; self.response=body


def sse(*events):
    return io.BytesIO(b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events)+b'data: [DONE]\n\n')


class BridgeTests(unittest.TestCase):
    def test_tool_history_translation(self):
        p={'model':'test','max_tokens':100,'messages':[{'role':'assistant','content':[{'type':'tool_use','id':'call_1','name':'lookup','input':{'x':1}}]},{'role':'user','content':[{'type':'tool_result','tool_use_id':'call_1','content':'ok'}]}],'tools':[{'name':'lookup','input_schema':{'type':'object'}}]}
        req=bridge.translate(p)
        self.assertEqual(req['messages'][0]['tool_calls'][0]['id'],'call_1')
        self.assertEqual(req['messages'][1]['tool_call_id'],'call_1')
        self.assertEqual(req['tools'][0]['function']['parameters'],{'type':'object'})

    def test_complete_and_streaming_tool_calls(self):
        chunks=[{'choices':[{'delta':{'tool_calls':[{'index':0,'id':'call_1','function':{'name':'lookup','arguments':'{"x":'}}]}}]}, {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'1}'}}]},'finish_reason':'tool_calls'}], 'usage':{'prompt_tokens':3,'completion_tokens':4}}]
        h=Handler(); bridge.relay(h,sse(*chunks),{'model':'test'})
        self.assertEqual(h.response['content'][0]['input'],{'x':1})
        self.assertEqual(h.response['stop_reason'],'tool_use')
        self.assertEqual(h.response['usage']['output_tokens'],4)
        h=Handler(); bridge.relay(h,sse(*chunks),{'model':'test','stream':True})
        text=h.wfile.getvalue().decode()
        self.assertIn('event: content_block_start',text)
        self.assertIn('input_json_delta',text)
        self.assertIn('event: message_stop',text)

    def test_empty_stream_is_error(self):
        h=Handler(); bridge.relay(h,sse(),{'model':'test','stream':True})
        self.assertIn('event: error',h.wfile.getvalue().decode())
        self.assertNotIn('event: message_stop',h.wfile.getvalue().decode())

if __name__ == '__main__': unittest.main()
