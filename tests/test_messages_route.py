import asyncio
import json

import httpx
import pytest
from conftest import UPSTREAM, sse_events, tap_lines, with_usage

from gateway.app import run_shielded

MESSAGES = {
    "model": "claude-sonnet-4-5",
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "hi"}],
}

TOOLS = [
    {
        "name": "Bash",
        "description": "Run a shell command",
        "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
    }
]


def post(client, body=None):
    return client.post("/v1/messages", json={**MESSAGES, **(body or {})})


def test_non_stream_text_turn(client, respx_ok_text, settings):
    response = post(client)
    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "message"
    assert payload["content"] == [{"type": "text", "text": "hello"}]
    assert payload["model"] == "claude-sonnet-4-5"
    assert payload["stop_reason"] == "end_turn"
    assert response.headers["x-m365-dea-score"] == "1e-08"
    assert "estimate" not in json.dumps(payload["usage"])

    lines = tap_lines(settings.tap_dir)
    assert len(lines) == 1
    line = lines[0]
    assert line["status"] == 200 and line["stop_reason"] == "end_turn"
    assert line["model_requested"] == "claude-sonnet-4-5"
    assert line["model_mapped"] == "claude-sonnet"
    assert line["tools_count"] == 0 and line["est_input_tokens"] > 0
    assert line["elapsed_ms"] >= 0 and line["x_m365_dea_score"] == 1e-08


def test_query_string_and_auth_headers_are_ignored(client, respx_ok_text):
    response = client.post(
        "/v1/messages?beta=true",
        json=MESSAGES,
        headers={"x-api-key": "anything", "anthropic-beta": "unknown-beta-2026", "x-app": "cli"},
    )
    assert response.status_code == 200


def test_stream_emits_the_six_events_in_order(client, respx_ok_text):
    response = post(client, {"stream": True})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response.text)
    assert [name for name, _ in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    by_name = dict(events)
    assert by_name["message_start"]["message"]["content"] == []
    assert by_name["message_start"]["message"]["usage"]["output_tokens"] == 0
    assert by_name["content_block_delta"]["delta"] == {"type": "text_delta", "text": "hello"}
    assert by_name["message_delta"]["delta"]["stop_reason"] == "end_turn"
    assert by_name["message_stop"] == {"type": "message_stop"}


def test_stream_tool_turn_delivers_one_whole_json_delta(client, respx_ok_tool):
    response = post(client, {"stream": True, "tools": TOOLS})
    events = dict(sse_events(response.text))
    assert events["content_block_start"]["content_block"] == {
        "type": "tool_use",
        "id": "call_abc",
        "name": "Bash",
        "input": {},
    }
    assert json.loads(events["content_block_delta"]["delta"]["partial_json"]) == {"command": "ls"}
    assert events["message_delta"]["delta"]["stop_reason"] == "tool_use"


def test_tool_result_is_sent_back_as_a_tool_message(client, respx_ok_tool, upstream):
    response = post(
        client,
        {
            "tools": TOOLS,
            "messages": [
                {"role": "user", "content": "list files"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call_abc", "name": "Bash", "input": {"command": "ls"}}
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call_abc", "content": "a.txt"}],
                },
            ],
        },
    )
    assert response.status_code == 200
    sent = json.loads(upstream.calls.last.request.content)
    assert [message["role"] for message in sent["messages"]] == ["user", "assistant", "tool"]
    assert sent["messages"][2]["tool_call_id"] == "call_abc"


def test_disengage_is_a_400_even_when_streaming_was_requested(client, upstream):
    upstream.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(
            502, json={"error": {"message": "M365 Copilot disengaged", "type": "disengaged"}}
        )
    )
    response = post(client, {"stream": True})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "m365_disengaged"
    assert response.headers["content-type"].startswith("application/json")


def test_throttle_is_a_429_with_retry_after(client, upstream):
    upstream.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"message": "hold", "code": "m365_throttle_hold"}},
            headers={"Retry-After": "30"},
        )
    )
    response = post(client)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    assert response.json()["error"]["type"] == "rate_limit_error"


def test_conversation_guard_refuses_at_95_percent(client, upstream, settings):
    upstream.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json=with_usage(96))
    )
    response = post(client)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "m365_conversation_exhausted"
    assert "compact" in response.json()["error"]["message"]
    assert tap_lines(settings.tap_dir)[0]["status"] == 400


def test_a_degenerate_upstream_body_is_a_retryable_503(client, upstream):
    upstream.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    response = post(client)
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "overloaded_error"


def test_unmappable_model_falls_back_to_the_default(client, respx_ok_text, upstream):
    post(client, {"model": "totally-unknown-model"})
    sent = json.loads(upstream.calls.last.request.content)
    assert sent["model"] == "claude-sonnet"


def test_force_model_overrides_mapping(upstream, settings, respx_ok_text):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from gateway.app import create_app

    with TestClient(create_app(replace(settings, force_model="gpt-5.5"))) as forced_client:
        forced_client.post("/v1/messages", json={**MESSAGES, "model": "claude-opus-4-8"})
    assert json.loads(upstream.calls.last.request.content)["model"] == "gpt-5.5"


def test_missing_max_tokens_is_accepted(client, respx_ok_text):
    body = {key: value for key, value in MESSAGES.items() if key != "max_tokens"}
    assert client.post("/v1/messages", json=body).status_code == 200


def test_invalid_body_is_a_400_with_the_field_named(client):
    response = client.post("/v1/messages", json={"model": "m"})
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "messages" in response.json()["error"]["message"]


async def test_cancelling_the_request_does_not_cancel_the_upstream_turn():
    started, finished = asyncio.Event(), asyncio.Event()

    async def work():
        started.set()
        await asyncio.sleep(0.05)
        finished.set()
        return "ok"

    task = asyncio.create_task(run_shielded(work()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(finished.wait(), timeout=1)


def test_tap_records_projected_tools(client, respx_ok_tool, settings):
    todos = {
        "name": "TodoWrite",
        "description": "track todos",
        "input_schema": {
            "type": "object",
            "properties": {"todos": {"type": "array", "items": {"type": "object"}}},
            "required": ["todos"],
        },
    }
    post(client, {"tools": [todos]})
    line = tap_lines(settings.tap_dir)[0]
    assert line["projected_tools"] == [{"tool": "TodoWrite", "params": ["todos_json"]}]
    assert line["tools_count"] == 1 and line["tools_chars"] > 0
    assert line["tool_calls"] == [{"name": "Bash", "args_chars": 16, "parse_failed": False}]
