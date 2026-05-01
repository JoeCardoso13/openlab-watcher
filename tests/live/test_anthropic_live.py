"""Live Anthropic smoke test. Enable with RUN_LIVE_TESTS=1."""
import os

import pytest

from scripts import check


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to run live smoke tests",
)


def test_anthropic_live_returns_parseable_tool_response():
    api_key = os.environ["ANTHROPIC_API_KEY"]
    messages = check.build_prompt(
        stable_ctx={
            "conventions": "Root markdown files should be linked from _sidebar.md.",
            "sidebar": "- [Home](home.md)\n",
            "navbar": "",
            "tree": ["home.md"],
        },
        diff="diff --git a/home.md b/home.md\n+Small wording update.\n",
        edited_md={"home.md": "# Home\n\nSmall wording update.\n"},
        sibling_lists={},
    )

    raw = check.call_llm(messages, api_key=api_key)
    parsed = check.parse_llm_response(raw)

    assert set(parsed) == {"has_issues", "summary", "findings"}
    assert isinstance(parsed["has_issues"], bool)
    assert isinstance(parsed["summary"], str)
    assert isinstance(parsed["findings"], list)
