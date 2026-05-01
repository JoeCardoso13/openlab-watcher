"""End-to-end orchestrator tests with all I/O mocked.

Pins the failure-mode contract:
- "Nothing to do" outcomes exit 0 and advance state when work was meaningfully judged.
- Real errors (GitHub / Anthropic / SMTP / malformed) exit non-zero and DO NOT advance state.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import check


# ---------- fixtures ---------- #


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"last_seen_sha": "olds123", "last_run": "2026-04-30T00:00:00Z"}))
    return p


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("SMTP_USER", "loancardoso1312@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-pw-test")
    monkeypatch.setenv("RECIPIENT_EMAIL", "david@example.com")
    monkeypatch.setenv("GH_TOKEN", "ghp_test")


def _two_commits():
    return [
        {"sha": "newsha1abc", "short_sha": "newsha1", "message": "Update tools.md", "url": "https://github.com/davidmalawey/openLab/commit/newsha1abc"},
        {"sha": "newsha2def", "short_sha": "newsha2", "message": "Update tour.md", "url": "https://github.com/davidmalawey/openLab/commit/newsha2def"},
    ]


def _diff_bundle(num_files=2, total_bytes=500):
    return {
        "diff": "diff --git a/tools.md b/tools.md\n+new line\n",
        "edited_files": {"tools.md": "# Tools\n+new line\n"},
        "sibling_lists": {},
        "stable_ctx": {
            "conventions": "Site is Docsify-rendered.",
            "sidebar": "- [Home](home.md)",
            "navbar": "",
            "tree": ["home.md", "tools.md"],
        },
        "num_files": num_files,
        "total_bytes": total_bytes,
    }


def _llm_with_issues():
    return {
        "has_issues": True,
        "summary": "Two small things to consider.",
        "findings": [
            {"severity": "nudge", "file": "tools.md", "message": "msg", "suggestion": "sug"},
        ],
    }


def _llm_clean():
    return {"has_issues": False, "summary": "All looks consistent.", "findings": []}


# ---------- scenarios ---------- #


def test_no_new_commits_exits_zero_and_skips_everything(monkeypatch, state_path):
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: [])
    fetch_diff = MagicMock()
    call_llm = MagicMock()
    send = MagicMock()
    monkeypatch.setattr(check, "fetch_diff_and_context", fetch_diff)
    monkeypatch.setattr(check, "call_llm", call_llm)
    monkeypatch.setattr(check, "send_email", send)

    rc = check.main(state_path=state_path)

    assert rc == 0
    fetch_diff.assert_not_called()
    call_llm.assert_not_called()
    send.assert_not_called()


def test_skip_rule_advances_state_without_calling_llm(monkeypatch, state_path):
    commits = _two_commits()
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: commits)
    monkeypatch.setattr(
        check, "fetch_diff_and_context", lambda *a, **kw: _diff_bundle(num_files=999, total_bytes=1)
    )
    call_llm = MagicMock()
    send = MagicMock()
    monkeypatch.setattr(check, "call_llm", call_llm)
    monkeypatch.setattr(check, "send_email", send)

    rc = check.main(state_path=state_path)

    assert rc == 0
    call_llm.assert_not_called()
    send.assert_not_called()
    new_state = json.loads(state_path.read_text())
    assert new_state["last_seen_sha"] == commits[-1]["sha"]


def test_llm_clean_advances_state_and_sends_no_email(monkeypatch, state_path):
    commits = _two_commits()
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: commits)
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _diff_bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: [{"role": "user", "content": []}])
    monkeypatch.setattr(check, "call_llm", lambda *a, **kw: object())
    monkeypatch.setattr(check, "parse_llm_response", lambda raw: _llm_clean())
    send = MagicMock()
    monkeypatch.setattr(check, "send_email", send)

    rc = check.main(state_path=state_path)

    assert rc == 0
    send.assert_not_called()
    new_state = json.loads(state_path.read_text())
    assert new_state["last_seen_sha"] == commits[-1]["sha"]


def test_llm_with_issues_sends_email_and_advances_state(monkeypatch, state_path):
    commits = _two_commits()
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: commits)
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _diff_bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: [{"role": "user", "content": []}])
    monkeypatch.setattr(check, "call_llm", lambda *a, **kw: object())
    monkeypatch.setattr(check, "parse_llm_response", lambda raw: _llm_with_issues())
    monkeypatch.setattr(
        check, "render_email", lambda *a, **kw: ("Subject: x", "Body: y")
    )
    send = MagicMock()
    monkeypatch.setattr(check, "send_email", send)

    rc = check.main(state_path=state_path)

    assert rc == 0
    send.assert_called_once()
    args, kwargs = send.call_args
    all_args = list(args) + list(kwargs.values())
    assert "david@example.com" in all_args
    new_state = json.loads(state_path.read_text())
    assert new_state["last_seen_sha"] == commits[-1]["sha"]


def test_github_error_exits_nonzero_and_state_unchanged(monkeypatch, state_path):
    def boom(*a, **kw):
        raise RuntimeError("github is down")

    monkeypatch.setattr(check, "fetch_new_commits", boom)
    send = MagicMock()
    monkeypatch.setattr(check, "send_email", send)

    rc = check.main(state_path=state_path)

    assert rc != 0
    send.assert_not_called()
    state = json.loads(state_path.read_text())
    assert state["last_seen_sha"] == "olds123"


def test_anthropic_error_exits_nonzero_and_state_unchanged(monkeypatch, state_path):
    commits = _two_commits()
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: commits)
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _diff_bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: [{"role": "user", "content": []}])

    def boom(*a, **kw):
        raise RuntimeError("anthropic 500")

    monkeypatch.setattr(check, "call_llm", boom)
    send = MagicMock()
    monkeypatch.setattr(check, "send_email", send)

    rc = check.main(state_path=state_path)

    assert rc != 0
    send.assert_not_called()
    state = json.loads(state_path.read_text())
    assert state["last_seen_sha"] == "olds123"


def test_smtp_error_exits_nonzero_and_state_unchanged(monkeypatch, state_path):
    commits = _two_commits()
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: commits)
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _diff_bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: [{"role": "user", "content": []}])
    monkeypatch.setattr(check, "call_llm", lambda *a, **kw: object())
    monkeypatch.setattr(check, "parse_llm_response", lambda raw: _llm_with_issues())
    monkeypatch.setattr(check, "render_email", lambda *a, **kw: ("s", "b"))

    def boom(*a, **kw):
        raise RuntimeError("smtp connection refused")

    monkeypatch.setattr(check, "send_email", boom)

    rc = check.main(state_path=state_path)

    assert rc != 0
    state = json.loads(state_path.read_text())
    assert state["last_seen_sha"] == "olds123"


def test_malformed_llm_response_exits_nonzero_and_state_unchanged(monkeypatch, state_path):
    commits = _two_commits()
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: commits)
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _diff_bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: [{"role": "user", "content": []}])
    monkeypatch.setattr(check, "call_llm", lambda *a, **kw: object())

    def boom(_raw):
        raise ValueError("model did not call the tool")

    monkeypatch.setattr(check, "parse_llm_response", boom)
    send = MagicMock()
    monkeypatch.setattr(check, "send_email", send)

    rc = check.main(state_path=state_path)

    assert rc != 0
    send.assert_not_called()
    state = json.loads(state_path.read_text())
    assert state["last_seen_sha"] == "olds123"
