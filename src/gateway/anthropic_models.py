"""Permissive pydantic models for the Anthropic request shapes Claude Code sends.

Anthropic adds fields and beta blocks often, so every model here allows extra
fields and keeps them; validation exists to guarantee the handful of fields
translation depends on, never to reject the client's payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MAX_TOKENS = 8192
EMPTY_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


class _Permissive(BaseModel):
    model_config = ConfigDict(extra="allow")


class Block(_Permissive):
    """One content block: text, tool_use, tool_result, thinking, image, …"""

    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    content: Any = None
    is_error: bool | None = None
    source: dict[str, Any] | None = None
    cache_control: dict[str, Any] | None = None


class MessageParam(_Permissive):
    role: str
    content: str | list[Block]


class ToolParam(_Permissive):
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=lambda: dict(EMPTY_OBJECT_SCHEMA))
    cache_control: dict[str, Any] | None = None


class MessagesRequest(_Permissive):
    """`POST /v1/messages` body. Fields the proxy ignores are still declared so
    the tap can record them."""

    model: str
    messages: list[MessageParam]
    max_tokens: int = DEFAULT_MAX_TOKENS
    system: str | list[Block] | None = None
    tools: list[ToolParam] | None = None
    tool_choice: dict[str, Any] | None = None
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    metadata: dict[str, Any] | None = None
    stop_sequences: list[str] | None = None
    thinking: dict[str, Any] | None = None
    service_tier: str | None = None


class CountTokensRequest(_Permissive):
    """`POST /v1/messages/count_tokens` body — everything `MessagesRequest` has
    except the output-side knobs."""

    model: str
    messages: list[MessageParam]
    system: str | list[Block] | None = None
    tools: list[ToolParam] | None = None
