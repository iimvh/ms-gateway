import json

from gateway.anthropic_models import MessagesRequest
from gateway.translate_request import build_upstream_request


def build(body, model="claude-sonnet"):
    return build_upstream_request(MessagesRequest.model_validate(body), model)


def test_system_string_becomes_one_leading_system_message():
    up = build(
        {"model": "x", "max_tokens": 5, "system": "S1", "messages": [{"role": "user", "content": "hi"}]}
    )
    assert up.payload["messages"][0] == {"role": "system", "content": "S1"}
    assert up.payload["stream"] is False and up.payload["model"] == "claude-sonnet"


def test_system_blocks_are_joined_and_cache_control_dropped():
    up = build(
        {
            "model": "x",
            "system": [
                {"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "B"},
            ],
            "messages": [{"role": "user", "content": "hi"}],
        }
    )
    assert up.payload["messages"][0]["content"] == "A\n\nB"


def test_empty_system_sends_no_system_message():
    for system in ("", []):
        up = build({"model": "x", "system": system, "messages": [{"role": "user", "content": "hi"}]})
        assert [m["role"] for m in up.payload["messages"]] == ["user"]


def test_tool_result_blocks_become_tool_messages_before_trailing_text():
    up = build(
        {
            "model": "x",
            "messages": [
                {"role": "user", "content": "run it"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "ok"},
                        {"type": "tool_use", "id": "call_1", "name": "Bash", "input": {"command": "ls"}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_1", "content": "a\nb", "is_error": True},
                        {"type": "text", "text": "and now edit"},
                    ],
                },
            ],
        }
    )
    roles = [m["role"] for m in up.payload["messages"]]
    assert roles == ["user", "assistant", "tool", "user"]
    assert up.payload["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "[tool error] a\nb",
    }
    assert up.payload["messages"][3]["content"] == "and now edit"
    asst = up.payload["messages"][1]
    assert asst["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "Bash", "arguments": '{"command":"ls"}'},
        }
    ]
    assert asst["content"] == "ok"


def test_assistant_null_content_with_tool_calls_stays_null():
    up = build(
        {
            "model": "x",
            "messages": [
                {"role": "user", "content": "u"},
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call_9", "name": "Bash", "input": {}}],
                },
            ],
        }
    )
    assert up.payload["messages"][1]["content"] is None


def test_tool_result_with_image_or_empty_content_still_becomes_a_tool_message():
    for content in (
        [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}}],
        [],
    ):
        up = build(
            {
                "model": "x",
                "messages": [
                    {"role": "user", "content": "u"},
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {}}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "c1", "content": content}],
                    },
                ],
            }
        )
        assert up.payload["messages"][2] == {"role": "tool", "tool_call_id": "c1", "content": ""}


def test_tools_and_tool_choice_map():
    up = build(
        {
            "model": "x",
            "messages": [{"role": "user", "content": "u"}],
            "tools": [
                {
                    "name": "Bash",
                    "description": "d",
                    "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}},
                }
            ],
            "tool_choice": {"type": "any"},
        }
    )
    assert up.payload["tools"][0]["function"]["name"] == "Bash"
    assert up.payload["tool_choice"] == "required"
    assert up.tools_count == 1 and up.messages_count == 1 and up.system_chars == 0


def test_tool_choice_none_and_tool_forms():
    base = {"model": "x", "messages": [{"role": "user", "content": "u"}], "tools": [{"name": "Bash"}]}
    assert build({**base, "tool_choice": {"type": "none"}}).payload["tool_choice"] == "none"
    assert build({**base, "tool_choice": {"type": "auto"}}).payload["tool_choice"] == "auto"
    assert build({**base, "tool_choice": {"type": "tool", "name": "Bash"}}).payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "Bash"},
    }
    assert "tool_choice" not in build(base).payload


def test_dropped_params_are_dropped_and_estimates_counted():
    up = build(
        {
            "model": "x",
            "max_tokens": 1024,
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 5,
            "stop_sequences": ["END"],
            "thinking": {"type": "enabled", "budget_tokens": 1024},
            "metadata": {"user_id": "u"},
            "service_tier": "auto",
            "messages": [{"role": "user", "content": "0123456789" * 4}],
        }
    )
    for key in (
        "max_tokens",
        "temperature",
        "top_p",
        "top_k",
        "stop_sequences",
        "thinking",
        "metadata",
        "service_tier",
    ):
        assert key not in up.payload
    assert up.est_input_tokens == 10  # 40 chars / 4
    assert up.system_chars == 0 and up.tools_chars == 0


def test_estimate_includes_system_and_tools():
    up = build(
        {
            "model": "x",
            "system": "y" * 40,
            "messages": [{"role": "user", "content": "x" * 40}],
            "tools": [{"name": "Bash", "description": "z" * 400}],
        }
    )
    assert up.est_input_tokens > 20
    assert up.system_chars == 40 and up.tools_chars > 400


def test_tool_without_schema_goes_upstream_with_an_empty_object_schema():
    up = build({"model": "x", "messages": [{"role": "user", "content": "u"}], "tools": [{"name": "T"}]})
    assert up.payload["tools"][0]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_json_arguments_are_compact():
    up = build(
        {
            "model": "x",
            "messages": [
                {"role": "user", "content": "u"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "c", "name": "T", "input": {"a": 1, "b": [1, 2]}}]},
            ],
        }
    )
    arguments = up.payload["messages"][1]["tool_calls"][0]["function"]["arguments"]
    assert arguments == json.dumps({"a": 1, "b": [1, 2]}, separators=(",", ":"))
