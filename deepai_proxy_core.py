#!/usr/bin/env python3
"""Portable OpenAI-compatible gateway for the DeepAI model catalog."""
import argparse
import base64
import copy
import json
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import responses_bridge
import contas_proxy

BASE = "https://api.deepai.org/deepai_code"
INSTANCE = secrets.token_hex(16)
CATALOG_TTL_SECONDS = 60
_CATALOG = None
_CATALOG_AT = 0.0
_CATALOG_LOCK = threading.Lock()


def upstream_open(path, body=None, timeout=930):
    headers = {'Accept-Encoding': 'identity'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    return contas_proxy.abrir(BASE + path, body, headers, INSTANCE, timeout)


def load_catalog(force=False):
    global _CATALOG, _CATALOG_AT
    with _CATALOG_LOCK:
        now = time.monotonic()
        if not force and _CATALOG is not None and now - _CATALOG_AT < CATALOG_TTL_SECONDS:
            return _CATALOG
        with upstream_open("/models", timeout=20) as response:
            payload = json.load(response)
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise ValueError("Catálogo DeepAI inválido.")
        _CATALOG = [m for m in models if isinstance(m, dict) and isinstance(m.get("id"), str)]
        _CATALOG_AT = time.monotonic()
        return _CATALOG


def public_model(model):
    inputs = model.get("input") if isinstance(model.get("input"), list) else ["text"]
    reasoning = bool(model.get("reasoning"))
    result = {
        "id": model["id"],
        "object": "model",
        "created": 0,
        "owned_by": "deepai",
        "name": model.get("name", model["id"]),
        "context_window": model.get("contextWindow"),
        "max_output_tokens": model.get("maxTokens"),
        "reasoning": reasoning,
        "vision": "image" in inputs,
        "streaming": True,
        "reasoning_efforts": ["off", "minimal", "low", "medium", "high"] if reasoning else [],
        "capabilities": {
            "text": True,
            "vision": "image" in inputs,
            "reasoning": reasoning,
            "streaming": True,
            "tools": True,
        },
        "input": list(inputs),
        "premier": bool(model.get("premier")),
    }
    if "api" in model:
        result["api"] = model["api"]
    if "theosPerRequest" in model:
        result["theos_per_request"] = model["theosPerRequest"]
    if "theosPerSecond" in model:
        result["theos_per_second"] = model["theosPerSecond"]
    return result


def normalize_chat_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("JSON inválido.")
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Modelo não informado.")
    if model.startswith("deepai/"):
        payload["model"] = model[7:]
    if payload.get("reasoning_effort") == "off":
        payload["reasoning_effort"] = "none"
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort") == "off":
        payload["reasoning"] = {**reasoning, "effort": "none"}
    return payload


def normalize_stream_event(event, requested_model=None):
    if not isinstance(event, dict):
        return event
    event = copy.deepcopy(event)
    if requested_model:
        event["model"] = requested_model
    event.pop("provider", None)
    event.pop("service_tier", None)
    for choice in event.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        if isinstance(delta.get("reasoning"), str) and not delta.get("reasoning_content"):
            delta["reasoning_content"] = delta["reasoning"]
        delta.pop("reasoning_details", None)
    return event


def error_message(payload):
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        value = error.get("message")
        return value if isinstance(value, str) and value.strip() else None
    if isinstance(error, str) and error.strip():
        return error
    detail = payload.get("detail")
    return detail if isinstance(detail, str) and detail.strip() else None


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def write_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def fail(self, status, message, retry_after=None, code="deepai_error"):
        if not message.startswith("DeepAI"):
            message = "DeepAI — " + message
        headers = {"Retry-After": retry_after} if retry_after else None
        self.write_json(status, {
            "error": {
                "message": message,
                "type": "provider_error",
                "code": code,
                "provider": "DeepAI",
                "upstream_status": status,
            }
        }, headers)

    def upstream_error(self, error):
        retry_after = error.headers.get("Retry-After") if error.headers else None
        message = None
        code = "deepai_error"
        try:
            raw = error.read()
            payload = json.loads(raw) if raw else None
            message = error_message(payload)
            nested = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(nested, dict) and isinstance(nested.get("code"), str):
                code = nested["code"]
        except (OSError, ValueError, TypeError):
            pass
        self.fail(error.code, message or "Não foi possível concluir a requisição.", retry_after, code)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Content-Length inválido.")
        if length < 1 or length > 33554432:
            raise ValueError("Tamanho de requisição inválido.")
        raw = self.rfile.read(length)
        return json.loads(raw)

    def model_by_id(self, model_id):
        wanted = urllib.parse.unquote(model_id)
        for model in load_catalog():
            if model.get("id") == wanted:
                return public_model(model)
        return None

    def proxy_chat(self, payload):
        payload = normalize_chat_payload(payload)
        requested_model = payload["model"]
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            upstream = upstream_open("/chat/completions", body)
        except urllib.error.HTTPError as error:
            return self.upstream_error(error)
        with upstream:
            content_type = upstream.headers.get("Content-Type", "application/json")
            stream = "text/event-stream" in content_type.lower()
            self.send_response(upstream.status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            retry_after = upstream.headers.get("Retry-After")
            if retry_after:
                self.send_header("Retry-After", retry_after)
            self.end_headers()
            self.close_connection = True
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if not stream:
                raw = upstream.read()
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        data["model"] = requested_model
                    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    pass
                self.wfile.write(raw)
                self.wfile.flush()
                return
            for line in upstream:
                if line.startswith(b"data:"):
                    raw = line[5:].strip()
                    if raw and raw != b"[DONE]":
                        try:
                            event = normalize_stream_event(json.loads(raw), requested_model)
                            line = b"data: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
                        except (ValueError, UnicodeDecodeError):
                            pass
                elif line.startswith(b":"):
                    continue
                self.wfile.write(line)
                self.wfile.flush()

    def proxy_responses(self, payload):
        payload = normalize_chat_payload(payload)
        requested_model = payload["model"]
        if requested_model != "gpt-6-astra":
            response_request = copy.deepcopy(payload)
            chat_payload, custom = responses_bridge.translate(payload)
            body = json.dumps(chat_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            try:
                upstream = upstream_open("/chat/completions", body)
            except urllib.error.HTTPError as error:
                return self.upstream_error(error)
            with upstream:
                if upstream.status >= 400:
                    return self.fail(upstream.status, "Não foi possível concluir a requisição.", upstream.headers.get("Retry-After"))
                responses_bridge.relay(self, upstream, response_request, custom)
            return
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        try:
            upstream = upstream_open("/responses", body)
        except urllib.error.HTTPError as error:
            return self.upstream_error(error)
        with upstream:
            content_type = upstream.headers.get("Content-Type", "application/json")
            self.send_response(upstream.status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            while True:
                chunk = upstream.read1(65536) if hasattr(upstream, "read1") else upstream.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()

    def handle_request(self):
        if self.headers.get("Origin"):
            return self.fail(403, "Requisições de navegador não são permitidas.")
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if self.command == "GET" and path == "/health":
                return self.write_json(200, {"status": "ok", "provider": "DeepAI", "version": "1.0"})
            if self.command == "GET" and path == "/v1":
                return self.write_json(200, {"object": "api", "provider": "DeepAI", "models": "/v1/models"})
            if self.command == "GET" and path == "/v1/models":
                models = [public_model(model) for model in load_catalog()]
                return self.write_json(200, {"object": "list", "data": models})
            if self.command == "GET" and path.startswith("/v1/models/"):
                model = self.model_by_id(path[len("/v1/models/"):])
                return self.write_json(200, model) if model else self.fail(404, "Modelo não encontrado.", code="model_not_found")
            if self.command == "POST" and path == "/v1/chat/completions":
                return self.proxy_chat(self.read_body())
            if self.command == "POST" and path == "/v1/responses":
                return self.proxy_responses(self.read_body())
            return self.fail(404, "Endpoint não suportado.", code="not_found")
        except responses_bridge.Unsupported as error:
            return self.fail(400, str(error), code="unsupported_request")
        except (ValueError, AttributeError, KeyError, TypeError) as error:
            return self.fail(400, str(error) or "JSON inválido.", code="invalid_request")
        except urllib.error.HTTPError as error:
            return self.upstream_error(error)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except OSError as error:
            try:
                return self.fail(502, "Falha de conexão com o serviço.", code="connection_error")
            except OSError:
                self.close_connection = True

    do_GET = handle_request
    do_POST = handle_request


class Server(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(port):
    server = Server(("127.0.0.1", port), Gateway)
    actual = server.server_port
    runtime = Path(__file__).resolve().parent / 'runtime_proxy.json'
    try:
        runtime.write_text(json.dumps({'pid':os.getpid(),'port':actual}), encoding='utf-8')
        try: os.chmod(runtime, 0o600)
        except OSError: pass
    except OSError: pass
    print(f"DeepAI Proxy em http://127.0.0.1:{actual}/v1", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if json.loads(runtime.read_text()).get('pid') == os.getpid(): runtime.unlink()
        except (OSError, ValueError, AttributeError): pass


def main():
    if os.environ.get("DEEPAI_PROXY_INTERNAL") != "1":
        sys.exit("Use o inicializador incluído nesta pasta.")
    parser = argparse.ArgumentParser()
    parser.add_argument("acao", nargs="?", default="serve", choices=["serve"])
    parser.add_argument("--porta", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.porta <= 65535:
        parser.error("porta deve estar entre 0 e 65535")
    serve(args.porta)


if __name__ == "__main__":
    main()
