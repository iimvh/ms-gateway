"""HTTP routes. `/v1/messages` owns the whole translation path."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from .anthropic_models import CountTokensRequest, MessagesRequest
from .app import run_shielded
from .errors import AnthropicError, conversation_exhausted_error
from .log import get_logger
from .models_map import map_model
from .projection import describe
from .translate_request import build_upstream_request
from .translate_response import build_turn, sse
from .upstream import quota_headers

log = get_logger("routes")

CONVERSATION_GUARD_PCT = 95
KNOWN_MODELS_TTL_S = 300.0


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "body"
        parts.append(f"{location}: {error.get('msg')}")
    return "invalid Anthropic request — " + "; ".join(parts)


def _usage(completion: Any) -> dict[str, Any]:
    usage = completion.get("usage") if isinstance(completion, dict) else None
    return usage if isinstance(usage, dict) else {}


def _conversation_pct(completion: Any) -> int | None:
    value = _usage(completion).get("x_m365_conversation_pct")
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _m365_fields(completion: Any) -> dict[str, Any]:
    return {key: value for key, value in _usage(completion).items() if key.startswith("x_m365_")}


async def _known_models(request: Request) -> set[str]:
    """The upstream's model list, cached for a few minutes; a failure just means
    the family mapping decides (spec §7.6 passthrough row)."""
    state = request.app.state
    now = time.monotonic()
    if state.known_models is not None and now - state.known_models_at < KNOWN_MODELS_TTL_S:
        return state.known_models
    try:
        payload = await state.upstream.list_models()
    except AnthropicError as exc:
        log.info("could not list upstream models (%s); family mapping only", exc)
        state.known_models = state.known_models or set()
    else:
        data = payload.get("data") if isinstance(payload, dict) else None
        state.known_models = {
            item["id"]
            for item in (data or [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
    state.known_models_at = now
    return state.known_models


async def messages(request: Request) -> Response:
    state = request.app.state
    settings = state.settings
    started = time.perf_counter()
    record: dict[str, Any] = {"stream": False, "path": "/v1/messages"}

    try:
        try:
            body = await request.json()
        except ValueError as exc:
            raise AnthropicError(
                400, "invalid_request_error", f"request body is not JSON: {exc}"
            ) from exc

        try:
            parsed = MessagesRequest.model_validate(body)
        except ValidationError as exc:
            raise AnthropicError(400, "invalid_request_error", _validation_message(exc)) from exc

        record.update(
            {
                "stream": parsed.stream,
                "model_requested": parsed.model,
                "max_tokens": parsed.max_tokens,
            }
        )

        known = await _known_models(request)
        mapped = map_model(
            parsed.model,
            opus_model=settings.opus_model,
            default_model=settings.default_model,
            force_model=settings.force_model,
            known=known,
        )
        upstream_request = build_upstream_request(parsed, mapped)
        record.update(
            {
                "model_mapped": mapped,
                "system_chars": upstream_request.system_chars,
                "tools_count": upstream_request.tools_count,
                "tools_chars": upstream_request.tools_chars,
                "messages_count": upstream_request.messages_count,
                "est_input_tokens": upstream_request.est_input_tokens,
                "dropped_params": upstream_request.dropped,
                "projected_tools": describe(upstream_request.projection),
                "tools": [
                    tool["function"]["name"] for tool in upstream_request.payload.get("tools", [])
                ],
            }
        )

        if state.upstream.in_flight > 0:
            log.warning(
                "another upstream turn is already in flight — M365 throttles threads, "
                "and one Claude Code session per account is the documented assumption"
            )

        call_started = time.perf_counter()
        completion = await run_shielded(state.upstream.chat_completion(upstream_request.payload))
        record["upstream_ms"] = int((time.perf_counter() - call_started) * 1000)

        turn = build_turn(
            completion,
            model_requested=parsed.model,
            projection=upstream_request.projection,
            est_input_tokens=upstream_request.est_input_tokens,
        )
        record.update(
            {
                "stop_reason": turn.stop_reason,
                "text_chars": turn.text_chars,
                "tool_calls": turn.tool_calls,
                "x_m365": _m365_fields(completion),
            }
        )

        pct = _conversation_pct(completion)
        if pct is not None and pct >= CONVERSATION_GUARD_PCT:
            raise conversation_exhausted_error(pct)

        headers = quota_headers(completion, {})
        record["status"] = 200
        if parsed.stream:
            return StreamingResponse(
                iter([sse(turn.events)]),
                media_type="text/event-stream",
                headers={**headers, "cache-control": "no-cache"},
            )
        return JSONResponse(turn.message, headers=headers)
    except AnthropicError as exc:
        record.update(
            {
                "status": exc.status,
                "error_type": exc.type,
                "error_code": exc.code,
                "retry_after": exc.retry_after,
            }
        )
        return JSONResponse(exc.to_body(), status_code=exc.status, headers=exc.headers())
    except Exception as exc:  # never leak a stack trace to the client
        log.exception("unhandled error while serving /v1/messages")
        record.update({"status": 500, "error_type": "api_error"})
        error = AnthropicError(500, "api_error", f"gateway error: {exc}")
        return JSONResponse(error.to_body(), status_code=500)
    finally:
        record["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        record.update(_m365_fields_from_record(record))
        state.tap.record(record)


def _m365_fields_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten `x_m365` into the tap's documented `x_m365_*` columns (§9)."""
    fields = record.pop("x_m365", None) or {}
    return {key: value for key, value in fields.items() if key.startswith("x_m365_")}


