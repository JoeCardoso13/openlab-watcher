"""Live calibration check for the cost guardrail. Enable with RUN_LIVE_TESTS=1.

The guard is only trustworthy if the local estimate never reads LOW against
real Anthropic tokenization. This builds a real prompt from the live openLab
repo and compares estimate_prompt_tokens() to the count_tokens endpoint
(free — no generation, no output tokens billed).

Re-run this after any change to build_prompt, the model, or the divisor.
"""
import os

import anthropic
import pytest

from scripts import check


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to run live smoke tests",
)


def test_estimate_is_conservative_against_real_tokenizer():
    gh_token = os.environ.get("GH_TOKEN")
    commits = check.fetch_new_commits(
        check.UPSTREAM_REPO, check.UPSTREAM_BRANCH, since_sha=None, gh_token=gh_token
    )
    assert commits, "expected a non-empty batch from live openLab"

    bundle = check.fetch_diff_and_context(check.UPSTREAM_REPO, commits, gh_token)
    messages = check.build_prompt(
        bundle["stable_ctx"], bundle["diff"], bundle["edited_files"], bundle["sibling_lists"]
    )

    estimated = check.estimate_prompt_tokens(messages)
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    real = client.messages.count_tokens(
        model="claude-sonnet-4-6", messages=messages
    ).input_tokens

    assert estimated >= real, (
        f"estimator reads LOW on real content ({estimated:,} < {real:,}): the guard "
        "would allow more spend than MAX_INPUT_TOKENS promises"
    )
    assert estimated <= real * 1.5, (
        f"estimator is wildly paranoid ({estimated:,} vs {real:,}): the guard would "
        "trip on ordinary weeks"
    )
