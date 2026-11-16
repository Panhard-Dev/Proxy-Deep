"""Estimativa agregada da última requisição, sem guardar texto ou credenciais."""
import threading
import time

_lock = threading.Lock()
_ultimo = None


def _estimar_texto(texto):
    ascii_count = sum(1 for c in texto if ord(c) < 128)
    return (ascii_count + 3) // 4 + len(texto) - ascii_count


def _contar(valor):
    if isinstance(valor, str):
        return _estimar_texto(valor), 0
    if isinstance(valor, list):
        partes = [_contar(x) for x in valor]
        return sum(x[0] for x in partes), sum(x[1] for x in partes)
    if isinstance(valor, dict):
        if valor.get('type') in ('image', 'image_url', 'input_image'):
            return 0, 1
        partes = [_contar(v) for k, v in valor.items() if k != 'encrypted_content']
        return sum(x[0] for x in partes), sum(x[1] for x in partes)
    return 0, 0


def analisar(payload):
    if not isinstance(payload, dict): return None
    partes = {'instrucoes': 0, 'ferramentas': 0, 'resultados': 0, 'conversa': 0}
    imagens = 0
    def somar(categoria, valor):
        nonlocal imagens
        tokens, quantidade = _contar(valor)
        partes[categoria] += tokens
        imagens += quantidade
    somar('instrucoes', payload.get('instructions', ''))
    somar('instrucoes', payload.get('system', ''))
    somar('ferramentas', payload.get('tools', []))
    items = payload.get('messages', payload.get('input', []))
    if isinstance(items, str): items = [{'role': 'user', 'content': items}]
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict): continue
            role, typ = item.get('role'), item.get('type')
            if role in ('system', 'developer'):
                categoria = 'instrucoes'
            elif role == 'tool' or typ in ('function_call_output', 'custom_tool_call_output'):
                categoria = 'resultados'
            else:
                categoria = 'conversa'
            # Anthropic mistura resultados de ferramentas com blocos do usuário.
            content = item.get('content')
            if isinstance(content, list):
                resto = {k: v for k, v in item.items() if k != 'content'}
                somar(categoria, resto)
                for bloco in content:
                    alvo = 'resultados' if isinstance(bloco, dict) and bloco.get('type') == 'tool_result' else categoria
                    somar(alvo, bloco)
            else:
                somar(categoria, item)
    model = payload.get('model')
    armazenado = bool(payload.get('previous_response_id') or payload.get('conversation'))
    if isinstance(items, list):
        armazenado = armazenado or any(isinstance(x, dict) and x.get('type') == 'compaction' for x in items)
    return {'modelo': model[:128] if isinstance(model, str) else '', 'partes': partes,
            'estimativa': sum(partes.values()), 'imagens': imagens,
            'historico_remoto': armazenado,
            'horario': time.time()}


def registrar(payload, memoria_identificada=False):
    global _ultimo
    try:
        resultado = analisar(payload)
        if resultado is not None: resultado['memoria_identificada'] = bool(memoria_identificada)
    except (TypeError, ValueError, RecursionError):
        resultado = None
    with _lock: _ultimo = resultado


def ultimo():
    with _lock:
        if _ultimo is None: return None
        return {**_ultimo, 'partes': dict(_ultimo['partes'])}
