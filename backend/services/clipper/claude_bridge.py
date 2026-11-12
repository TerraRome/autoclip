"""OpenAI-compatible endpoint backed by `claude -p`, so AutoClip analysis runs on a Claude subscription.

Listens on 127.0.0.1:11435 (fixed, like Ollama's 11434) so the address saved in the model
settings survives restarts of the desktop app, whose own API port changes every launch.

In AutoClip: Settings → Providers & models → Custom compatible endpoint
  API base URL  http://127.0.0.1:11435/v1    API key: anything    Model: sonnet / opus / haiku

Text only: image parts of a message are dropped, so pick another model for visual analysis.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

HOST, PORT = "127.0.0.1", 11435
BASE_URL = f"http://{HOST}:{PORT}/v1"
MODELS = ("sonnet", "opus", "haiku")
_server: ThreadingHTTPServer | None = None
_error = ""


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def split_messages(messages: list[dict]) -> tuple[str, str]:
    """(system prompt, conversation prompt) for `claude -p`."""
    system = "\n\n".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
    turns = [m for m in messages if m.get("role") != "system"]
    if len(turns) == 1:
        return system, _text(turns[0].get("content"))
    return system, "\n\n".join(f"[{m.get('role', 'user')}]\n{_text(m.get('content'))}" for m in turns)


def completion(answer: str, model: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}", "object": "chat.completion", "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def chunk(model: str, delta: dict, finish: str | None = None) -> dict:
    return {"id": "chatcmpl-claude-cli", "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class _Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeCliBridge/1.0"

    def log_message(self, fmt, *args):  # noqa: N802 - quiet
        logger.debug("claude bridge: " + fmt, *args)

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "anthropic"} for m in MODELS]})
        return self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "not found"}})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._json(400, {"error": {"message": "invalid JSON"}})
        model = str(body.get("model") or "sonnet")
        system, prompt = split_messages(body.get("messages") or [])
        from . import claude_cli
        if not body.get("stream"):
            try:
                return self._json(200, completion(claude_cli.run_text(system, prompt, model), model))
            except Exception as error:  # noqa: BLE001
                return self._json(502, {"error": {"message": str(error)[:300], "type": "claude_cli_error"}})
        # Stream: keep the socket alive with SSE comments while `claude -p` thinks.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        result: dict = {}
        worker = threading.Thread(target=lambda: result.update(_call(claude_cli, system, prompt, model)), daemon=True)
        worker.start()
        try:
            while worker.is_alive():
                worker.join(10)
                if worker.is_alive():
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            if "error" in result:
                self.wfile.write(f"data: {json.dumps({'error': {'message': result['error']}})}\n\n".encode("utf-8"))
            else:
                for piece in (chunk(model, {"role": "assistant", "content": result["text"]}), chunk(model, {}, "stop")):
                    self.wfile.write(f"data: {json.dumps(piece, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def _call(claude_cli, system, prompt, model) -> dict:
    try:
        return {"text": claude_cli.run_text(system, prompt, model)}
    except Exception as error:  # noqa: BLE001
        return {"error": str(error)[:300]}


def start() -> bool:
    """Start once per process when `claude` is installed and the setting is on. Never raises."""
    global _server, _error
    if _server is not None:
        return True
    try:
        from . import claude_cli, settings
        if not settings.load().get("claude_bridge", True) or not claude_cli.claude_bin():
            return False
        _server = ThreadingHTTPServer((HOST, PORT), _Handler)
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, name="claude-cli-bridge", daemon=True).start()
        logger.info("Claude CLI bridge listening on %s", BASE_URL)
        return True
    except OSError as error:  # port taken: another AutoClip already serves it
        _error = f"{type(error).__name__}: {error}"[:200]
        logger.warning("Claude CLI bridge not started: %s", _error)
        return False


def status() -> dict:
    from . import claude_cli
    return {"installed": bool(claude_cli.claude_bin()), "running": _server is not None, "base_url": BASE_URL,
            "models": list(MODELS), "error": _error}
