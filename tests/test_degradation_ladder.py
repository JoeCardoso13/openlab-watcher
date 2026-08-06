"""Degradation-ladder contract (designed 2026-07-23 after Actions run 30022819116).

Rung 0: valid full payload — behavior unchanged.
Rung 1: flat max_tokens=8192, one retry on stop_reason=="max_tokens" (tested in test_llm_call.py).
Rung 2: incomplete payload — salvage, email what we have + cut-short note + compare link + contact note.
Rung 3: nothing usable — minimal factual email (commit count + compare link + contact note).
Any rung reached after the LLM stage: state advances, exit 0. Pre-LLM failures stay loud.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import check


CONTACT_NOTE = (
    "If this problem persists, and/or is bothering you, please contact "
    "Joe Cardoso, the master of *agent* puppets, he'll be happy to help."
)

SAMPLE_COMMITS = [
    {
        "sha": "newsha1abc",
        "short_sha": "newsha1",
        "message": "Update tools.md",
        "url": "https://github.com/davidmalawey/openLab/commit/newsha1abc",
    },
    {
        "sha": "newsha2def",
        "short_sha": "newsha2",
        "message": "Update tour.md",
        "url": "https://github.com/davidmalawey/openLab/commit/newsha2def",
    },
]

SAMPLE_FINDING = {
    "severity": "nudge",
    "file": "tools.md",
    "message": "tools.md keeps growing.",
    "suggestion": "Split it.",
}

COMPARE_URL = "https://github.com/davidmalawey/openLab/compare/newsha1abc~1...newsha2def"


# ---------- rendering: degraded (Rung 2) ---------- #


def _render_degraded():
    return check.render_email(
        [SAMPLE_FINDING],
        "One salvaged note.",
        SAMPLE_COMMITS,
        "davidmalawey/openLab",
        4,
        complete=False,
        compare_url=COMPARE_URL,
    )


def test_degraded_email_keeps_subject_serial():
    subject, _ = _render_degraded()

    assert "openLab Watcher" in subject
    assert "#4" in subject


def test_degraded_email_includes_cut_short_note():
    _, body = _render_degraded()

    assert "cut short" in body.lower()
    assert "may be missing" in body.lower()


def test_degraded_email_includes_compare_link_and_contact_note():
    _, body = _render_degraded()

    assert COMPARE_URL in body
    assert CONTACT_NOTE in body


def test_degraded_email_contact_note_comes_after_compare_link():
    _, body = _render_degraded()

    assert body.index(COMPARE_URL) < body.index(CONTACT_NOTE)


def test_degraded_email_still_carries_salvaged_content():
    _, body = _render_degraded()

    assert "One salvaged note." in body
    assert SAMPLE_FINDING["message"] in body


def test_complete_email_has_no_degradation_notes():
    """A complete email carries the compare link too, but none of the apology."""
    _, body = check.render_email(
        [SAMPLE_FINDING],
        "All good.",
        SAMPLE_COMMITS,
        "davidmalawey/openLab",
        4,
        compare_url=COMPARE_URL,
    )

    assert COMPARE_URL in body
    assert "cut short" not in body.lower()
    assert CONTACT_NOTE not in body


# ---------- rendering: fallback (Rung 3) ---------- #


def test_fallback_email_subject_carries_serial():
    subject, _ = check.render_fallback_email(24, COMPARE_URL, 4)

    assert "openLab Watcher" in subject
    assert "#4" in subject


def test_fallback_email_body_has_count_link_and_contact_note():
    _, body = check.render_fallback_email(24, COMPARE_URL, 4)

    assert "24" in body
    assert "commit" in body.lower()
    assert COMPARE_URL in body
    assert CONTACT_NOTE in body
    assert body.index(COMPARE_URL) < body.index(CONTACT_NOTE)


# ---------- run loop rungs ---------- #


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("SMTP_USER", "loancardoso1312@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-pw-test")
    monkeypatch.setenv("RECIPIENT_EMAIL", "david@example.com")
    monkeypatch.setenv("GH_TOKEN", "ghp_test")


def _commits():
    return [
        {"sha": "newsha1abc", "short_sha": "newsha1", "message": "Update tools.md", "url": "u1"},
        {"sha": "newsha2def", "short_sha": "newsha2", "message": "Update tour.md", "url": "u2"},
    ]


def _bundle():
    return {
        "diff": "diff --git a/tools.md b/tools.md\n+new line\n",
        "edited_files": {"tools.md": "# Tools\n"},
        "sibling_lists": {},
        "stable_ctx": {"conventions": "c", "sidebar": "", "navbar": "", "tree": []},
        "num_files": 2,
        "total_bytes": 500,
    }


def _fake_raw():
    return SimpleNamespace(
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )


def _wire(monkeypatch, parsed=None, parse_raises=None):
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: _commits())
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: [{"role": "user", "content": []}])
    monkeypatch.setattr(check, "call_llm", lambda *a, **kw: _fake_raw())
    if parse_raises is not None:
        def boom(_raw):
            raise parse_raises
        monkeypatch.setattr(check, "parse_llm_response", boom)
    else:
        monkeypatch.setattr(check, "parse_llm_response", lambda raw: parsed)

    sent = {}

    def fake_send(host, port, user, password, to_addr, subject, body):
        sent["to"] = to_addr
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr(check, "send_email", fake_send)
    return sent


def _state_file(tmp_path, email_count=3):
    p = tmp_path / "state.json"
    p.write_text(
        json.dumps({"last_seen_sha": "olds123", "last_run": "ts", "email_count": email_count})
    )
    return p


def test_rung2_incomplete_payload_sends_degraded_email(monkeypatch, tmp_path):
    p = _state_file(tmp_path)
    parsed = {"has_issues": True, "summary": "Salvaged.", "findings": [SAMPLE_FINDING], "complete": False}
    sent = _wire(monkeypatch, parsed=parsed)

    rc = check.main(state_path=p)

    assert rc == 0
    assert "body" in sent
    assert "cut short" in sent["body"].lower()
    assert COMPARE_URL in sent["body"]
    assert CONTACT_NOTE in sent["body"]
    assert "#4" in sent["subject"]
    state = json.loads(p.read_text())
    assert state["last_seen_sha"] == "newsha2def"
    assert state["email_count"] == 4


def test_rung2_emails_even_when_salvaged_has_issues_false(monkeypatch, tmp_path):
    """A truncated 'no issues' cannot be trusted — incomplete always emails."""
    p = _state_file(tmp_path)
    parsed = {"has_issues": False, "summary": "", "findings": [], "complete": False}
    sent = _wire(monkeypatch, parsed=parsed)

    rc = check.main(state_path=p)

    assert rc == 0
    assert "body" in sent
    assert COMPARE_URL in sent["body"]
    assert json.loads(p.read_text())["email_count"] == 4


def test_rung3_unusable_response_sends_fallback_email(monkeypatch, tmp_path):
    p = _state_file(tmp_path)
    sent = _wire(monkeypatch, parse_raises=check.MalformedLLMResponse("no tool call"))

    rc = check.main(state_path=p)

    assert rc == 0
    assert "body" in sent
    assert "2" in sent["body"]
    assert COMPARE_URL in sent["body"]
    assert CONTACT_NOTE in sent["body"]
    assert "#4" in sent["subject"]
    state = json.loads(p.read_text())
    assert state["last_seen_sha"] == "newsha2def"
    assert state["email_count"] == 4


def test_rung0_clean_run_still_sends_nothing(monkeypatch, tmp_path):
    p = _state_file(tmp_path)
    parsed = {"has_issues": False, "summary": "Fine.", "findings": [], "complete": True}
    sent = _wire(monkeypatch, parsed=parsed)

    rc = check.main(state_path=p)

    assert rc == 0
    assert sent == {}
    assert json.loads(p.read_text())["email_count"] == 3


def test_anthropic_transport_error_stays_loud(monkeypatch, tmp_path):
    """API/network failures are not a rung — no response means nothing to
    salvage; fail loud, keep state, let next run retry."""
    p = _state_file(tmp_path)
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: _commits())
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: [{"role": "user", "content": []}])

    def boom(*a, **kw):
        raise RuntimeError("anthropic 500")

    monkeypatch.setattr(check, "call_llm", boom)
    send = MagicMock()
    monkeypatch.setattr(check, "send_email", send)

    rc = check.main(state_path=p)

    assert rc != 0
    send.assert_not_called()
    assert json.loads(p.read_text())["last_seen_sha"] == "olds123"


# ---------- observability ---------- #


def test_run_logs_stop_reason_and_token_usage(monkeypatch, tmp_path, capsys):
    p = _state_file(tmp_path)
    parsed = {"has_issues": False, "summary": "Fine.", "findings": [], "complete": True}
    _wire(monkeypatch, parsed=parsed)

    check.main(state_path=p)

    out = capsys.readouterr().out
    assert "stop_reason=tool_use" in out
    assert "input_tokens=1000" in out
    assert "output_tokens=200" in out


def test_run_logs_rung_0_on_normal_run(monkeypatch, tmp_path, capsys):
    p = _state_file(tmp_path)
    parsed = {"has_issues": False, "summary": "Fine.", "findings": [], "complete": True}
    _wire(monkeypatch, parsed=parsed)

    check.main(state_path=p)

    assert "rung=0" in capsys.readouterr().out


def test_run_logs_rung_2_on_salvage(monkeypatch, tmp_path, capsys):
    p = _state_file(tmp_path)
    parsed = {"has_issues": True, "summary": "s", "findings": [], "complete": False}
    _wire(monkeypatch, parsed=parsed)

    check.main(state_path=p)

    assert "rung=2" in capsys.readouterr().out


def test_run_logs_rung_3_on_fallback(monkeypatch, tmp_path, capsys):
    p = _state_file(tmp_path)
    _wire(monkeypatch, parse_raises=check.MalformedLLMResponse("no tool call"))

    check.main(state_path=p)

    assert "rung=3" in capsys.readouterr().out
