"""The acceptance loop from spec §2, exercised through the gateway with a stub
upstream: a tool_use turn, then the tool_result going back in OpenAI shape.

This is the same wire contract the live `claude` run duplicates — the stub makes
it a build gate, the live run is the operator step (spec §10).
"""

import json

import httpx

from conftest import UPSTREAM, sse_events

SYSTEM = "You are Claude Code, Anthropic's official CLI."
TOOLS = [
    {
        "name": "Bash",
        "description": "Run a shell command",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    }
]


def tool_completion(call_id: str = "call_abc") -> dict:
    return {
        "id": "chatcmpl-tool",
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
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "Bash",
                                "arguments": '{"command":"python hello.py"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"x_m365_conversation_pct": 2},
    }


def text_completion(text: str = "hi from hello.py") -> dict:
    return {
        "id": "chatcmpl-text",
        "object": "chat.completion",
        "model": "claude-sonnet",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"x_m365_conversation_pct": 4},
    }


def test_tool_loop_round_trips_through_the_gateway(client, upstream, settings):
    upstream.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json=tool_completion())
    )
    first = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-5",
            "max_tokens": 4096,
            "system": SYSTEM,
            "tools": TOOLS,
            "stream": True,
            "messages": [{"role": "user", "content": "create hello.py that prints hi, run it"}],
        },
    )
    assert first.status_code == 200
    events = dict(sse_events(first.text))
    tool_use = events["content_block_start"]["content_block"]
    assert tool_use == {
        "type": "tool_use",
        "id": "call_abc",
        "name": "Bash",
        "input": {},
    }
    assert json.loads(events["content_block_delta"]["delta"]["partial_json"]) == {
        "command": "python hello.py"
    }

    upstream.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json=text_completion())
    )
    second = client.post(
        "/v1/messages",
        json={
            "model": "claude-sonnet-4-5",
            "max_tokens": 4096,
            "system": SYSTEM,
            "tools": TOOLS,
            "stream": True,
            "messages": [
                {"role": "user", "content": "create hello.py that prints hi, run it"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call_abc",
                            "name": "Bash",
                            "input": {"command": "python hello.py"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_abc", "content": "hi"}
                    ],
                },
            ],
        },
    )
    assert second.status_code == 200
    second_events = dict(sse_events(second.text))
    assert second_events["message_delta"]["delta"]["stop_reason"] == "end_turn"
    assert second_events["content_block_delta"]["delta"] == {
        "type": "text_delta",
        "text": "hi from hello.py",
    }

    sent = json.loads(upstream.calls.last.request.content)
    assert [message["role"] for message in sent["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert sent["messages"][0]["content"] == SYSTEM
    assistant_call = sent["messages"][2]["tool_calls"][0]
    assert assistant_call["id"] == "call_abc"
    assert json.loads(assistant_call["function"]["arguments"]) == {"command": "python hello.py"}
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_abc", "content": "hi"}
    assert sent["tools"][0]["function"]["name"] == "Bash"
    assert sent["stream"] is False

    lines = tap_lines_all(settings)
    assert [line["stop_reason"] for line in lines] == ["tool_use", "end_turn"]


def tap_lines_all(settings):
    from conftest import tap_lines

    return tap_lines(settings.tap_dir)
