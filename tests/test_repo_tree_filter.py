"""Tree filtering keeps useful context and drops noisy assets."""
from scripts import check


def test_repo_tree_filters_to_markdown_top_dirs_and_structured_asset_dirs(monkeypatch):
    def fake_fetch_json(url, _headers):
        if "/commits/" in url:
            return {"commit": {"tree": {"sha": "tree123"}}}
        return {
            "tree": [
                {"path": "home.md", "type": "blob"},
                {"path": "tools.md", "type": "blob"},
                {"path": "img", "type": "tree"},
                {"path": "img/foo.jpg", "type": "blob"},
                {"path": "img/bar/baz.jpg", "type": "blob"},
                {"path": "manuals", "type": "tree"},
                {"path": "manuals/2025 Manual - X.pdf", "type": "blob"},
                {"path": "docs", "type": "tree"},
                {"path": "docs/data.pdf", "type": "blob"},
                {"path": "pano", "type": "tree"},
                {"path": "pano/360.jpg", "type": "blob"},
                {"path": "ref", "type": "tree"},
                {"path": "ref/whitepaper.pdf", "type": "blob"},
            ]
        }

    monkeypatch.setattr(check, "_fetch_json", fake_fetch_json)

    tree = check._repo_tree("davidmalawey/openLab", "abc123", "gh-token")

    assert "home.md" in tree
    assert "tools.md" in tree
    assert "img/" in tree
    assert "manuals/" in tree
    assert "manuals/2025 Manual - X.pdf" in tree
    assert "docs/" in tree
    assert "docs/data.pdf" in tree
    assert "pano/" in tree
    assert "ref/" in tree
    assert "ref/whitepaper.pdf" in tree
    assert "img/foo.jpg" not in tree
    assert "img/bar/baz.jpg" not in tree
    assert "pano/360.jpg" not in tree
