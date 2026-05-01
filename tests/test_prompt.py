"""Prompt construction: stable context cached, diff + edited content + siblings included."""
from scripts import check


SAMPLE_STABLE = {
    "conventions": "Site is Docsify-rendered. _sidebar.md is source of truth.",
    "sidebar": "- [Home](home.md)\n- [Tools](tools.md)\n",
    "navbar": "- [Github](https://github.com/davidmalawey/openLab)\n",
    "tree": ["home.md", "tools.md", "tour.md", "img/", "manuals/"],
}

SAMPLE_DIFF = """diff --git a/tools.md b/tools.md
+## New section about gloves
+Some text here.
"""

SAMPLE_EDITED = {
    "tools.md": "# Tools\n\n## New section about gloves\nSome text here.\n",
}

SAMPLE_SIBLINGS = {
    "manuals/": [
        "2025 Manual - Vacuum - Shark NV360.pdf",
        "Manual - RidgidBradNailer.pdf",
    ],
}


def _all_text_blocks(prompt):
    blocks = []
    for msg in prompt:
        content = msg.get("content", [])
        if isinstance(content, str):
            blocks.append(content)
        else:
            for c in content:
                if c.get("type") == "text":
                    blocks.append(c.get("text", ""))
    return blocks


def test_build_prompt_returns_messages_list():
    prompt = check.build_prompt(SAMPLE_STABLE, SAMPLE_DIFF, SAMPLE_EDITED, SAMPLE_SIBLINGS)

    assert isinstance(prompt, list)
    assert len(prompt) >= 1
    assert all("role" in m and "content" in m for m in prompt)


def test_stable_context_block_has_ephemeral_cache_control():
    prompt = check.build_prompt(SAMPLE_STABLE, SAMPLE_DIFF, SAMPLE_EDITED, SAMPLE_SIBLINGS)

    cached_blocks = []
    for msg in prompt:
        content = msg["content"] if isinstance(msg["content"], list) else []
        for c in content:
            if c.get("cache_control") == {"type": "ephemeral"}:
                cached_blocks.append(c)

    assert len(cached_blocks) >= 1, "expected at least one block with ephemeral cache_control"


def test_stable_context_includes_conventions_and_sidebar():
    prompt = check.build_prompt(SAMPLE_STABLE, SAMPLE_DIFF, SAMPLE_EDITED, SAMPLE_SIBLINGS)
    text = "\n".join(_all_text_blocks(prompt))

    assert SAMPLE_STABLE["conventions"] in text
    assert SAMPLE_STABLE["sidebar"] in text


def test_diff_is_included():
    prompt = check.build_prompt(SAMPLE_STABLE, SAMPLE_DIFF, SAMPLE_EDITED, SAMPLE_SIBLINGS)
    text = "\n".join(_all_text_blocks(prompt))

    assert "New section about gloves" in text


def test_edited_file_full_content_included():
    prompt = check.build_prompt(SAMPLE_STABLE, SAMPLE_DIFF, SAMPLE_EDITED, SAMPLE_SIBLINGS)
    text = "\n".join(_all_text_blocks(prompt))

    assert "tools.md" in text
    assert SAMPLE_EDITED["tools.md"] in text


def test_sibling_filenames_included_for_affected_dirs():
    prompt = check.build_prompt(SAMPLE_STABLE, SAMPLE_DIFF, SAMPLE_EDITED, SAMPLE_SIBLINGS)
    text = "\n".join(_all_text_blocks(prompt))

    assert "Manual - RidgidBradNailer.pdf" in text
    assert "2025 Manual - Vacuum - Shark NV360.pdf" in text
