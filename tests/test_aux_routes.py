import httpx

from conftest import MODELS, UPSTREAM, tap_lines


def test_count_tokens_estimates_input_tokens(client):
    response = client.post(
        "/v1/messages/count_tokens",
        json={"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "x" * 40}]},
    )
    assert response.status_code == 200
    assert response.json() == {"input_tokens": 10}


def test_count_tokens_includes_system_and_tools(client):
    body = {
        "model": "m",
        "system": "y" * 40,
        "messages": [{"role": "user", "content": ""}],
        "tools": [
            {
                "name": "T",
                "description": "z" * 40,
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    }
    assert client.post("/v1/messages/count_tokens", json=body).json()["input_tokens"] > 20


def test_count_tokens_never_calls_upstream(client, upstream):
    client.post(
        "/v1/messages/count_tokens",
        json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert not [
        call for call in upstream.calls if call.request.url.path.endswith("/chat/completions")
    ]


def test_models_endpoint_proxies_upstream(client):
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == MODELS["data"][0]["id"]


def test_models_endpoint_reports_an_unreachable_upstream(client, upstream):
    upstream.get(f"{UPSTREAM}/models").mock(side_effect=httpx.ConnectError("refused"))
    response = client.get("/v1/models")
    assert response.status_code == 502
    assert response.json()["error"]["type"] == "api_error"


def test_healthz_reports_upstream_reachability(client):
    assert client.get("/healthz").json() == {"status": "ok", "upstream": True}


def test_healthz_stays_200_when_upstream_is_down(client, upstream):
    upstream.get(f"{UPSTREAM}/models").mock(side_effect=httpx.ConnectError("refused"))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "upstream": False}


def test_unknown_path_is_404_and_tapped(client, settings):
    response = client.post("/v1/some-future-endpoint", json={"a": 1})
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "invalid_request_error"
    lines = tap_lines(settings.tap_dir)
    assert any(line.get("path") == "/v1/some-future-endpoint" for line in lines)


def test_unknown_get_path_is_404_too(client):
    assert client.get("/v1/unknown").status_code == 404
