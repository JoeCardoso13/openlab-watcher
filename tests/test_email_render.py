"""Email rendering: batch-aware body, friendly tone, github links.

Every email describes the WHOLE weekly batch (redesigned 2026-08-06):
summary, then one line per commit message (no SHAs — David is not a SWE),
then findings, then the compare link to the full change set — in every
email, complete or degraded.
"""
from scripts import check


SAMPLE_COMMITS = [
    {
        "sha": "aaa111bbb2223334445556",
        "short_sha": "aaa111b",
        "message": "Add shop lights section to tools page",
        "url": "https://github.com/davidmalawey/openLab/commit/aaa111bbb2223334445556",
    },
    {
        "sha": "61fff5a1234567890abcdef",
        "short_sha": "61fff5a",
        "message": "Include shop lights links and installation tips",
        "url": "https://github.com/davidmalawey/openLab/commit/61fff5a1234567890abcdef",
    },
]

COMPARE_URL = (
    "https://github.com/davidmalawey/openLab/compare/aaa111bbb2223334445556~1...61fff5a1234567890abcdef"
)

SAMPLE_FINDINGS = [
    {
        "severity": "nudge",
        "file": "tools.md",
        "message": "tools.md keeps growing — this section overlaps with organize.md.",
        "suggestion": "Consider moving the storage tips to organize.md or linking out.",
    },
    {
        "severity": "concern",
        "file": "manuals/2025 manual - microphone, Lark M2.pdf",
        "message": "Filename uses lowercase 'manual' while siblings use 'Manual'.",
        "suggestion": "Rename to '2025 Manual - Microphone, Lark M2.pdf'.",
    },
]

SAMPLE_SUMMARY = "Two small consistency notes from this batch."


def _render():
    return check.render_email(
        SAMPLE_FINDINGS,
        SAMPLE_SUMMARY,
        SAMPLE_COMMITS,
        "davidmalawey/openLab",
        48,
        compare_url=COMPARE_URL,
    )


def test_render_email_returns_subject_and_body():
    subject, body = _render()

    assert isinstance(subject, str) and subject
    assert isinstance(body, str) and body


def test_subject_names_the_tool_and_includes_serial():
    subject, _ = _render()

    assert "openLab Watcher" in subject
    assert "#48" in subject


def test_subject_does_not_leak_commit_sha():
    subject, _ = _render()

    assert "61fff5a" not in subject
    assert "aaa111b" not in subject


def test_body_includes_summary_and_each_finding_message():
    _, body = _render()

    assert SAMPLE_SUMMARY in body
    for f in SAMPLE_FINDINGS:
        assert f["message"] in body
        assert f["suggestion"] in body


def test_body_lists_every_commit_message_for_the_week():
    _, body = _render()

    assert "this week" in body.lower()
    for commit in SAMPLE_COMMITS:
        assert commit["message"] in body


def test_commit_list_carries_no_shas():
    """David is not a SWE: a SHA is noise to him as prose.

    Links are exempt — a blob URL is pinned to the latest sha and a compare
    URL is a range, so it necessarily embeds the earliest sha as well. Those
    are opaque strings David clicks, not text he reads. The rule is therefore
    scoped to lines that are not links.
    """
    _, body = _render()

    prose_lines = [line for line in body.splitlines() if "https://" not in line]
    for commit in SAMPLE_COMMITS:
        for line in prose_lines:
            assert commit["sha"] not in line, f"sha leaked into prose: {line!r}"
            assert commit["short_sha"] not in line, f"short sha leaked into prose: {line!r}"


def test_body_order_summary_then_commits_then_findings_then_compare_link():
    _, body = _render()

    sections = [
        SAMPLE_SUMMARY,
        SAMPLE_COMMITS[0]["message"],
        SAMPLE_FINDINGS[0]["message"],
        COMPARE_URL,
    ]
    for section in sections:
        assert section in body, f"missing from body: {section!r}"

    positions = [body.index(section) for section in sections]
    assert positions == sorted(positions), "sections are out of order"


def test_complete_email_includes_compare_link():
    _, body = _render()

    assert COMPARE_URL in body


def test_body_links_each_finding_to_github_blob_url():
    _, body = _render()

    latest_sha = SAMPLE_COMMITS[-1]["sha"]
    for f in SAMPLE_FINDINGS:
        expected_prefix = f"https://github.com/davidmalawey/openLab/blob/{latest_sha}/"
        assert expected_prefix in body
        assert f["file"] in body


def test_body_distinguishes_severity_levels():
    _, body = _render()

    lowered = body.lower()
    assert "nudge" in lowered or "concern" in lowered
