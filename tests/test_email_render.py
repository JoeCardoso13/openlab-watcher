"""Email rendering: subject and body shape, friendly tone, github links."""
from scripts import check


SAMPLE_LATEST_COMMIT = {
    "sha": "61fff5a1234567890abcdef",
    "short_sha": "61fff5a",
    "message": "Include shop lights links and installation tips",
    "url": "https://github.com/davidmalawey/openLab/commit/61fff5a1234567890abcdef",
}

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


def test_render_email_returns_subject_and_body():
    subject, body = check.render_email(
        SAMPLE_FINDINGS, SAMPLE_SUMMARY, SAMPLE_LATEST_COMMIT, repo="davidmalawey/openLab"
    )

    assert isinstance(subject, str) and subject
    assert isinstance(body, str) and body


def test_subject_contains_short_sha():
    subject, _ = check.render_email(
        SAMPLE_FINDINGS, SAMPLE_SUMMARY, SAMPLE_LATEST_COMMIT, repo="davidmalawey/openLab"
    )

    assert "61fff5a" in subject


def test_body_includes_summary_and_each_finding_message():
    _, body = check.render_email(
        SAMPLE_FINDINGS, SAMPLE_SUMMARY, SAMPLE_LATEST_COMMIT, repo="davidmalawey/openLab"
    )

    assert SAMPLE_SUMMARY in body
    for f in SAMPLE_FINDINGS:
        assert f["message"] in body
        assert f["suggestion"] in body


def test_body_links_each_finding_to_github_blob_url():
    _, body = check.render_email(
        SAMPLE_FINDINGS, SAMPLE_SUMMARY, SAMPLE_LATEST_COMMIT, repo="davidmalawey/openLab"
    )

    for f in SAMPLE_FINDINGS:
        expected_prefix = f"https://github.com/davidmalawey/openLab/blob/{SAMPLE_LATEST_COMMIT['sha']}/"
        assert expected_prefix in body
        assert f["file"] in body


def test_body_distinguishes_severity_levels():
    _, body = check.render_email(
        SAMPLE_FINDINGS, SAMPLE_SUMMARY, SAMPLE_LATEST_COMMIT, repo="davidmalawey/openLab"
    )

    lowered = body.lower()
    assert "nudge" in lowered or "concern" in lowered
