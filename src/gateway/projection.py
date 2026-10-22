"""Schema projection for non-scalar tool parameters (spec §7.9).

The fenced tool format the Node proxy emulates cannot carry a JSON array as a
fence body: a single-parameter tool's only parameter *becomes* the body, and the
parser returns that body as a raw string. So every `array`/`object` parameter is
renamed to `<name>_json`, typed `string`, and parsed back into the original name
and shape here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}
_DROP = object()


def _is_non_scalar(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    kind = schema.get("type")
    if isinstance(kind, list):
        return bool({"array", "object"} & set(kind))
    if kind in ("array", "object"):
        return True
    return "items" in schema or "properties" in schema


def _parse_non_scalar(value: Any) -> Any:
    """JSON first, YAML second; anything that is not a list/object is dropped."""
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        return _DROP
    text = value.strip()
    if not text:
        return _DROP
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            return _DROP
    return parsed if isinstance(parsed, (list, dict)) else _DROP


@dataclass(frozen=True)
class Projection:
    """tool name → {projected parameter name: original parameter name}"""

    tools: dict[str, dict[str, str]] = field(default_factory=dict)

    def restore(self, tool_name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        mapping = self.tools.get(tool_name)
        restored: dict[str, Any] = {}
        for key, value in args.items():
            original = (mapping or {}).get(key)
            if original is None:
                restored[key] = value
                continue
            parsed = _parse_non_scalar(value)
            if parsed is _DROP:
                continue
            restored[original] = parsed
        return restored


def project_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], Projection]:
    """Convert Anthropic tool definitions to OpenAI function tools, projecting
    non-scalar parameters. Tools with no schema get an empty object schema."""
    projected_tools: list[dict[str, Any]] = []
    mapping: dict[str, dict[str, str]] = {}

    for tool in tools:
        name = str(tool.get("name") or "")
        schema = tool.get("input_schema") or EMPTY_SCHEMA
        properties = schema.get("properties") or {}
        required = list(schema.get("required") or [])

        new_properties: dict[str, Any] = {}
        renamed: dict[str, str] = {}
        for param_name, param_schema in properties.items():
            if not _is_non_scalar(param_schema):
                new_properties[param_name] = param_schema
                continue
            projected_name = f"{param_name}_json"
            renamed[param_name] = projected_name
            description = param_schema.get("description") if isinstance(param_schema, dict) else None
            kind = param_schema.get("type", "value") if isinstance(param_schema, dict) else "value"
            new_properties[projected_name] = {
                "type": "string",
                "description": (
                    f"JSON-encoded {description}" if description else f"JSON-encoded {param_name} ({kind})"
                ),
            }

        new_required = [renamed.get(item, item) for item in required]
        parameters: dict[str, Any] = {"type": "object", "properties": new_properties}
        if new_required:
            parameters["required"] = new_required

        projected_tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    **({"description": tool["description"]} if tool.get("description") else {}),
                    "parameters": parameters,
                },
            }
        )
        if renamed:
            mapping[name] = {projected: original for original, projected in renamed.items()}

    return projected_tools, Projection(tools=mapping)


def describe(projection: Projection) -> list[dict[str, Any]]:
    """The tap's `projected_tools` field: a visible record of the adaptation."""
    return [
        {"tool": name, "params": sorted(params)}
        for name, params in projection.tools.items()
    ]
