"""OpenAI completion → Anthropic message and the six-event SSE sequence
(spec §7.2, §7.3).

The turn is translated whole: the upstream call is non-streaming, so there are no
live deltas to forward, and a failed turn is a real HTTP error instead of an SSE
stream that opens with 200.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from typing import Any

from .errors import AnthropicError
from .log import get_logger
from .projection import Projection

log = get_logger("translate_response")


@dataclass(frozen=True)
class TranslatedTurn:
    message: dict[str, Any]
    events: list[tuple[str, dict[str, Any]]]
    stop_reason: str
    text_chars: int
    tool_calls: list[dict[str, Any]]


def estimate_text_tokens(text: str) -> int:
    """chars/4, rounded up — an estimate, never presented as an M365 count."""
    return math.ceil(len(text) / 4)


def _parse_arguments(raw: Any) -> tuple[dict[str, Any], bool]:
    if isinstance(raw, dict):
        return raw, False
    if not isinstance(raw, str) or not raw.strip():
        return {}, False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}, True
    if not isinstance(parsed, dict):
        return {}, True
    return parsed, False


def _stop_reason(finish_reason: str | None) -> str:
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return "end_turn"


def _message(payload: Any, *, model_requested: str, projection: Projection, est_input_tokens: int):
    if not isinstance(payload, dict):
        raise AnthropicError(
            503, "overloaded_error", "the M365 proxy returned no completion choices"
        )
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AnthropicError(
            503, "overloaded_error", "the M365 proxy returned no completion choices"
        )
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        raise AnthropicError(
            503, "overloaded_error", "the M365 proxy returned a completion without a message"
        )
    return first, message


def build_turn(
    completion: dict[str, Any],
    *,
    model_requested: str,
    projection: Projection,
    est_input_tokens: int,
) -> TranslatedTurn:
    first, message = _message(
        completion,
        model_requested=model_requested,
        projection=projection,
        est_input_tokens=est_input_tokens,
    )

    text = message.get("content") or ""
    if not isinstance(text, str):
        text = ""
    stop_reason = _stop_reason(first.get("finish_reason"))

    blocks: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    raw_calls = message.get("tool_calls")
    for call in raw_calls if isinstance(raw_calls, list) else []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(function.get("name") or "")
        raw_arguments = function.get("arguments")
        args_chars = (
            len(raw_arguments)
            if isinstance(raw_arguments, str)
            else len(json.dumps(raw_arguments or {}))
        )
        arguments, parse_failed = _parse_arguments(raw_arguments)
        if parse_failed:
            log.warning("tool call %s had arguments that are not a JSON object", name)
        restored = projection.restore(name, arguments)
        call_id = call.get("id") or f"toolu_{uuid.uuid4().hex[:24]}"
        blocks.append({"type": "tool_use", "id": call_id, "name": name, "input": restored})
        tool_calls.append({"name": name, "args_chars": args_chars, "parse_failed": parse_failed})

    if blocks:
        stop_reason = "tool_use"
        if text:
            blocks.insert(0, {"type": "text", "text": text})
    elif text or not blocks:
        blocks = [{"type": "text", "text": text}]

    output_tokens = estimate_text_tokens(text)
    payload = {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model_requested,
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": est_input_tokens, "output_tokens": output_tokens},
    }

    events = _events(
        payload, blocks, stop_reason, est_input_tokens=est_input_tokens, output_tokens=output_tokens
    )
    return TranslatedTurn(
        message=payload,
        events=events,
        stop_reason=stop_reason,
        text_chars=len(text),
        tool_calls=tool_calls,
    )


def _events(
    payload: dict[str, Any],
    blocks: list[dict[str, Any]],
    stop_reason: str,
    *,
    est_input_tokens: int,
    output_tokens: int,
) -> list[tuple[str, dict[str, Any]]]:
    started = {
        **payload,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": est_input_tokens, "output_tokens": 0},
    }
    events: list[tuple[str, dict[str, Any]]] = [
        ("message_start", {"type": "message_start", "message": started})
    ]

    for index, block in enumerate(blocks):
        if block["type"] == "tool_use":
            events.append(
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": {},
                        },
                    },
                )
            )
            partial = json.dumps(block["input"], separators=(",", ":"), ensure_ascii=False)
            events.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "input_json_delta", "partial_json": partial},
                    },
                )
            )
        else:
            events.append(
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            )
            events.append(
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": block["text"]},
                    },
                )
            )
        events.append(("content_block_stop", {"type": "content_block_stop", "index": index}))

    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return events


def sse(events: list[tuple[str, dict[str, Any]]]) -> str:
    """Serialize as Anthropic SSE: an `event:` line and a compact `data:` line."""
    return "".join(
        f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        for name, data in events
    )
