"""Upstream failure → Anthropic error (spec §7.7).

The whole point of the non-streaming upstream mode: every row here arrives with a
real HTTP status, so a `Disengaged` is a fatal 400 the client will not retry, and
a throttle is a 429 with `retry-after`.
"""

from __future__ import annotations

from typing import Any

CONVERSATION_EXHAUSTED_PCT = 95


class AnthropicError(Exception):
    """Carries everything the HTTP layer needs to answer the client."""

    def __init__(
        self,
        status: int,
        type: str,
        message: str,
        code: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.type = type
        self.message = message
        self.code = code
        self.retry_after = retry_after

    def to_body(self) -> dict[str, Any]:
        error: dict[str, Any] = {"type": self.type, "message": self.message}
        if self.code:
            error["code"] = self.code
        return {"type": "error", "error": error}

    def headers(self) -> dict[str, str]:
        return {"retry-after": str(self.retry_after)} if self.retry_after is not None else {}


def _parse_retry_after(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if text.isdigit() else None


def _error_dict(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return {}


def _generic_message(status: int) -> str:
    return f"the M365 proxy returned HTTP {status}"


def _disengaged_message(upstream_message: str) -> str:
    detail = f" Upstream said: {upstream_message}" if upstream_message else ""
    return (
        "M365 Copilot disengaged from this request (its content filter declined to answer). "
        "Try another model or a leaner toolset. Not retried: sending the same payload again "
        f"would burn the account's quota for the same refusal.{detail}"
    )


def map_upstream_error(status: int, body: Any, retry_after: str | None) -> AnthropicError:
    error = _error_dict(body)
    upstream_message = str(error.get("message") or "").strip()
    kind = str(error.get("type") or "")
    code = error.get("code") or None
    message = upstream_message or _generic_message(status)
    retry = _parse_retry_after(retry_after)

    if status == 429:
        return AnthropicError(429, "rate_limit_error", message, code, retry)
    if status == 502 and kind == "disengaged":
        return AnthropicError(
            400, "invalid_request_error", _disengaged_message(upstream_message), "m365_disengaged"
        )
    if status == 502 and kind == "upstream_empty_response":
        return AnthropicError(503, "overloaded_error", message, code, retry)
    if status == 502 and code == "model_route_unavailable":
        return AnthropicError(404, "not_found_error", message, code)
    if status >= 500:
        return AnthropicError(502, "api_error", message, code, retry)
    if status >= 400:
        return AnthropicError(status, "invalid_request_error", message, code, retry)
    return AnthropicError(502, "api_error", message, code, retry)


def conversation_exhausted_error(pct: int) -> AnthropicError:
    return AnthropicError(
        400,
        "invalid_request_error",
        f"This M365 conversation is {pct}% used (the cap is per conversation, ~600 messages). "
        "Run /compact, or start a new session to continue.",
        "m365_conversation_exhausted",
    )
