from gateway.errors import conversation_exhausted_error, map_upstream_error


def err(status, body=None, retry=None):
    return map_upstream_error(status, body, retry)


def test_throttle_codes_are_429_and_carry_retry_after():
    for code in ("m365_throttle_hold", "m365_turn_budget", "priority_access_exhausted"):
        e = err(429, {"error": {"message": "m", "type": "rate_limit_error", "code": code}}, "42")
        assert (e.status, e.type, e.retry_after, e.code) == (429, "rate_limit_error", 42, code)


def test_disengage_is_a_non_retryable_400():
    e = err(502, {"error": {"message": "M365 Copilot disengaged", "type": "disengaged"}})
    assert (e.status, e.type, e.code) == (400, "invalid_request_error", "m365_disengaged")
    assert "disengaged" in e.message.lower()
    assert "M365 Copilot disengaged" in e.message


def test_empty_response_is_503_and_model_route_is_404():
    empty = err(502, {"error": {"type": "upstream_empty_response", "message": "empty"}})
    assert (empty.status, empty.type) == (503, "overloaded_error")
    route = err(502, {"error": {"type": "upstream_error", "code": "model_route_unavailable"}})
    assert (route.status, route.type, route.code) == (404, "not_found_error", "model_route_unavailable")


def test_other_5xx_is_502_api_error_and_400_passes_through():
    assert err(500, {"error": {"message": "boom"}}).status == 502
    assert err(500, {"error": {"message": "boom"}}).type == "api_error"
    e400 = err(400, {"error": {"message": "bad field"}})
    assert (e400.status, e400.type, e400.message) == (400, "invalid_request_error", "bad field")


def test_non_json_body_still_yields_a_message():
    e = err(502, "<html>bad gateway</html>", None)
    assert e.status == 502 and "bad gateway" not in e.message and e.message


def test_retry_after_garbage_is_ignored():
    assert err(429, {"error": {"code": "m365_throttled"}}, "soon").retry_after is None
    assert err(429, {"error": {"code": "m365_throttled"}}, " 7 ").retry_after == 7


def test_body_follows_the_anthropic_envelope():
    body = conversation_exhausted_error(97).to_body()
    assert body["type"] == "error" and body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "m365_conversation_exhausted"
    assert "compact" in body["error"]["message"] and "97" in body["error"]["message"]


def test_other_4xx_keeps_its_status_but_reads_as_a_request_error():
    e = err(403, {"error": {"message": "denied"}})
    assert (e.status, e.type, e.message) == (403, "invalid_request_error", "denied")
