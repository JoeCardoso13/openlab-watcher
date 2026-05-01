"""Operational logging for GitHub Actions diagnosis."""
import json
from pathlib import Path

import pytest

from scripts import check


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
    monkeypatch.setenv("RECIPIENT_EMAIL", "david.person@example.com")
    monkeypatch.setenv("GH_TOKEN", "ghp_test")


def _commits():
    return [
        {"sha": "newsha1abc", "short_sha": "newsha1", "message": "Update tools.md", "url": "https://example.test/1"},
        {"sha": "newsha2def", "short_sha": "newsha2", "message": "Update tour.md", "url": "https://example.test/2"},
    ]


def _diff_bundle():
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
        "num_files": 2,
        "total_bytes": 500,
    }


def test_main_logs_clean_llm_decision_without_email(monkeypatch, state_path, capsys):
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: _commits())
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _diff_bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: [{"role": "user", "content": []}])
    monkeypatch.setattr(check, "call_llm", lambda *a, **kw: object())
    monkeypatch.setattr(
        check,
        "parse_llm_response",
        lambda raw: {"has_issues": False, "summary": "All consistent.", "findings": []},
    )

    assert check.main(state_path=state_path) == 0

    output = capsys.readouterr().out
    assert "commits_found=2" in output
    assert "llm_result has_issues=False findings=0" in output
    assert "email_send_start" not in output
    assert "state_advanced sha=newsha2def" in output


def test_main_logs_masked_email_when_email_is_sent(monkeypatch, state_path, capsys):
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: _commits())
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _diff_bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: [{"role": "user", "content": []}])
    monkeypatch.setattr(check, "call_llm", lambda *a, **kw: object())
    monkeypatch.setattr(
        check,
        "parse_llm_response",
        lambda raw: {
            "has_issues": True,
            "summary": "One issue.",
            "findings": [{"severity": "nudge", "file": "tools.md", "message": "msg", "suggestion": "sug"}],
        },
    )
    monkeypatch.setattr(check, "render_email", lambda *a, **kw: ("Subject", "Body"))
    monkeypatch.setattr(check, "send_email", lambda *a, **kw: None)

    assert check.main(state_path=state_path) == 0

    output = capsys.readouterr().out
    assert "llm_result has_issues=True findings=1" in output
    assert "email_send_start to=d***@example.com findings=1" in output
    assert "email_send_success to=d***@example.com" in output
    assert "david.person@example.com" not in output
