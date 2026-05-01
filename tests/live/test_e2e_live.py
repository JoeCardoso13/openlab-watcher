"""Live end-to-end smoke test. Enable with RUN_LIVE_TESTS=1."""
import json
import os

import pytest

from scripts import check


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to run live smoke tests",
)


def test_live_main_advances_state(tmp_path, monkeypatch):
    required = ["ANTHROPIC_API_KEY", "SMTP_USER", "SMTP_PASSWORD", "LIVE_RECIPIENT"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.fail(f"missing live env vars: {', '.join(missing)}")

    monkeypatch.setenv("RECIPIENT_EMAIL", os.environ["LIVE_RECIPIENT"])

    token = os.environ.get("GH_TOKEN")
    commits = check.fetch_new_commits("davidmalawey/openLab", "main", since_sha=None, gh_token=token)
    assert len(commits) >= 2

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"last_seen_sha": commits[-2]["sha"], "last_run": None}))

    rc = check.main(state_path=state_path)

    assert rc == 0
    state = json.loads(state_path.read_text())
    assert state["last_seen_sha"] == commits[-1]["sha"]
    assert state["last_run"]
