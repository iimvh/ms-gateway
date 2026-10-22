from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from gateway.app import create_app
from gateway.config import Settings

UPSTREAM = "http://up/v1"

MODELS = {
    "object": "list",
    "data": [{"id": "claude-sonnet"}, {"id": "claude-opus"}, {"id": "gpt-5.5"}],
}

TEXT_COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "model": "claude-sonnet",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
    ],
    "usage": {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "x_m365_dea_score": 1e-08,
        "x_m365_conversation_pct": 12,
        "x_m365_content_origin": "chat",
    },
}

TOOL_COMPLETION = {
    "id": "chatcmpl-2",
    "object": "chat.completion",
    "model": "claude-sonnet",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "Bash", "arguments": '{"command":"ls"}'},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
}


def with_usage(pct: int) -> dict:
    completion = json.loads(json.dumps(TEXT_COMPLETION))
    completion["usage"]["x_m365_conversation_pct"] = pct
    return completion


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(upstream_url=UPSTREAM, tap_dir=tmp_path / "tap")


@pytest.fixture
def upstream():
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{UPSTREAM}/models").mock(return_value=httpx.Response(200, json=MODELS))
        yield mock


@pytest.fixture
def client(upstream, settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def respx_ok_text(upstream):
    upstream.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json=TEXT_COMPLETION)
    )
    return TEXT_COMPLETION


@pytest.fixture
def respx_ok_tool(upstream):
    upstream.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json=TOOL_COMPLETION)
    )
    return TOOL_COMPLETION


def sse_events(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in text.strip().split("\n\n"):
        name = None
        data = None
        for line in chunk.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if name and data is not None:
            events.append((name, data))
    return events


def tap_lines(tap_dir: Path) -> list[dict]:
    lines: list[dict] = []
    for path in sorted(Path(tap_dir).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                lines.append(json.loads(line))
    return lines
