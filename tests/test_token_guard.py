"""The single cost guardrail: local token estimate vs MAX_INPUT_TOKENS.

Replaces the old skip rule (file/byte proxies), which silently swallowed
batches — designed 2026-08-06 after run 31126880968 skipped a 6-commit,
25-file week without telling anyone. Contract:

- Estimate = total characters of all prompt text blocks, divided by a
  deliberately conservative chars-per-token ratio. Local, free, computed
  after build_prompt; no extra API call.
- Over budget: no paid LLM call; David gets the existing fallback email
  (count + compare link + contact note); serial consumed; state advances;
  exit 0. Zero tokens billed.
- At or under budget: proceed to the LLM as normal.
- SMTP failure while delivering the guard email stays loud (state untouched).
"""
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import check


# Calibration fixture: 4,000 characters of real openLab markdown (tools.md),
# measured once against the live count_tokens endpoint on claude-sonnet-4-6.
# A wallet guard must never read LOW, so the estimator has to cover this.
SAMPLE_PATH = Path(__file__).parent / "fixtures_openlab_sample.txt"
SAMPLE_REAL_TOKENS = 1302


BUDGET = 200_000
COMPARE_URL = "https://github.com/davidmalawey/openLab/compare/newsha1abc~1...newsha2def"
CONTACT_NOTE = (
    "If this problem persists, and/or is bothering you, please contact "
    "Joe Cardoso, the master of *agent* puppets, he'll be happy to help."
)


# ---------- the old proxies are purged ---------- #


def test_skip_rule_is_gone():
    assert not hasattr(check, "should_skip")
    assert not hasattr(check, "SKIP_FILE_THRESHOLD")
    assert not hasattr(check, "SKIP_BYTE_THRESHOLD")


def test_budget_constant_is_200k():
    assert getattr(check, "MAX_INPUT_TOKENS", None) == BUDGET


def test_estimator_never_reads_low_on_real_openlab_content():
    """Regression: chars//4 underestimated real tokens by ~25% (measured
    2026-08-06 against a live 25-commit batch: 39,024 estimated vs 48,796
    actual). A guard that reads low lets through more spend than its constant
    promises, so the estimator must bias conservative on real content.
    """
    sample = SAMPLE_PATH.read_text()
    messages = [{"role": "user", "content": [{"type": "text", "text": sample}]}]

    assert check.estimate_prompt_tokens(messages) >= SAMPLE_REAL_TOKENS


def test_estimator_stays_within_a_sane_margin():
    """Conservative, but not so paranoid it trips on ordinary weeks."""
    sample = SAMPLE_PATH.read_text()
    messages = [{"role": "user", "content": [{"type": "text", "text": sample}]}]

    assert check.estimate_prompt_tokens(messages) <= SAMPLE_REAL_TOKENS * 1.5


def test_dead_empty_commits_branch_removed():
    # main() returns before fetch_diff_and_context when there are no commits,
    # so an empty-commits early return inside it is unreachable code.
    assert "if not commits" not in inspect.getsource(check.fetch_diff_and_context)


# ---------- guard behavior through main() ---------- #


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


def _prompt_blocks(*char_counts):
    return [
        {
            "role": "user",
            "content": [{"type": "text", "text": "x" * n} for n in char_counts],
        }
    ]


def _chars_for(tokens):
    """Characters that estimate to exactly `tokens`.

    Derived from the estimator rather than hardcoded, so the boundary tests
    keep testing the strict-'>' contract even if the divisor is recalibrated.
    """
    probe = 3_000
    chars_per_token = probe // check.estimate_prompt_tokens(_prompt_blocks(probe))
    chars = tokens * chars_per_token
    assert check.estimate_prompt_tokens(_prompt_blocks(chars)) == tokens
    return chars


