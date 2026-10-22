from gateway.models_map import map_model, normalize_model_id

KNOWN = {"claude-sonnet", "claude-opus", "gpt-5.5", "gpt-5.5-think-deeper"}


def m(requested, **kw):
    kw.setdefault("opus_model", "claude-opus")
    kw.setdefault("default_model", "claude-sonnet")
    kw.setdefault("known", KNOWN)
    return map_model(requested, **kw)


def test_normalize():
    assert normalize_model_id("claude-sonnet-4-5-20250929[1m]") == "claude-sonnet-4-5"
    assert normalize_model_id("Claude-Opus-4.8") == "claude-opus-4.8"


def test_families():
    assert m("claude-sonnet-4-5") == "claude-sonnet"
    assert m("claude-3-5-haiku-20241022") == "gpt-5.5"
    assert m("claude-opus-4-8") == "claude-opus"
    assert m("claude-opus-4-8", opus_model="claude-opus-4.8") == "claude-opus-4.8"


def test_known_passthrough_then_default():
    assert m("gpt-5.5-think-deeper") == "gpt-5.5-think-deeper"
    assert m("something-else") == "claude-sonnet"
    assert m("something-else", default_model="gpt-5.5") == "gpt-5.5"


def test_force_overrides_everything():
    assert m("claude-opus-4-8", force_model="claude-sonnet") == "claude-sonnet"


def test_opus_beats_known_passthrough():
    # a raw upstream ID that Claude Code happens to know must still be family-routed
    assert m("claude-opus", known={"claude-opus"}) == "claude-opus"
