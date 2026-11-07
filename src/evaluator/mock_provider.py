"""Offline OpenAI-compatible provider used only by release E2E tests."""

from __future__ import annotations

import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_MODELS = ("mock-fast", "mock-precise")


class Handler(BaseHTTPRequestHandler):
    server_version = "LLMMock/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json({"status": "ok"})
        elif self.path == "/v1/models":
            self._json({"object": "list", "data": [{"id": model} for model in _MODELS]})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1_000_000:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        try:
            request = json.loads(self.rfile.read(length))
            prompt = str(request["messages"][-1]["content"])
            model = str(request["model"])
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        content = _answer(prompt, model)
        usage: dict[str, Any] = {
            "prompt_tokens": max(1, len(prompt) // 4),
            "completion_tokens": max(1, len(content) // 4),
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        if request.get("stream") is True:
            self._stream(content, usage)
        else:
            self._json(
                {
                    "id": "mock-completion",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                }
            )

    def _stream(self, content: str, usage: dict[str, Any]) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Request-ID", "mock-stream")
        self.end_headers()
        midpoint = max(1, len(content) // 2)
        for part in (content[:midpoint], content[midpoint:]):
            event = {"choices": [{"delta": {"content": part}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.01)
        final = {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        }
        self.wfile.write(f"data: {json.dumps(final)}\n\ndata: [DONE]\n\n".encode())
        self.wfile.flush()

    def _json(self, value: dict[str, Any]) -> None:
        content = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Request-ID", "mock-json")
        self.end_headers()
        self.wfile.write(content)


def _answer(prompt: str, model: str) -> str:
    if "<evaluation-data>" in prompt:
        data_text = prompt.split("<evaluation-data>\n", 1)[1].split("\n</evaluation-data>", 1)[0]
        data = json.loads(data_text)
        criteria = [
            {"id": item["id"], "score": "1", "reason": "Mock criterion passed."}
            for item in data["rubric"]
        ]
        return json.dumps(
            {"score": "1", "passed": True, "reason": "Mock judge passed.", "criteria": criteria}
        )
    markers = {
        "[EXACT]": "exact antwoord",
        "[CONTAINS]": "Deze tekst bevat alpha en beta.",
        "[REGEX]": "ABC-123",
        "[NUMERIC]": "42.00",
        "[JSON]": '{"ok":true,"model":"' + model + '"}',
        "[JUDGE]": "Dit kandidaatantwoord is correct en volledig.",
    }
    return next((answer for marker, answer in markers.items() if marker in prompt), "mock answer")


def main() -> None:
    host = os.environ.get("MOCK_HOST", "0.0.0.0")
    port = int(os.environ.get("MOCK_PORT", "8000"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
