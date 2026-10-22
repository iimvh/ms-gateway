"""Claude Code model IDs → Node proxy model IDs (spec §7.6).

The mapping is deliberately small: Claude Code always asks for a Claude model,
the proxy serves those families from M365, and anything the proxy already
advertises passes through so an operator can pin a tone by hand.
"""

from __future__ import annotations

import re
from collections.abc import Collection

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_CONTEXT_SUFFIX = "[1m]"

SONNET_UPSTREAM = "claude-sonnet"
HAIKU_UPSTREAM = "gpt-5.5"


def normalize_model_id(model: str) -> str:
    """Lowercase, drop a `[1m]` context marker and a trailing `-YYYYMMDD` date."""
    normalized = model.strip().lower()
    if normalized.endswith(_CONTEXT_SUFFIX):
        normalized = normalized[: -len(_CONTEXT_SUFFIX)]
    return _DATE_SUFFIX.sub("", normalized)


def map_model(
    requested: str,
    *,
    opus_model: str,
    default_model: str,
    force_model: str = "",
    known: Collection[str] = (),
) -> str:
    """Resolve the upstream model for a Claude Code model ID, in spec order."""
    if force_model:
        return force_model

    normalized = normalize_model_id(requested)
    if "opus" in normalized:
        return opus_model
    if "sonnet" in normalized:
        return SONNET_UPSTREAM
    if "haiku" in normalized:
        return HAIKU_UPSTREAM
    if normalized in {normalize_model_id(name) for name in known}:
        return normalized
    return default_model
