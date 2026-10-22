"""Anthropic request → OpenAI chat-completion request (spec §7.1).

The upstream call is always non-streaming, and every parameter M365 ignores is
dropped rather than forwarded (the tap still records what the client asked for).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from .anthropic_models import EMPTY_OBJECT_SCHEMA, Block, MessagesRequest
from .log import get_logger
from .projection import Projection, project_tools

log = get_logger("translate_request")


@dataclass(frozen=True)
class UpstreamRequest:
    payload: dict[str, Any]
    projection: Projection
    est_input_tokens: int
    tools_count: int
    tools_chars: int
    system_chars: int
    messages_count: int


def _block_kind(block: Any) -> str | None:
    if isinstance(block, Block):
        return block.type
    if isinstance(block, dict):
        return block.get("type")
    return None


def _system_text(system: str | list[Block] | None) -> str:
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    parts = [block.text for block in system if block.type == "text" and block.text]
    return "\n\n".join(parts)


def _tool_result_content(block: Block) -> str:
    content = block.content
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            kind = _block_kind(item)
            if isinstance(item, str):
                parts.append(item)
            elif kind == "text":
                value = item.text if isinstance(item, Block) else item.get("text")
                if value:
                    parts.append(value)
            elif kind == "image":
                log.info("dropping an image block from a tool_result (M365 takes no images)")
        text = "".join(parts)
    else:
        text = str(content)
    return f"[tool error] {text}" if block.is_error else text


def _assistant_message(blocks: list[Block]) -> dict[str, Any]:
    texts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in blocks:
        if block.type == "text" and block.text:
            texts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(
                {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input or {}, separators=(",", ":")),
                    },
                }
            )
        elif block.type in ("thinking", "redacted_thinking"):
            continue
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(texts) if texts else None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _user_messages(blocks: list[Block]) -> list[dict[str, Any]]:
    """Tool results first (OpenAI requires them adjacent to the assistant turn),
    then whatever text the client added around them."""
    out: list[dict[str, Any]] = []
    texts: list[str] = []
    for block in blocks:
        if block.type == "tool_result":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": _tool_result_content(block),
                }
            )
        elif block.type == "text" and block.text:
            texts.append(block.text)
        elif block.type == "image":
            log.info("dropping an image block from a user message (M365 takes no images)")
    if texts:
        out.append({"role": "user", "content": "\n".join(texts)})
    return out


def _convert_message(message: Any) -> list[dict[str, Any]]:
    if message.role == "assistant":
        return [_assistant_message(message.content)] if isinstance(message.content, list) else [
            {"role": "assistant", "content": message.content}
        ]
    if message.role == "user":
        if isinstance(message.content, str):
            return [{"role": "user", "content": message.content}]
        return _user_messages(message.content)
    # `system` inside messages is not an Anthropic shape, but tolerate it.
    if isinstance(message.content, str):
        return [{"role": message.role, "content": message.content}]
    texts = [b.text for b in message.content if b.type == "text" and b.text]
    return [{"role": message.role, "content": "\n".join(texts)}]


def _tool_choice(choice: dict[str, Any] | None) -> Any:
    if not choice:
        return None
    kind = choice.get("type")
    if kind == "auto":
        return "auto"
    if kind == "any":
        return "required"
    if kind == "none":
        return "none"
    if kind == "tool":
        return {"type": "function", "function": {"name": choice.get("name", "")}}
    return None


def _messages_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += len(content)
        for call in message.get("tool_calls") or []:
            total += len(call["function"]["arguments"])
    return total


def build_upstream_request(request: MessagesRequest, model: str) -> UpstreamRequest:
    """Translate one Anthropic request. `model` is the already-mapped upstream id."""
    messages: list[dict[str, Any]] = []

    system_text = _system_text(request.system)
    if system_text:
        messages.append({"role": "system", "content": system_text})

    for message in request.messages:
        messages.extend(_convert_message(message))

    tools: list[dict[str, Any]] = []
    projection = Projection()
    if request.tools:
        tools, projection = project_tools([tool.model_dump(exclude_none=True) for tool in request.tools])

    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools

    choice = _tool_choice(request.tool_choice)
    if choice is not None:
        payload["tool_choice"] = choice

    system_chars = len(system_text)
    tools_chars = len(json.dumps(tools)) if tools else 0
    messages_chars = _messages_chars(messages)
    est_input_tokens = math.ceil((system_chars + tools_chars + messages_chars) / 4)

    return UpstreamRequest(
        payload=payload,
        projection=projection,
        est_input_tokens=est_input_tokens,
        tools_count=len(request.tools or []),
        tools_chars=tools_chars,
        system_chars=system_chars,
        messages_count=len(messages),
    )
