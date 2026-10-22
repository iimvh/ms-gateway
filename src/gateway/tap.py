"""Append-only JSONL measurement tap (spec §9).

One line per request, one file per day. A tap failure must never fail a request:
every I/O error is logged and swallowed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .log import get_logger

log = get_logger("tap")


class Tap:
    def __init__(self, settings: Settings) -> None:
        self._dir = Path(settings.tap_dir)
        self._off = settings.tap_off

    def record(self, fields: dict[str, Any]) -> None:
        if self._off:
            return
        now = datetime.now().astimezone()
        entry: dict[str, Any] = {
            "ts": now.isoformat(timespec="milliseconds"),
            "req_id": f"req_{uuid.uuid4().hex[:16]}",
            **fields,
        }
        path = self._dir / f"{now.date().isoformat()}.jsonl"
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
        except OSError as exc:  # the tap is diagnostics, never a dependency
            log.warning("could not write the tap at %s: %s", path, exc)
