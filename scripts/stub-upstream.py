#!/usr/bin/env python
"""A stub of the Node proxy's OpenAI surface, for exercising the real `claude` CLI
without an M365 account (spec §10: the live loop is an operator step).

It answers `/v1/models`, `/v1/chat/completions` (non-streaming JSON, which is all
the gateway sends) and logs every request body to stderr so the wire shapes the
real Claude Code 2.1.308 client produces are visible.

    uv run python scripts/stub-upstream.py [--port 4141]

Turn 1 returns a `Bash` tool call; once the incoming conversation contains a
`role:"tool"` message it answers with text instead, so one full tool loop closes.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = {
    "object": "list",
    "data": [{"id": "claude-sonnet", "object": "model", "owned_by": "microsoft"}],
}


def completion(message: dict, finish_reason: str) -> dict:
    return {
        "id": "chatcmpl-stub",
        "object": "chat.completion",
        "created": 0,
        "model": "claude-sonnet",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", **message},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "x_m365_conversation_pct": 3,
            "x_m365_dea_score": 1e-08,
            "x_m365_content_origin": "chat",
        },
    }


def answer(payload: dict) -> dict:
    messages = payload.get("messages") or []
    saw_tool_result = any(message.get("role") == "tool" for message in messages)
    if saw_tool_result:
        return completion({"content": "hi from hello.py"}, "stop")
    return completion(
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_stub1",
                    "type": "function",
                    "function": {
                        "name": "Bash",
                        "arguments": json.dumps({"command": "python hello.py"}),
                    },
                }
            ],
        },
        "tool_calls",
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # quieter default access log
        sys.stderr.write("[stub] " + fmt % args + "\n")

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send(200, MODELS)
        else:
            self._send(
                404,
                {"error": {"message": f"no route {self.path}", "type": "invalid_request_error"}},
            )

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self._send(
                404,
                {"error": {"message": f"no route {self.path}", "type": "invalid_request_error"}},
            )
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "bad json", "type": "invalid_request_error"}})
            return
        roles = [message.get("role") for message in payload.get("messages") or []]
        tools = [tool.get("function", {}).get("name") for tool in payload.get("tools") or []]
        sys.stderr.write(
            f"[stub] request: model={payload.get('model')} stream={payload.get('stream')} "
            f"roles={roles} tools={tools}\n"
        )
        self._send(200, answer(payload))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4141)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    sys.stderr.write(f"[stub] listening on http://127.0.0.1:{args.port}/v1\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
