"""Live GitHub smoke tests. Enable with RUN_LIVE_TESTS=1."""
import os

import pytest

from scripts import check


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to run live smoke tests",
)


def test_github_live_fetches_commits_and_context():
    token = os.environ.get("GH_TOKEN")
    commits = check.fetch_new_commits("davidmalawey/openLab", "main", since_sha=None, gh_token=token)

    assert commits
    latest_batch = commits[-5:]
    for commit in latest_batch:
        assert commit["sha"]
        assert commit["short_sha"]
        assert "github.com/davidmalawey/openLab/commit/" in commit["url"]

    bundle = check.fetch_diff_and_context("davidmalawey/openLab", latest_batch, token)

    assert isinstance(bundle["diff"], str)
    assert bundle["stable_ctx"]["sidebar"]
    assert "conventions.md" not in bundle["stable_ctx"]["conventions"]
    assert bundle["num_files"] >= 0
    assert bundle["total_bytes"] >= 0
