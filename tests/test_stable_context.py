"""Stable context wiring for prompts."""
from pathlib import Path

import pytest

from scripts import check


def test_stable_context_reads_local_conventions_file(monkeypatch):
    expected = (Path(__file__).resolve().parents[1] / "prompts" / "conventions.md").read_text()

    def fake_fetch_repo_file(_repo, path, _ref, _gh_token):
        if path == "README.md":
            pytest.fail("conventions should come from prompts/conventions.md, not upstream README.md")
        return f"upstream {path}"

    monkeypatch.setattr(check, "_fetch_repo_file", fake_fetch_repo_file)
    monkeypatch.setattr(check, "_repo_tree", lambda *_args, **_kwargs: ["home.md"])

    stable = check._fetch_stable_context("davidmalawey/openLab", "gh-token", ref="abc123")

    assert stable["conventions"] == expected


def test_stable_context_still_fetches_sidebar_and_navbar(monkeypatch):
    fetched_paths = []

    def fake_fetch_repo_file(_repo, path, _ref, _gh_token):
        fetched_paths.append(path)
        return f"contents of {path}"

    monkeypatch.setattr(check, "_fetch_repo_file", fake_fetch_repo_file)
    monkeypatch.setattr(check, "_repo_tree", lambda *_args, **_kwargs: ["home.md"])

    stable = check._fetch_stable_context("davidmalawey/openLab", "gh-token", ref="abc123")

    assert stable["sidebar"] == "contents of _sidebar.md"
    assert stable["navbar"] == "contents of _navbar.md"
    assert "_sidebar.md" in fetched_paths
    assert "_navbar.md" in fetched_paths