async def count_tokens(request: Request) -> Response:
    """Estimate only (spec §7.5): chars/4 over system + tools + messages, no upstream call."""
    started = time.perf_counter()
    record: dict[str, Any] = {"path": "/v1/messages/count_tokens", "stream": False}
    try:
        try:
            body = await request.json()
        except ValueError as exc:
            raise AnthropicError(
                400, "invalid_request_error", f"request body is not JSON: {exc}"
            ) from exc
        try:
            parsed = CountTokensRequest.model_validate(body)
        except ValidationError as exc:
            raise AnthropicError(400, "invalid_request_error", _validation_message(exc)) from exc

        estimated = build_upstream_request(
            MessagesRequest(
                model=parsed.model,
                messages=parsed.messages,
                system=parsed.system,
                tools=parsed.tools,
            ),
            "count-tokens",
        )
        record.update(
            {
                "status": 200,
                "model_requested": parsed.model,
                "system_chars": estimated.system_chars,
                "tools_count": estimated.tools_count,
                "tools_chars": estimated.tools_chars,
                "messages_count": estimated.messages_count,
                "est_input_tokens": estimated.est_input_tokens,
            }
        )
        return JSONResponse({"input_tokens": estimated.est_input_tokens})
    except AnthropicError as exc:
        record.update({"status": exc.status, "error_type": exc.type, "error_code": exc.code})
        return JSONResponse(exc.to_body(), status_code=exc.status, headers=exc.headers())
    finally:
        record["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        request.app.state.tap.record(record)


async def models(request: Request) -> Response:
    """The Node proxy's model list, for debugging a mapping."""
    return JSONResponse(await request.app.state.upstream.list_models())


async def healthz(request: Request) -> Response:
    """Liveness stays 200; upstream reachability is in the body."""
    return JSONResponse({"status": "ok", "upstream": await request.app.state.upstream.healthy()})


async def not_found(request: Request) -> Response:
    """Log and tap unknown paths: they are how we learn what Claude Code calls."""
    path = request.url.path
    log.warning("unknown endpoint hit: %s %s", request.method, path)
    request.app.state.tap.record({"path": path, "method": request.method, "status": 404})
    error = AnthropicError(404, "invalid_request_error", f"unknown endpoint: {path}")
    return JSONResponse(error.to_body(), status_code=404)


_CATCH_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def register(app: FastAPI) -> None:
    app.add_api_route("/v1/messages", messages, methods=["POST"], name="messages")
    app.add_api_route(
        "/v1/messages/count_tokens", count_tokens, methods=["POST"], name="count_tokens"
    )
    app.add_api_route("/v1/models", models, methods=["GET"], name="models")
    app.add_api_route("/healthz", healthz, methods=["GET"], name="healthz")
    # Registered last so every specific route matches first.
    app.add_api_route("/{path:path}", not_found, methods=_CATCH_ALL_METHODS, name="not_found")
