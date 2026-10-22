from pathlib import Path

import pytest

from gateway.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert (s.upstream_url, s.port, s.default_model) == (
        "http://127.0.0.1:4141/v1",
        4142,
        "claude-sonnet",
    )
    assert s.tap_dir == Path("tap") and s.tap_off is False
    assert s.upstream_timeout_s == 600.0 and s.force_model == "" and s.opus_model == "claude-opus"


def test_env_overrides():
    s = Settings.from_env(
        {
            "UPSTREAM_URL": "http://x/v1",
            "PORT": "5000",
            "DEFAULT_MODEL": "gpt-5.5",
            "TAP_DIR": "t",
            "UPSTREAM_TIMEOUT_S": "12.5",
            "FORCE_MODEL": "claude-sonnet",
            "M365_GATEWAY_OPUS_MODEL": "claude-opus-4.8",
        }
    )
    assert (s.upstream_url, s.port, s.default_model, s.tap_dir) == (
        "http://x/v1",
        5000,
        "gpt-5.5",
        Path("t"),
    )
    assert s.upstream_timeout_s == 12.5 and s.force_model == "claude-sonnet"
    assert s.opus_model == "claude-opus-4.8"


def test_tap_off_truthiness():
    assert Settings.from_env({"TAP_OFF": "1"}).tap_off is True
    assert Settings.from_env({"TAP_OFF": "true"}).tap_off is True
    assert Settings.from_env({"TAP_OFF": "0"}).tap_off is False
    assert Settings.from_env({"TAP_OFF": ""}).tap_off is False


def test_bad_port_fails_loudly():
    with pytest.raises(ValueError, match="PORT"):
        Settings.from_env({"PORT": "not-a-port"})
