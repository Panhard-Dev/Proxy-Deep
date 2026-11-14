"""Translate the Responses subset used by Codex to Chat Completions."""
import hashlib, json, secrets, time

class Unsupported(ValueError): pass

def _deduplicar_repeticao(texto):
    texto=texto.strip()
    if not texto: return ''
    periodo=(texto+texto).find(texto,1,len(texto)*2-1)
    if 2<=periodo<len(texto) and len(texto)%periodo==0: return texto[:periodo]
    return texto

def convert_content(content):
    if isinstance(content, str) or content is None: return content
    if not isinstance(content, list): raise Unsupported('Conteúdo de mensagem inválido.')
    result=[]
    for part in content:
        typ=part.get('type')
        if typ in ('input_text','output_text','text'): result.append({'type':'text','text':part.get('text','')})
        elif typ=='input_image':
            image=part.get('image_url')
            if not isinstance(image,str): raise Unsupported('Imagem sem URL ou data URL não é suportada.')
            result.append({'type':'image_url','image_url':{'url':image,**({'detail':part['detail']} if part.get('detail') else {})}})
        else: raise Unsupported('Conteúdo não suportado: '+str(typ))
    return result

def translate(p):
    if p.get('previous_response_id') or p.get('conversation'): raise Unsupported('Conversas armazenadas não são suportadas; envie o histórico completo.')
    messages=[]; pending=[]
    tool_specs=[]; names={}
    for tool in p.get('tools',[]):
        if tool.get('type') == 'namespace':
            ns=tool.get('name')
            if not ns: raise Unsupported('Namespace sem nome.')
            for child in tool.get('tools',[]):
                child=child.copy(); original=child.get('name')
                if not original: raise Unsupported('Ferramenta sem nome.')
                readable=ns+'__'+original
                alias=readable if len(readable)<=64 else readable[:51]+'_'+hashlib.sha256(readable.encode()).hexdigest()[:12]
                names[alias]=(ns,original); child['name']=alias; tool_specs.append(child)
        else: tool_specs.append(tool)
    def alias_for(ns, name):
        if not ns: return name
        for alias, pair in names.items():
            if pair==(ns,name): return alias
        raise Unsupported('Ferramenta do histórico não consta na lista: '+str(name))
    def flush():
        if pending: messages.append({'role':'assistant','content':None,'tool_calls':pending.copy()}); pending.clear()
    if p.get('instructions'): messages.append({'role':'system','content':p['instructions']})
    inp=p.get('input',[])
    if isinstance(inp,str): inp=[{'role':'user','content':inp}]
    if not isinstance(inp,list): raise Unsupported('Input deve ser texto ou uma lista.')
    for item in inp:
        if not isinstance(item,dict): raise Unsupported('Item de entrada inválido.')
        typ=item.get('type','message')
        if typ in ('function_call','custom_tool_call'):
            name=alias_for(item.get('namespace'),item.get('name')); call_id=item.get('call_id')
            if not name or not call_id: raise Unsupported('Chamada de ferramenta incompleta.')
            args=item.get('arguments','{}') if typ=='function_call' else json.dumps({'input':item.get('input','')},ensure_ascii=False)
            pending.append({'id':call_id,'type':'function','function':{'name':name,'arguments':args}})
        elif typ in ('function_call_output','custom_tool_call_output'):
            flush(); output=item.get('output','')
            if not isinstance(output,str): output=json.dumps(output,ensure_ascii=False)
            messages.append({'role':'tool','tool_call_id':item.get('call_id',''),'content':output})
        elif typ=='message':
            flush(); role=item.get('role','user')
            if role=='developer': role='system'
            if role not in ('user','assistant','system'): raise Unsupported('Papel não suportado: '+str(role))
            messages.append({'role':role,'content':convert_content(item.get('content',''))})
        elif typ=='reasoning':
            continue
        else: raise Unsupported('Item de entrada não suportado: '+str(typ))
    flush()
    custom=set(); tools=[]
    for t in tool_specs:
        typ=t.get('type'); name=t.get('name')
        if typ=='function':
            if not name: raise Unsupported('Ferramenta sem nome.')
            f={'name':name,'parameters':t.get('parameters') or {'type':'object','properties':{}}}
            if 'description' in t: f['description']=t['description']
            if t.get('strict') is not None: f['strict']=t['strict']
            tools.append({'type':'function','function':f})
        elif typ=='custom':
            if not name: raise Unsupported('Ferramenta sem nome.')
            custom.add(name)
            tools.append({'type':'function','function':{'name':name,'description':t.get('description',''),'parameters':{'type':'object','properties':{'input':{'type':'string','description':'Entrada da ferramenta'}},'required':['input'],'additionalProperties':False}}})
        elif typ in ('web_search','web_search_preview'):
            continue  # Chat Completions não fornece a busca nativa do Codex.
        else: raise Unsupported('Tipo de ferramenta não suportado: '+str(typ))
    payload={'model':p['model'],'messages':messages,'stream':True}
    if tools: payload['tools']=tools
    for k in ('temperature','top_p','parallel_tool_calls'):
        if k in p and p[k] is not None: payload[k]=p[k]
    if p.get('max_output_tokens') is not None: payload['max_tokens']=p['max_output_tokens']
    effort=(p.get('reasoning') or {}).get('effort')
    if effort: payload['reasoning_effort']=effort
    choice=p.get('tool_choice')
    if isinstance(choice,str): payload['tool_choice']=choice
    elif isinstance(choice,dict) and choice.get('type') in ('function','custom'):
        payload['tool_choice']={'type':'function','function':{'name':choice['name']}}
    elif choice is not None: raise Unsupported('Seleção de ferramenta não suportada.')
    return payload,(custom,names)

