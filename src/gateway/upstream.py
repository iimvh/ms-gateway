"""The one upstream call the gateway makes: non-streaming chat completions.

Streaming is deliberately not used: the Node proxy commits HTTP 200 the instant
it opens its SSE stream, so every failure arrives in-band with no status. The
non-streaming path is the only one that yields real 400/429/502 responses, which
is what the error mapping and Claude Code's retry logic need (spec §7.1).
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

import httpx

from .config import Settings
from .errors import AnthropicError, map_upstream_error
from .log import get_logger

log = get_logger("upstream")

# Reachability/model-list probes get this ceiling, whatever the turn timeout is.
PROBE_TIMEOUT_S = 15.0


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


def quota_headers(body: Any, headers: Mapping[str, str] | None = None) -> dict[str, str]:
    """Surface M365's real quota/classifier numbers as `x-m365-*` headers.

    The upstream puts them in the response body's `usage` block, not in HTTP
    headers, so this renames them; a header of the same name from upstream wins.
    """
    out: dict[str, str] = {}
    usage = body.get("usage") if isinstance(body, dict) else None
    if isinstance(usage, dict):
        for key, value in usage.items():
            if not isinstance(key, str) or not key.startswith("x_m365_"):
                continue
            out["x-m365-" + key[len("x_m365_") :].replace("_", "-")] = _stringify(value)
        if "x_m365_conversation_pct" in usage:
            out["x-m365-context-pct"] = _stringify(usage["x_m365_conversation_pct"])
    for name, value in (headers or {}).items():
        if name.lower().startswith("x-m365-"):
            out[name.lower()] = str(value)
    return out


class UpstreamClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.upstream_timeout_s)
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        url = f"{self._settings.upstream_url}{path}"
        self._in_flight += 1
        try:
            return await self._http().post(url, json=payload)
        except httpx.HTTPError as exc:
            raise AnthropicError(502, "api_error", f"upstream request failed: {exc}") from exc
        finally:
            self._in_flight -= 1

    async def _get(self, path: str) -> httpx.Response:
        url = f"{self._settings.upstream_url}{path}"
        # Model-list and reachability probes must fail fast: a wedged (not refused)
        # proxy would otherwise hang `/healthz` for the 600 s turn timeout.
        timeout = min(PROBE_TIMEOUT_S, self._settings.upstream_timeout_s)
        try:
            return await self._http().get(url, timeout=timeout)
        except httpx.HTTPError as exc:
            raise AnthropicError(502, "api_error", f"upstream request failed: {exc}") from exc

    @staticmethod
    def _decode(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text

    def _raise_for_status(self, response: httpx.Response, body: Any) -> None:
        if response.status_code != 200:
            raise map_upstream_error(
                response.status_code, body, response.headers.get("retry-after")
            )

    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = {**payload, "stream": False}  # spec §7.1: always non-streaming
        started = time.perf_counter()
        response = await self._post("/chat/completions", body)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        decoded = self._decode(response)
        self._raise_for_status(response, decoded)
        if not isinstance(decoded, dict):
            raise AnthropicError(
                502, "api_error", "the M365 proxy returned a non-JSON completion body"
            )
        log.debug("upstream completion in %d ms", elapsed_ms)
        return decoded

    async def list_models(self) -> dict[str, Any]:
        response = await self._get("/models")
        decoded = self._decode(response)
        self._raise_for_status(response, decoded)
        if not isinstance(decoded, dict):
            raise AnthropicError(502, "api_error", "the M365 proxy returned a non-JSON model list")
        return decoded

    async def healthy(self) -> bool:
        try:
            response = await self._get("/models")
        except AnthropicError:
            return False
        return response.status_code == 200
