"""Gateway configuration from the environment (spec §6).

Every setting is optional; the defaults target the localhost Node proxy.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_UPSTREAM_URL = "http://127.0.0.1:4141/v1"
DEFAULT_PORT = 4142
DEFAULT_MODEL = "claude-sonnet"
DEFAULT_TAP_DIR = "tap"
DEFAULT_TIMEOUT_S = 600.0
DEFAULT_OPUS_MODEL = "claude-opus"

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Resolved gateway settings. Never re-read the environment outside `from_env`."""

    upstream_url: str = DEFAULT_UPSTREAM_URL
    port: int = DEFAULT_PORT
    default_model: str = DEFAULT_MODEL
    tap_dir: Path = field(default_factory=lambda: Path(DEFAULT_TAP_DIR))
    tap_off: bool = False
    upstream_timeout_s: float = DEFAULT_TIMEOUT_S
    force_model: str = ""
    opus_model: str = DEFAULT_OPUS_MODEL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        raw = os.environ if env is None else env

        port_text = raw.get("PORT", str(DEFAULT_PORT)).strip()
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"PORT must be an integer: {port_text!r}") from exc

        timeout_text = raw.get("UPSTREAM_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)).strip()
        try:
            timeout = float(timeout_text)
        except ValueError as exc:
            raise ValueError(f"UPSTREAM_TIMEOUT_S must be a number: {timeout_text!r}") from exc

        return cls(
            upstream_url=(raw.get("UPSTREAM_URL") or DEFAULT_UPSTREAM_URL).strip().rstrip("/"),
            port=port,
            default_model=(raw.get("DEFAULT_MODEL") or DEFAULT_MODEL).strip(),
            tap_dir=Path((raw.get("TAP_DIR") or DEFAULT_TAP_DIR).strip()),
            tap_off=(raw.get("TAP_OFF") or "").strip().lower() in _TRUTHY,
            upstream_timeout_s=timeout,
            force_model=(raw.get("FORCE_MODEL") or "").strip(),
            opus_model=(raw.get("M365_GATEWAY_OPUS_MODEL") or DEFAULT_OPUS_MODEL).strip(),
        )
