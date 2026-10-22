import json

import httpx
import pytest
import respx

from gateway.config import Settings
from gateway.errors import AnthropicError
from gateway.upstream import UpstreamClient, quota_headers

SETTINGS = Settings(upstream_url="http://up/v1")


@respx.mock
async def test_chat_completion_posts_non_streaming_and_captures_status():
    route = respx.post("http://up/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "c",
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            },
        )
    )
    async with httpx.AsyncClient() as http:
        client = UpstreamClient(SETTINGS, client=http)
        body = await client.chat_completion({"model": "m", "messages": [], "stream": True})
    sent = json.loads(route.calls.last.request.content)
    assert sent["stream"] is False
    assert body["choices"][0]["message"]["content"] == "hi"
    assert client.in_flight == 0


@respx.mock
async def test_disengage_status_reaches_the_gateway_as_a_400():
    respx.post("http://up/v1/chat/completions").mock(
        return_value=httpx.Response(502, json={"error": {"type": "disengaged", "message": "no"}})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(AnthropicError) as exc:
            await UpstreamClient(SETTINGS, client=http).chat_completion({"messages": []})
    assert exc.value.status == 400 and exc.value.code == "m365_disengaged"


@respx.mock
async def test_retry_after_header_is_carried():
    respx.post("http://up/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json={"error": {"code": "m365_throttle_hold", "message": "hold"}},
            headers={"Retry-After": "30"},
        )
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(AnthropicError) as exc:
            await UpstreamClient(SETTINGS, client=http).chat_completion({"messages": []})
    assert (exc.value.status, exc.value.retry_after) == (429, 30)


@respx.mock
async def test_transport_failure_is_a_502_api_error():
    respx.post("http://up/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(AnthropicError) as exc:
            await UpstreamClient(SETTINGS, client=http).chat_completion({"messages": []})
    assert (exc.value.status, exc.value.type) == (502, "api_error")


@respx.mock
async def test_non_json_200_body_is_an_api_error_not_a_crash():
    respx.post("http://up/v1/chat/completions").mock(
        return_value=httpx.Response(200, text="<html>proxy</html>")
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(AnthropicError) as exc:
            await UpstreamClient(SETTINGS, client=http).chat_completion({"messages": []})
    assert exc.value.status == 502


@respx.mock
async def test_list_models_and_health():
    respx.get("http://up/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "claude-sonnet"}]})
    )
    async with httpx.AsyncClient() as http:
        client = UpstreamClient(SETTINGS, client=http)
        assert (await client.list_models())["data"][0]["id"] == "claude-sonnet"
        assert await client.healthy() is True


@respx.mock
async def test_health_is_false_when_upstream_is_down():
    respx.get("http://up/v1/models").mock(side_effect=httpx.ConnectError("refused"))
    async with httpx.AsyncClient() as http:
        assert await UpstreamClient(SETTINGS, client=http).healthy() is False


def test_quota_headers_rename_upstream_usage_keys():
    headers = quota_headers(
        {
            "usage": {
                "prompt_tokens": 0,
                "x_m365_conversation_pct": 97,
                "x_m365_dea_score": 0.001,
                "x_m365_metering": {"ClaudeOpusQueryDaily": 3},
            }
        },
        {},
    )
    assert headers["x-m365-context-pct"] == "97"
    assert headers["x-m365-dea-score"] == "0.001"
    assert json.loads(headers["x-m365-metering"])["ClaudeOpusQueryDaily"] == 3
    assert headers["x-m365-conversation-pct"] == "97"
    assert "x-m365-prompt-tokens" not in headers


def test_quota_headers_also_carry_response_headers():
    headers = quota_headers({"usage": {}}, {"X-M365-Dea-Score": "0.5"})
    assert headers["x-m365-dea-score"] == "0.5"
