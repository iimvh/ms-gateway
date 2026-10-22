from gateway.anthropic_models import CountTokensRequest, MessagesRequest


def test_unknown_top_level_and_block_fields_are_preserved():
    req = MessagesRequest.model_validate(
        {
            "model": "claude-sonnet-4-5",
            "max_tokens": 8192,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hi", "future_field": 7}]}
            ],
            "brand_new_field": {"a": 1},
            "anthropic_beta": ["x"],
        }
    )
    assert req.brand_new_field == {"a": 1}
    assert req.messages[0].content[0].future_field == 7


def test_defaults_and_system_shapes():
    a = MessagesRequest.model_validate({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert a.stream is False and a.max_tokens == 8192 and a.system is None
    b = MessagesRequest.model_validate(
        {"model": "m", "max_tokens": 10, "messages": [], "system": [{"type": "text", "text": "s"}]}
    )
    assert b.system[0].text == "s"


def test_tool_choice_and_thinking_keep_their_shape():
    req = MessagesRequest.model_validate(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "x"}],
            "tool_choice": {"type": "tool", "name": "Bash"},
            "thinking": {"type": "enabled", "budget_tokens": 1024},
        }
    )
    assert req.tool_choice["name"] == "Bash"
    assert req.thinking["budget_tokens"] == 1024


def test_missing_max_tokens_defaults_to_8192():
    # spec §7.8: max_tokens is mandatory in the Anthropic API; default it rather than 400
    req = MessagesRequest.model_validate({"model": "m", "messages": [{"role": "user", "content": "x"}]})
    assert req.max_tokens == 8192


def test_tool_with_no_schema_gets_an_empty_object_schema():
    req = MessagesRequest.model_validate(
        {
            "model": "m",
            "messages": [{"role": "user", "content": "x"}],
            "tools": [{"name": "Bash", "description": "d"}],
        }
    )
    assert req.tools[0].input_schema == {"type": "object", "properties": {}}


def test_count_tokens_request_accepts_the_same_shapes_without_max_tokens():
    req = CountTokensRequest.model_validate(
        {
            "model": "m",
            "system": "sys",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "tools": [{"name": "T", "input_schema": {"type": "object", "properties": {}}}],
        }
    )
    assert req.system == "sys" and req.tools[0].name == "T"
