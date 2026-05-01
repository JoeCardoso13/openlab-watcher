"""Parse Anthropic structured-output response into a plain dict."""
import pytest

from scripts import check


def _tool_use_response(input_payload):
    """Mimic the shape of an anthropic.types.Message when the model emits a tool_use block."""
    class _Block:
        def __init__(self):
            self.type = "tool_use"
            self.name = "report_findings"
            self.input = input_payload

    class _Msg:
        def __init__(self):
            self.content = [_Block()]
            self.stop_reason = "tool_use"

    return _Msg()


def test_parse_valid_response_with_findings():
    raw = _tool_use_response({
        "has_issues": True,
        "summary": "Two small naming issues.",
        "findings": [
            {
                "severity": "nudge",
                "file": "manuals/Manual - RidgidBradNailer.pdf",
                "message": "Filename omits the year used by siblings.",
                "suggestion": "Rename to '2025 Manual - Ridgid Brad Nailer.pdf'.",
            }
        ],
    })

    parsed = check.parse_llm_response(raw)

    assert parsed["has_issues"] is True
    assert parsed["summary"].startswith("Two small")
    assert len(parsed["findings"]) == 1
    f = parsed["findings"][0]
    assert f["severity"] == "nudge"
    assert "RidgidBradNailer" in f["file"]


def test_parse_valid_response_with_no_issues():
    raw = _tool_use_response({"has_issues": False, "summary": "All looks consistent.", "findings": []})

    parsed = check.parse_llm_response(raw)

    assert parsed["has_issues"] is False
    assert parsed["findings"] == []


def test_parse_malformed_response_raises():
    class _Msg:
        content = [type("X", (), {"type": "text", "text": "I forgot to use the tool."})()]
        stop_reason = "end_turn"

    with pytest.raises(check.MalformedLLMResponse):
        check.parse_llm_response(_Msg())


def test_parse_response_missing_required_keys_raises():
    raw = _tool_use_response({"summary": "missing has_issues"})

    with pytest.raises(check.MalformedLLMResponse):
        check.parse_llm_response(raw)


def test_parse_invalid_severity_raises():
    raw = _tool_use_response({
        "has_issues": True,
        "summary": "x",
        "findings": [{"severity": "panic", "file": "a", "message": "b", "suggestion": "c"}],
    })

    with pytest.raises(check.MalformedLLMResponse):
        check.parse_llm_response(raw)
