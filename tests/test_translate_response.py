import json

import pytest

from gateway.errors import AnthropicError
from gateway.projection import project_tools
from gateway.translate_response import build_turn, sse

NO_PROJECTION = project_tools([])[1]


def completion(message, finish_reason="stop"):
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "claude-sonnet",
        "choices": [
            {"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish_reason}
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "x_m365_conversation_pct": 12,
            "x_m365_dea_score": 1e-08,
        },
    }


def turn(msg, finish="stop", est=7, proj=None):
    return build_turn(
        completion(msg, finish),
        model_requested="claude-sonnet-4-5",
        projection=proj or NO_PROJECTION,
        est_input_tokens=est,
    )


def test_text_turn():
    t = turn({"content": "hello"})
    assert t.stop_reason == "end_turn"
    assert t.message["content"] == [{"type": "text", "text": "hello"}]
    assert t.message["model"] == "claude-sonnet-4-5"  # echoes the request, not the upstream id
    assert t.message["usage"] == {"input_tokens": 7, "output_tokens": 2}
    assert t.text_chars == 5 and t.tool_calls == []
    assert t.message["type"] == "message" and t.message["role"] == "assistant"
    assert t.message["stop_sequence"] is None and t.message["id"].startswith("msg_")


def test_length_maps_to_max_tokens():
    assert turn({"content": "x"}, finish="length").stop_reason == "max_tokens"


def test_tool_turn_reuses_the_upstream_id_and_parses_arguments():
    proj = project_tools([{"name": "Bash"}])[1]
    t = turn(
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "Bash", "arguments": '{"command":"ls"}'},
                }
            ],
        },
        finish="tool_calls",
        proj=proj,
    )
    assert t.stop_reason == "tool_use"
    assert t.message["content"] == [
        {"type": "tool_use", "id": "call_abc", "name": "Bash", "input": {"command": "ls"}}
    ]
    assert t.message["usage"]["output_tokens"] == 0
    assert t.tool_calls == [{"name": "Bash", "args_chars": 16, "parse_failed": False}]


def test_unparseable_arguments_yield_empty_input_and_a_tap_flag():
    t = turn(
        {
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "Bash", "arguments": '{"command": "ls'}}
            ],
        },
        finish="tool_calls",
    )
    assert t.message["content"][0]["input"] == {}
    assert t.tool_calls[0]["parse_failed"] is True


def test_non_object_arguments_are_a_parse_failure_too():
    t = turn(
        {
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "T", "arguments": "[1,2]"}}],
        },
        finish="tool_calls",
    )
    assert t.message["content"][0]["input"] == {}
    assert t.tool_calls[0]["parse_failed"] is True


def test_projected_param_is_restored_on_the_turn():
    proj = project_tools(
        [{"name": "TodoWrite", "input_schema": {"type": "object", "properties": {"todos": {"type": "array"}}}}]
    )[1]
    t = turn(
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "TodoWrite",
                        "arguments": json.dumps({"todos_json": '[{"content":"a","status":"pending"}]'}),
                    },
                }
            ],
        },
        finish="tool_calls",
        proj=proj,
    )
    assert t.message["content"][0]["input"] == {"todos": [{"content": "a", "status": "pending"}]}


def test_degenerate_completion_raises_upstream_empty():
    for bad in ({"choices": []}, {"choices": [{"index": 0, "finish_reason": "stop"}]}):
        with pytest.raises(AnthropicError) as e:
            build_turn(
                bad,
                model_requested="m",
                projection=NO_PROJECTION,
                est_input_tokens=1,
            )
        assert e.value.status == 503 and e.value.type == "overloaded_error"


def test_text_delta_event_carries_the_whole_text():
    t = turn({"content": "hello"})
    assert t.events[0][0] == "message_start"
    assert t.events[0][1]["message"]["content"] == []
    assert t.events[0][1]["message"]["usage"] == {"input_tokens": 7, "output_tokens": 0}
    assert t.events[1] == ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    assert t.events[2] == (
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}},
    )
    assert t.events[3] == ("content_block_stop", {"type": "content_block_stop", "index": 0})
    assert t.events[4][0] == "message_delta"
    assert t.events[4][1]["delta"] == {"stop_reason": "end_turn", "stop_sequence": None}
    assert t.events[4][1]["usage"] == {"output_tokens": 2}
    assert t.events[5] == ("message_stop", {"type": "message_stop"})


def test_tool_delta_carries_the_restored_arguments_as_one_json_chunk():
    proj = project_tools(
        [{"name": "TodoWrite", "input_schema": {"type": "object", "properties": {"todos": {"type": "array"}}}}]
    )[1]
    t = turn(
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "TodoWrite", "arguments": json.dumps({"todos_json": "[]"})},
                }
            ],
        },
        finish="tool_calls",
        proj=proj,
    )
    events = dict(t.events)
    assert events["content_block_start"]["content_block"] == {
        "type": "tool_use",
        "id": "c1",
        "name": "TodoWrite",
        "input": {},
    }
    assert json.loads(events["content_block_delta"]["delta"]["partial_json"]) == {"todos": []}
    assert events["content_block_delta"]["delta"]["type"] == "input_json_delta"
    assert events["message_delta"]["delta"]["stop_reason"] == "tool_use"


def test_sse_serialises_each_event_with_name_and_data():
    t = turn({"content": "hi"})
    text = sse(t.events)
    assert text.startswith("event: message_start\ndata: {")
    assert text.count("event: ") == len(t.events)
    assert text.endswith("\n\n")
    first_data = text.split("\n")[1].removeprefix("data: ")
    assert json.loads(first_data)["type"] == "message_start"