def _setup(monkeypatch, tmp_path, prompt, email_count=47):
    p = tmp_path / "state.json"
    p.write_text(
        json.dumps({"last_seen_sha": "olds123", "last_run": "ts", "email_count": email_count})
    )
    monkeypatch.setattr(check, "fetch_new_commits", lambda *a, **kw: _commits())
    monkeypatch.setattr(check, "fetch_diff_and_context", lambda *a, **kw: _bundle())
    monkeypatch.setattr(check, "build_prompt", lambda *a, **kw: prompt)
    call_llm = MagicMock(
        return_value=SimpleNamespace(
            stop_reason="tool_use",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
        )
    )
    monkeypatch.setattr(check, "call_llm", call_llm)
    monkeypatch.setattr(
        check,
        "parse_llm_response",
        lambda raw: {"has_issues": False, "summary": "Fine.", "findings": [], "complete": True},
    )

    sent = {}

    def fake_send(host, port, user, password, to_addr, subject, body):
        sent["to"] = to_addr
        sent["subject"] = subject
        sent["body"] = body

    monkeypatch.setattr(check, "send_email", fake_send)
    return p, call_llm, sent


def test_over_budget_skips_llm_and_sends_fallback_email(monkeypatch, tmp_path):
    p, call_llm, sent = _setup(monkeypatch, tmp_path, _prompt_blocks(_chars_for(BUDGET + 1_000)))

    rc = check.main(state_path=p)

    assert rc == 0
    call_llm.assert_not_called()
    assert "body" in sent
    assert "2" in sent["body"]
    assert "commit" in sent["body"].lower()
    assert COMPARE_URL in sent["body"]
    assert CONTACT_NOTE in sent["body"]
    assert "#48" in sent["subject"]
    state = json.loads(p.read_text())
    assert state["last_seen_sha"] == "newsha2def"
    assert state["email_count"] == 48


def test_exactly_at_budget_proceeds_to_llm(monkeypatch, tmp_path):
    # A prompt landing exactly ON the budget is still reviewed — the guard
    # fires only when OVER it.
    p, call_llm, sent = _setup(monkeypatch, tmp_path, _prompt_blocks(_chars_for(BUDGET)))

    rc = check.main(state_path=p)

    assert rc == 0
    call_llm.assert_called_once()
    assert sent == {}
    assert json.loads(p.read_text())["last_seen_sha"] == "newsha2def"


def test_one_token_over_budget_trips_across_multiple_blocks(monkeypatch, tmp_path):
    # Split one token OVER budget across two blocks: proves the estimate sums
    # every text block, not just the first.
    over = _chars_for(BUDGET + 1)
    half = over // 2
    p, call_llm, _ = _setup(monkeypatch, tmp_path, _prompt_blocks(half, over - half))

    rc = check.main(state_path=p)

    assert rc == 0
    call_llm.assert_not_called()


def test_smtp_failure_on_guard_email_stays_loud(monkeypatch, tmp_path):
    p, _, _ = _setup(monkeypatch, tmp_path, _prompt_blocks(_chars_for(BUDGET + 1_000)))

    def boom(*a, **kw):
        raise RuntimeError("smtp connection refused")

    monkeypatch.setattr(check, "send_email", boom)

    rc = check.main(state_path=p)

    assert rc != 0
    state = json.loads(p.read_text())
    assert state["last_seen_sha"] == "olds123"
    assert state["email_count"] == 47


# ---------- observability ---------- #


def test_estimate_is_logged_on_every_reviewed_run(monkeypatch, tmp_path, capsys):
    p, _, _ = _setup(monkeypatch, tmp_path, _prompt_blocks(_chars_for(BUDGET)))

    check.main(state_path=p)

    out = capsys.readouterr().out
    assert "prompt_tokens_estimated" in out
    assert "tokens=200000" in out
    assert "budget=200000" in out


def test_guard_trip_is_logged(monkeypatch, tmp_path, capsys):
    p, _, _ = _setup(monkeypatch, tmp_path, _prompt_blocks(_chars_for(BUDGET + 1_000)))

    check.main(state_path=p)

    out = capsys.readouterr().out
    assert "token_guard_triggered" in out
    assert "budget=200000" in out
