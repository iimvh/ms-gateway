import json

from gateway.projection import describe, project_tools

TODO = {
    "name": "TodoWrite",
    "description": "d",
    "input_schema": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {"type": "object", "properties": {"content": {"type": "string"}}},
            }
        },
        "required": ["todos"],
    },
}


def test_array_param_is_projected_to_a_json_string():
    tools, proj = project_tools([TODO])
    params = tools[0]["function"]["parameters"]
    assert set(params["properties"]) == {"todos_json"}
    assert params["properties"]["todos_json"]["type"] == "string"
    assert "JSON-encoded" in params["properties"]["todos_json"]["description"]
    assert params["required"] == ["todos_json"]
    assert proj.tools == {"TodoWrite": {"todos_json": "todos"}}
    assert describe(proj) == [{"tool": "TodoWrite", "params": ["todos_json"]}]


def test_scalars_keep_their_names_and_nothing_is_projected():
    tools, proj = project_tools(
        [
            {
                "name": "Bash",
                "input_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            }
        ]
    )
    assert tools[0]["function"]["parameters"]["properties"] == {"command": {"type": "string"}}
    assert proj.tools == {} and describe(proj) == []


def test_restore_parses_json_and_restores_the_name():
    _, proj = project_tools([TODO])
    out = proj.restore("TodoWrite", {"todos_json": json.dumps([{"content": "a", "status": "pending"}])})
    assert out == {"todos": [{"content": "a", "status": "pending"}]}


def test_restore_accepts_a_yaml_block_and_drops_blank_values():
    _, proj = project_tools([TODO])
    yaml_block = "- content: a\n  status: pending\n"
    assert proj.restore("TodoWrite", {"todos_json": yaml_block})["todos"][0]["content"] == "a"
    assert proj.restore("TodoWrite", {"todos_json": "   "}) == {}


def test_restore_is_a_noop_for_unprojected_tools_and_unknown_params():
    _, proj = project_tools([TODO])
    assert proj.restore("Bash", {"command": "ls"}) == {"command": "ls"}
    assert proj.restore("TodoWrite", {"todos_json": "not json {{{"}) == {}


def test_object_param_is_projected_too_and_required_order_is_kept():
    tool = {
        "name": "Config",
        "input_schema": {
            "type": "object",
            "properties": {"settings": {"type": "object"}, "path": {"type": "string"}},
            "required": ["path", "settings"],
        },
    }
    tools, proj = project_tools([tool])
    assert tools[0]["function"]["parameters"]["required"] == ["path", "settings_json"]
    assert proj.restore("Config", {"path": "/x", "settings_json": '{"a":1}'}) == {
        "path": "/x",
        "settings": {"a": 1},
    }
