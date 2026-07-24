"""Parse Anthropic structured-output response into a plain dict.

Salvage contract (degradation ladder, Rung 2): a tool_use payload is never
rejected outright. Well-formed parts are kept, broken parts dropped, and the
result carries complete=False whenever anything had to be salvaged. Only a
response with no report_findings tool_use block at all raises (Rung 3 signal).
"""
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
    assert parsed["complete"] is True
    assert len(parsed["findings"]) == 1
    f = parsed["findings"][0]
    assert f["severity"] == "nudge"
    assert "RidgidBradNailer" in f["file"]


def test_parse_valid_response_with_no_issues():
    raw = _tool_use_response({"has_issues": False, "summary": "All looks consistent.", "findings": []})

    parsed = check.parse_llm_response(raw)

    assert parsed["has_issues"] is False
    assert parsed["findings"] == []
    assert parsed["complete"] is True


def test_parse_no_tool_use_block_raises():
    class _Msg:
        content = [type("X", (), {"type": "text", "text": "I forgot to use the tool."})()]
        stop_reason = "end_turn"

    with pytest.raises(check.MalformedLLMResponse):
        check.parse_llm_response(_Msg())


def test_parse_missing_findings_salvages_summary():
    # The production incident of 2026-07-23: truncation dropped `findings`
    # but has_issues and summary arrived intact.
    raw = _tool_use_response({"has_issues": True, "summary": "Several naming issues."})

    parsed = check.parse_llm_response(raw)

    assert parsed["complete"] is False
    assert parsed["summary"] == "Several naming issues."
    assert parsed["findings"] == []
    assert parsed["has_issues"] is True


def test_parse_broken_finding_dropped_good_finding_kept():
    good = {"severity": "nudge", "file": "tools.md", "message": "m", "suggestion": "s"}
    raw = _tool_use_response({
        "has_issues": True,
        "summary": "x",
        "findings": [
            good,
            {"severity": "panic", "file": "a", "message": "b", "suggestion": "c"},
            {"severity": "concern", "file": "b.md", "message": "truncat"},
        ],
    })

    parsed = check.parse_llm_response(raw)

    assert parsed["complete"] is False
    assert parsed["findings"] == [good]


def test_parse_missing_has_issues_defaults_conservatively_to_true():
    raw = _tool_use_response({"summary": "missing has_issues"})

    parsed = check.parse_llm_response(raw)

    assert parsed["complete"] is False
    assert parsed["has_issues"] is True


def test_parse_non_string_summary_salvaged_as_empty():
    raw = _tool_use_response({"has_issues": True, "summary": 42, "findings": []})

    parsed = check.parse_llm_response(raw)

    assert parsed["complete"] is False
    assert parsed["summary"] == ""


def test_parse_non_dict_payload_salvages_to_conservative_default():
    raw = _tool_use_response("not an object at all")

    parsed = check.parse_llm_response(raw)

    assert parsed["complete"] is False
    assert parsed["has_issues"] is True
    assert parsed["summary"] == ""
    assert parsed["findings"] == []


def test_parse_non_dict_finding_dropped():
    raw = _tool_use_response({"has_issues": True, "summary": "x", "findings": ["oops"]})

    parsed = check.parse_llm_response(raw)

    assert parsed["complete"] is False
    assert parsed["findings"] == []


def test_parse_finding_with_empty_field_dropped():
    raw = _tool_use_response({
        "has_issues": True,
        "summary": "x",
        "findings": [{"severity": "nudge", "file": "", "message": "m", "suggestion": "s"}],
    })

    parsed = check.parse_llm_response(raw)

    assert parsed["complete"] is False
    assert parsed["findings"] == []
