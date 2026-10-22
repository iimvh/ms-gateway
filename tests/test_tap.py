import json
from datetime import date

from gateway.config import Settings
from gateway.tap import Tap


def tap_path(tmp_path):
    return tmp_path / f"{date.today().isoformat()}.jsonl"


def test_appends_one_json_line_per_request(tmp_path):
    tap = Tap(Settings(tap_dir=tmp_path))
    tap.record({"status": 200, "model_requested": "claude-sonnet-4-5"})
    tap.record({"status": 429})
    lines = [json.loads(line) for line in tap_path(tmp_path).read_text(encoding="utf-8").splitlines()]
    assert [line["status"] for line in lines] == [200, 429]
    assert all(line["req_id"].startswith("req_") and "ts" in line for line in lines)
    assert lines[0]["model_requested"] == "claude-sonnet-4-5"


def test_tap_off_writes_nothing(tmp_path):
    Tap(Settings(tap_dir=tmp_path, tap_off=True)).record({"status": 200})
    assert not tap_path(tmp_path).exists()


def test_unserialisable_value_never_breaks_a_turn(tmp_path):
    tap = Tap(Settings(tap_dir=tmp_path))
    tap.record({"status": 200, "weird": object()})
    line = json.loads(tap_path(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert line["status"] == 200 and "object" in line["weird"]


def test_a_missing_directory_is_created_on_first_write(tmp_path):
    tap_dir = tmp_path / "deep" / "tap"
    Tap(Settings(tap_dir=tap_dir)).record({"status": 200})
    assert (tap_dir / f"{date.today().isoformat()}.jsonl").exists()


def test_created_at_is_rfc3339_with_an_offset(tmp_path):
    Tap(Settings(tap_dir=tmp_path)).record({"status": 200})
    line = json.loads(tap_path(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert line["ts"].endswith("+00:00") or "T" in line["ts"] and (
        "+" in line["ts"][10:] or line["ts"].endswith("Z")
    )
