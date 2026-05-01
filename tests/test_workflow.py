"""Structural checks for the GitHub Actions workflow."""
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "check.yml"


def test_workflow_can_write_state_back_to_repo():
    text = WORKFLOW.read_text()

    assert "contents: write" in text


def test_workflow_does_not_use_actions_cache_for_state():
    text = WORKFLOW.read_text()

    assert "actions/cache/restore" not in text
    assert "actions/cache/save" not in text


def test_workflow_commits_state_json_after_successful_run():
    text = WORKFLOW.read_text()

    assert "git diff --quiet state.json" in text
    assert "git add state.json" in text
    assert "git commit" in text
    assert "git push origin HEAD:${{ github.ref_name }}" in text
