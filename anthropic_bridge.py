"""Tradução da API Messages para Chat Completions."""
import json
import secrets

from responses_bridge import Unsupported


def translate(payload):
    messages = []
    system = payload.get('system')
    if system:
        if isinstance(system, list):
            system = '\n'.join(x.get('text', '') for x in system if x.get('type') == 'text')
        if not isinstance(system, str): raise Unsupported('Sistema inválido.')
        messages.append({'role': 'system', 'content': system})
    for msg in payload.get('messages', []):
        role = msg.get('role')
        if role not in ('user', 'assistant'): raise Unsupported('Papel não suportado.')
        content = msg.get('content', '')
        if isinstance(content, str):
            messages.append({'role': role, 'content': content})
            continue
        if not isinstance(content, list): raise Unsupported('Conteúdo inválido.')
        parts, calls = [], []
        def flush():
            if parts or calls:
                message = {'role': role, 'content': parts[:] if parts else None}
                if calls: message['tool_calls'] = calls[:]
                messages.append(message)
                parts.clear(); calls.clear()
        for block in content:
            typ = block.get('type')
            if typ == 'text': parts.append({'type': 'text', 'text': block.get('text', '')})
            elif typ == 'image':
                source = block.get('source', {})
                if source.get('type') == 'base64':
                    url = 'data:' + source.get('media_type', '') + ';base64,' + source.get('data', '')
                elif source.get('type') == 'url': url = source.get('url', '')
                else: raise Unsupported('Imagem não suportada.')
                parts.append({'type': 'image_url', 'image_url': {'url': url}})
            elif typ == 'tool_use' and role == 'assistant':
                calls.append({'id': block['id'], 'type': 'function', 'function': {'name': block['name'], 'arguments': json.dumps(block.get('input', {}), ensure_ascii=False)}})
            elif typ == 'tool_result' and role == 'user':
                flush()
                result = block.get('content', '')
                if not isinstance(result, str): result = json.dumps(result, ensure_ascii=False)
                messages.append({'role': 'tool', 'tool_call_id': block['tool_use_id'], 'content': result})
            elif typ in ('thinking', 'redacted_thinking') and role == 'assistant': continue
            else: raise Unsupported('Bloco Anthropic não suportado: ' + str(typ))
        flush()
    if not messages: raise Unsupported('Mensagens não informadas.')
    request = {'model': payload['model'], 'messages': messages, 'stream': True}
    for field in ('max_tokens', 'temperature', 'top_p', 'stop_sequences'):
        if field in payload: request['stop' if field == 'stop_sequences' else field] = payload[field]
    if payload.get('tools'):
        request['tools'] = [{'type': 'function', 'function': {'name': t['name'], 'description': t.get('description', ''), 'parameters': t.get('input_schema', {'type': 'object', 'properties': {}})}} for t in payload['tools']]
    choice = payload.get('tool_choice') or {}
    if choice.get('type') in ('auto', 'none'): request['tool_choice'] = choice['type']
    elif choice.get('type') == 'any': request['tool_choice'] = 'required'
    elif choice.get('type') == 'tool': request['tool_choice'] = {'type': 'function', 'function': {'name': choice['name']}}
    if choice.get('disable_parallel_tool_use'): request['parallel_tool_calls'] = False
    return request


def relay(handler, upstream, payload):
    streaming = bool(payload.get('stream'))
    mid = 'msg_' + secrets.token_hex(12)
    blocks, calls = [], {}
    usage = {'input_tokens': 0, 'output_tokens': 0}
    stop = 'end_turn'
    message = {'id': mid, 'type': 'message', 'role': 'assistant', 'model': payload['model'], 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': usage.copy()}
    if streaming:
        handler.send_response(200); handler.send_header('Content-Type', 'text/event-stream'); handler.send_header('Cache-Control', 'no-store'); handler.send_header('Connection', 'close'); handler.end_headers(); handler.close_connection = True
    def event(typ, **data):
        if streaming:
            obj = {'type': typ, **data}
            handler.wfile.write(('event: ' + typ + '\ndata: ' + json.dumps(obj, ensure_ascii=False) + '\n\n').encode()); handler.wfile.flush()
    event('message_start', message=message)
    text_index = None
    for line in upstream:
        if not line.startswith(b'data:'): continue
        raw = line[5:].strip()
        if not raw or raw == b'[DONE]': continue
        try: chunk = json.loads(raw)
        except (ValueError, UnicodeDecodeError): continue
        if isinstance(chunk.get('error'), dict):
            detail = chunk['error'].get('message') or 'Erro na resposta da DeepAI.'
            if streaming:
                event('error', error={'type': 'api_error', 'message': str(detail)})
                return
            raise Unsupported(str(detail))
        u = chunk.get('usage') or {}
        if u.get('prompt_tokens') is not None: usage['input_tokens'] = u['prompt_tokens']
        if u.get('completion_tokens') is not None: usage['output_tokens'] = u['completion_tokens']
        for choice in chunk.get('choices') or []:
            delta = choice.get('delta') or {}
            text = delta.get('content')
            if isinstance(text, str) and text:
                if text_index is None:
                    text_index = len(blocks); blocks.append({'type': 'text', 'text': ''})
                    event('content_block_start', index=text_index, content_block={'type': 'text', 'text': ''})
                blocks[text_index]['text'] += text
                event('content_block_delta', index=text_index, delta={'type': 'text_delta', 'text': text})
            for call in delta.get('tool_calls') or []:
                ci = call.get('index', 0); fn = call.get('function') or {}
                if ci not in calls:
                    idx = len(blocks); calls[ci] = idx
                    blocks.append({'type': 'tool_use', 'id': call.get('id') or 'toolu_'+secrets.token_hex(8), 'name': fn.get('name') or '', '_args': ''})
                    block = blocks[idx]
                    event('content_block_start', index=idx, content_block={'type': 'tool_use', 'id': block['id'], 'name': block['name'], 'input': {}})
                idx = calls[ci]; block = blocks[idx]
                if fn.get('name') and not block['name']: block['name'] = fn['name']
                args = fn.get('arguments') or ''
                if args:
                    block['_args'] += args
                    event('content_block_delta', index=idx, delta={'type': 'input_json_delta', 'partial_json': args})
            reason = choice.get('finish_reason')
            if reason == 'length': stop = 'max_tokens'
            elif reason == 'tool_calls': stop = 'tool_use'
    if not blocks:
        if streaming:
            event('error', error={'type': 'api_error', 'message': 'A DeepAI retornou uma resposta vazia.'})
            return
        raise Unsupported('A DeepAI retornou uma resposta vazia.')
    for idx, block in enumerate(blocks):
        if block['type'] == 'tool_use':
            try: block['input'] = json.loads(block.pop('_args') or '{}')
            except ValueError:
                if streaming:
                    event('error', error={'type': 'api_error', 'message': 'Argumentos de ferramenta inválidos.'})
                    return
                raise Unsupported('Argumentos de ferramenta inválidos.')
        event('content_block_stop', index=idx)
    if streaming:
        event('message_delta', delta={'stop_reason': stop, 'stop_sequence': None}, usage={'output_tokens': usage['output_tokens']})
        event('message_stop')
    else:
        message.update(content=blocks, stop_reason=stop, usage=usage)
        handler.write_json(200, message)