def _relay(handler, upstream, request, custom, content_filter=None, reasoning_filter=None, dedupe_final=False, fallback_text=None):
    custom, names = custom
    rid='resp_'+secrets.token_hex(12); created=int(time.time()); seq=0; streaming=request.get('stream',False)
    output=[]; by_index={}; msg=None; reasoning=None; usage=None; ended=False; finish=None; pending_text=[]
    handler._bridge_context={'id':rid,'created_at':created,'model':request['model']}
    handler._bridge_sequence=0
    def response(status):
        return {'id':rid,'object':'response','created_at':created,'status':status,'model':request['model'],'output':output.copy(),'error':None,'incomplete_details':None,'usage':usage}
    def event(typ,**data):
        nonlocal seq
        if streaming:
            obj={'type':typ,'sequence_number':seq,**data}; handler.wfile.write(('event: '+typ+'\ndata: '+json.dumps(obj,ensure_ascii=False)+'\n\n').encode()); handler.wfile.flush()
        seq+=1; handler._bridge_sequence=seq
    def start_message():
        nonlocal msg
        if msg is None:
            msg={'id':'msg_'+secrets.token_hex(12),'type':'message','status':'in_progress','role':'assistant','content':[]}; idx=len(output); output.append(msg)
            event('response.output_item.added',output_index=idx,item=msg.copy())
            event('response.content_part.added',item_id=msg['id'],output_index=idx,content_index=0,part={'type':'output_text','text':'','annotations':[],'logprobs':[]})
        return output.index(msg)
    def add_call(delta):
        idx=delta.get('index',0); fn=delta.get('function') or {}
        if idx not in by_index:
            item={'id':'fc_'+secrets.token_hex(12),'type':'function_call','status':'in_progress','call_id':delta.get('id') or 'call_'+secrets.token_hex(8),'name':fn.get('name') or '', 'arguments':''}
            by_index[idx]=item; output.append(item)
        item=by_index[idx]
        if delta.get('id'): item['call_id']=delta['id']
        name=fn.get('name') or ''
        if name and name!=item['name']: item['name']+=name
        piece=fn.get('arguments') or ''
        if piece:
            item['arguments']+=piece
    def start_reasoning():
        nonlocal reasoning
        if reasoning is None:
            reasoning={'id':'rs_'+secrets.token_hex(12),'type':'reasoning','status':'in_progress','summary':[]}
            idx=len(output); output.append(reasoning)
            event('response.output_item.added',output_index=idx,item=reasoning.copy())
            part={'type':'summary_text','text':''}
            reasoning['summary'].append(part)
            event('response.reasoning_summary_part.added',item_id=reasoning['id'],output_index=idx,summary_index=0,part=part.copy())
        return output.index(reasoning)
    def add_reasoning(text):
        if not text: return
        idx=start_reasoning(); part=reasoning['summary'][0]
        part['text']+=text
        event('response.reasoning_summary_text.delta',item_id=reasoning['id'],output_index=idx,summary_index=0,delta=text)
    if streaming:
        handler.send_response(200)
        handler.send_header('Content-Type','text/event-stream; charset=utf-8')
        handler.send_header('Cache-Control','no-store'); handler.send_header('X-Accel-Buffering','no'); handler.send_header('Connection','close'); handler.end_headers(); handler.close_connection=True
        handler._bridge_started=True
    event('response.created',response=response('in_progress')); event('response.in_progress',response=response('in_progress'))
    for line in upstream:
        if not line.startswith(b'data:'): continue
        raw=line[5:].strip()
        if raw==b'[DONE]': ended=True; break
        try: chunk=json.loads(raw)
        except ValueError: continue
        if chunk.get('error'):
            detail=chunk['error']
            message=detail.get('message') if isinstance(detail,dict) else detail
            raise RuntimeError('O serviço não conseguiu concluir a resposta.')
        if chunk.get('usage'):
            u=chunk['usage']; usage={'input_tokens':u.get('prompt_tokens',0),'output_tokens':u.get('completion_tokens',0),'total_tokens':u.get('total_tokens',0),'input_tokens_details':{'cached_tokens':(u.get('prompt_tokens_details') or {}).get('cached_tokens',0)},'output_tokens_details':{'reasoning_tokens':(u.get('completion_tokens_details') or {}).get('reasoning_tokens',0)}}
        for choice in chunk.get('choices',[]):
            delta=choice.get('delta') or {}; text=delta.get('content') or ''
            if text and content_filter is not None: text=content_filter.feed(text)
            if text:
                if dedupe_final: pending_text.append(text)
                else:
                    i=start_message()
                    if not msg['content']: msg['content']=[{'type':'output_text','text':'','annotations':[],'logprobs':[]}]
                    msg['content'][0]['text']+=text
                    event('response.output_text.delta',item_id=msg['id'],output_index=i,content_index=0,delta=text,logprobs=[])
            thought=delta.get('reasoning_content')
            if not isinstance(thought,str): thought=delta.get('reasoning')
            if isinstance(thought,str) and thought:
                if reasoning_filter is not None: thought=reasoning_filter.feed(thought)
                add_reasoning(thought)
            for call in delta.get('tool_calls') or []: add_call(call)
            finish=choice.get('finish_reason') or finish
    if not ended or not finish: raise RuntimeError('A resposta terminou antes de ser concluída.')
    if content_filter is not None:
        restante=content_filter.flush()
        if restante:
            if dedupe_final: pending_text.append(restante)
            else:
                i=start_message()
                if not msg['content']: msg['content']=[{'type':'output_text','text':'','annotations':[],'logprobs':[]}]
                msg['content'][0]['text']+=restante
                event('response.output_text.delta',item_id=msg['id'],output_index=i,content_index=0,delta=restante,logprobs=[])
    if dedupe_final and pending_text:
        text=_deduplicar_repeticao(''.join(pending_text))
        if text:
            i=start_message()
            if not msg['content']: msg['content']=[{'type':'output_text','text':'','annotations':[],'logprobs':[]}]
            msg['content'][0]['text']+=text
            event('response.output_text.delta',item_id=msg['id'],output_index=i,content_index=0,delta=text,logprobs=[])
    if reasoning_filter is not None:
        restante=reasoning_filter.flush()
        if restante: add_reasoning(restante)
    if fallback_text and msg is None and not by_index:
        i=start_message()
        msg['content']=[{'type':'output_text','text':fallback_text,'annotations':[],'logprobs':[]}]
        event('response.output_text.delta',item_id=msg['id'],output_index=i,content_index=0,delta=fallback_text,logprobs=[])
    if msg is None and not by_index and finish != 'length':
        raise RuntimeError('A DeepAI retornou uma resposta sem texto ou ferramenta.')
    for i,item in enumerate(output):
        if item['type']=='message':
            part=item['content'][0]; event('response.output_text.done',item_id=item['id'],output_index=i,content_index=0,text=part['text'],logprobs=[])
            event('response.content_part.done',item_id=item['id'],output_index=i,content_index=0,part=part)
        elif item['type']=='reasoning':
            part=item['summary'][0]
            event('response.reasoning_summary_text.done',item_id=item['id'],output_index=i,summary_index=0,text=part['text'])
            event('response.reasoning_summary_part.done',item_id=item['id'],output_index=i,summary_index=0,part=part.copy())
        else:
            is_custom=item['name'] in custom
            if item['name'] in names:
                item['namespace'],item['name']=names[item['name']]
            if is_custom:
                try: val=json.loads(item['arguments']); value=val['input']
                except (ValueError,KeyError,TypeError): raise RuntimeError('Argumentos inválidos de ferramenta personalizada.')
                item.pop('arguments'); item['type']='custom_tool_call'; item['input']=value
                event('response.output_item.added',output_index=i,item={**item,'input':''})
            else:
                event('response.output_item.added',output_index=i,item={**item,'arguments':''})
                event('response.function_call_arguments.delta',item_id=item['id'],output_index=i,delta=item['arguments'])
                event('response.function_call_arguments.done',item_id=item['id'],output_index=i,arguments=item['arguments'])
        item['status']='completed'; event('response.output_item.done',output_index=i,item=item.copy())
    status='incomplete' if finish=='length' else 'completed'; final=response(status)
    if status=='incomplete': final['incomplete_details']={'reason':'max_output_tokens'}
    event('response.completed' if status=='completed' else 'response.incomplete',response=final)
    if streaming: handler.wfile.write(b'data: [DONE]\n\n'); handler.wfile.flush()
    else:
        body=json.dumps(final,ensure_ascii=False).encode()
        handler.send_response(200); handler.send_header('Content-Type','application/json; charset=utf-8')
        handler.send_header('Content-Length',str(len(body))); handler.send_header('Cache-Control','no-store')
        handler.end_headers(); handler._bridge_started=True; handler.wfile.write(body)


def relay(handler, upstream, request, custom, content_filter=None, reasoning_filter=None, dedupe_final=False, fallback_text=None):
    handler._bridge_started=False
    try:
        _relay(handler,upstream,request,custom,content_filter,reasoning_filter,dedupe_final,fallback_text)
    except (BrokenPipeError,ConnectionResetError):
        handler.close_connection=True
    except (OSError,ValueError,TypeError,KeyError,RuntimeError) as exc:
        message='DeepAI — Não foi possível concluir a resposta. Tente novamente.'
        if not handler._bridge_started:
            handler.fail(502,message)
        elif request.get('stream'):
            ctx=handler._bridge_context
            response={'id':ctx['id'],'object':'response','created_at':ctx['created_at'],'status':'failed','model':ctx['model'],'output':[],
                      'error':{'code':'deepai_error','message':message},'incomplete_details':None,'usage':None}
            event={'type':'response.failed','sequence_number':handler._bridge_sequence,'response':response}
            try:
                handler.wfile.write(('event: response.failed\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode()); handler.wfile.flush()
            except (BrokenPipeError,ConnectionResetError): pass
            handler.close_connection=True
        else: handler.close_connection=True
