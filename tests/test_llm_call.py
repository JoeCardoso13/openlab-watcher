"""Regression tests for call_llm — model ID and tool wiring."""
from unittest.mock import MagicMock

from scripts import check


def _patched_anthropic(monkeypatch):
    """Replace anthropic.Anthropic with a Mock so we can capture .messages.create kwargs."""
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(content=[])
    fake_anthropic_cls = MagicMock(return_value=fake_client)
    monkeypatch.setattr(check.anthropic, "Anthropic", fake_anthropic_cls)
    return fake_anthropic_cls, fake_client


def test_call_llm_uses_claude_sonnet_4_6(monkeypatch):
    _, fake_client = _patched_anthropic(monkeypatch)

    check.call_llm(messages=[{"role": "user", "content": []}], api_key="sk-ant-test")

    fake_client.messages.create.assert_called_once()
    _, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"] == "claude-sonnet-4-6"


def test_call_llm_passes_api_key_to_client(monkeypatch):
    fake_anthropic_cls, _ = _patched_anthropic(monkeypatch)

    check.call_llm(messages=[{"role": "user", "content": []}], api_key="sk-ant-specific")

    fake_anthropic_cls.assert_called_once_with(api_key="sk-ant-specific")


def test_call_llm_forces_report_findings_tool(monkeypatch):
    _, fake_client = _patched_anthropic(monkeypatch)

    check.call_llm(messages=[{"role": "user", "content": []}], api_key="sk-ant-test")

    _, kwargs = fake_client.messages.create.call_args
    tool_names = [t["name"] for t in kwargs["tools"]]
    assert "report_findings" in tool_names
    assert kwargs["tool_choice"] == {"type": "tool", "name": "report_findings"}
