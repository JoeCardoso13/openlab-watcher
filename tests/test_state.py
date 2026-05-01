"""State file read/write round-trip and missing-file behavior."""
import json
from pathlib import Path

from scripts import check


def test_read_state_returns_dict_with_last_seen_sha_and_last_run(tmp_path: Path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"last_seen_sha": "abc1234", "last_run": "2026-05-01T00:00:00Z"}))

    state = check.read_state(p)

    assert state["last_seen_sha"] == "abc1234"
    assert state["last_run"] == "2026-05-01T00:00:00Z"


def test_read_state_missing_file_returns_initial_state(tmp_path: Path):
    p = tmp_path / "state.json"
    assert not p.exists()

    state = check.read_state(p)

    assert state["last_seen_sha"] is None
    assert state["last_run"] is None


def test_write_state_persists_round_trip(tmp_path: Path):
    p = tmp_path / "state.json"

    check.write_state(p, "deadbee", "2026-05-01T01:23:45Z")

    payload = json.loads(p.read_text())
    assert payload["last_seen_sha"] == "deadbee"
    assert payload["last_run"] == "2026-05-01T01:23:45Z"


def test_write_state_overwrites_existing_file(tmp_path: Path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"last_seen_sha": "old", "last_run": "old-ts"}))

    check.write_state(p, "new", "new-ts")

    payload = json.loads(p.read_text())
    assert payload["last_seen_sha"] == "new"
    assert payload["last_run"] == "new-ts"
