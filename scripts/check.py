"""openlab-watcher run loop."""
from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import anthropic


SKIP_FILE_THRESHOLD = 20
SKIP_BYTE_THRESHOLD = 100_000

UPSTREAM_REPO = "davidmalawey/openLab"
UPSTREAM_BRANCH = "main"
DEFAULT_STATE_PATH = Path("state.json")
CONVENTIONS_PATH = Path(__file__).resolve().parents[1] / "prompts" / "conventions.md"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
TREE_INCLUDE_PREFIXES = ("manuals/", "docs/", "ref/")


class MalformedLLMResponse(ValueError):
    """Raised when the model response cannot be parsed against the expected tool-use schema."""


def read_state(path):
    path = Path(path)
    if not path.exists():
        return {"last_seen_sha": None, "last_run": None}

    payload = json.loads(path.read_text())
    return {
        "last_seen_sha": payload.get("last_seen_sha"),
        "last_run": payload.get("last_run"),
    }


def write_state(path, sha, ts):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_seen_sha": sha, "last_run": ts}, indent=2) + "\n")


def fetch_new_commits(repo, branch, since_sha, gh_token):
    headers = _github_headers(gh_token)
    if since_sha:
        url = f"https://api.github.com/repos/{repo}/compare/{quote(since_sha)}...{quote(branch)}"
        payload = _fetch_json(url, headers)
        commits = payload.get("commits", [])
    else:
        url = f"https://api.github.com/repos/{repo}/commits?sha={quote(branch)}&per_page=25"
        commits = list(reversed(_fetch_json(url, headers)))

    return [_commit_summary(commit) for commit in commits]


def fetch_diff_and_context(repo, commits, gh_token):
    if not commits:
        return {
            "diff": "",
            "edited_files": {},
            "sibling_lists": {},
            "stable_ctx": _fetch_stable_context(repo, gh_token),
            "num_files": 0,
            "total_bytes": 0,
        }

    headers = _github_headers(gh_token)
    first_sha = commits[0]["sha"]
    latest_sha = commits[-1]["sha"]
    compare_url = f"https://api.github.com/repos/{repo}/compare/{quote(first_sha)}~1...{quote(latest_sha)}"
    compare = _fetch_json(compare_url, headers)
    files = compare.get("files", [])

    diff = _fetch_text(compare_url, {**headers, "Accept": "application/vnd.github.diff"})
    edited_files = {}
    sibling_lists = {}
    for file_info in files:
        filename = file_info.get("filename")
        status = file_info.get("status")
        if not filename or status == "removed":
            continue

        if filename.endswith(".md"):
            edited_files[filename] = _fetch_repo_file(repo, filename, latest_sha, gh_token)

        parent = str(Path(filename).parent)
        directory = "" if parent == "." else f"{parent}/"
        if directory not in sibling_lists:
            sibling_lists[directory] = _fetch_directory_listing(repo, directory, latest_sha, gh_token)

    return {
        "diff": diff,
        "edited_files": edited_files,
        "sibling_lists": sibling_lists,
        "stable_ctx": _fetch_stable_context(repo, gh_token, ref=latest_sha),
        "num_files": len(files),
        "total_bytes": len(diff.encode()),
    }


def should_skip(num_files, total_diff_bytes):
    return num_files > SKIP_FILE_THRESHOLD or total_diff_bytes > SKIP_BYTE_THRESHOLD


def build_prompt(stable_ctx, diff, edited_md, sibling_lists):
    stable_text = "\n".join(
        [
            "Repository context:",
            f"Conventions:\n{stable_ctx.get('conventions', '')}",
            f"Sidebar:\n{stable_ctx.get('sidebar', '')}",
            f"Navbar:\n{stable_ctx.get('navbar', '')}",
            "Tree:",
            "\n".join(stable_ctx.get("tree", [])),
        ]
    )
    edited_text = "\n\n".join(
        f"File: {path}\n\n{content}" for path, content in sorted(edited_md.items())
    )
    sibling_text = "\n\n".join(
        f"Directory: {directory or './'}\n" + "\n".join(names)
        for directory, names in sorted(sibling_lists.items())
    )

    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Review this openLab documentation change for consistency issues. "
                        "Report only concrete issues worth emailing about."
                    ),
                },
                {
                    "type": "text",
                    "text": stable_text,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": f"Diff:\n{diff}"},
                {"type": "text", "text": f"Full edited markdown files:\n{edited_text}"},
                {"type": "text", "text": f"Sibling filenames in affected directories:\n{sibling_text}"},
            ],
        }
    ]


def call_llm(messages, api_key):
    client = anthropic.Anthropic(api_key=api_key)
    return client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=messages,
        tools=[
            {
                "name": "report_findings",
                "description": "Report whether the openLab change has consistency issues.",
                "input_schema": {
                    "type": "object",
                    "required": ["has_issues", "summary", "findings"],
                    "properties": {
                        "has_issues": {"type": "boolean"},
                        "summary": {"type": "string"},
                        "findings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["severity", "file", "message", "suggestion"],
                                "properties": {
                                    "severity": {"type": "string", "enum": ["nudge", "concern"]},
                                    "file": {"type": "string"},
                                    "message": {"type": "string"},
                                    "suggestion": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            }
        ],
        tool_choice={"type": "tool", "name": "report_findings"},
    )


def parse_llm_response(raw):
    for block in getattr(raw, "content", []):
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "report_findings":
            payload = getattr(block, "input", None)
            return _validate_llm_payload(payload)
    raise MalformedLLMResponse("model did not call report_findings")


def render_email(findings, summary, latest_commit, repo):
    short_sha = latest_commit.get("short_sha") or latest_commit.get("sha", "")[:7]
    subject = f"openLab consistency notes for {short_sha}"
    lines = [
        summary,
        "",
        f"Commit: {latest_commit.get('message', '')}",
        latest_commit.get("url", ""),
        "",
    ]
    for finding in findings:
        file_path = finding["file"]
        blob_url = f"https://github.com/{repo}/blob/{latest_commit['sha']}/{file_path}"
        lines.extend(
            [
                f"[{finding['severity']}] {file_path}",
                finding["message"],
                f"Suggestion: {finding['suggestion']}",
                blob_url,
                "",
            ]
        )
    return subject, "\n".join(lines).strip() + "\n"


def send_email(smtp_host, smtp_port, smtp_user, smtp_password, to_addr, subject, body):
    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)


def main(state_path: Path | None = None):
    state_path = state_path or DEFAULT_STATE_PATH
    try:
        state = read_state(state_path)
        gh_token = os.environ.get("GH_TOKEN")
        commits = fetch_new_commits(UPSTREAM_REPO, UPSTREAM_BRANCH, state["last_seen_sha"], gh_token)
        if not commits:
            return 0

        bundle = fetch_diff_and_context(UPSTREAM_REPO, commits, gh_token)
        latest_commit = commits[-1]
        now = _now_iso()

        if should_skip(bundle["num_files"], bundle["total_bytes"]):
            write_state(state_path, latest_commit["sha"], now)
            return 0

        messages = build_prompt(
            bundle["stable_ctx"],
            bundle["diff"],
            bundle["edited_files"],
            bundle["sibling_lists"],
        )
        raw = call_llm(messages, os.environ["ANTHROPIC_API_KEY"])
        parsed = parse_llm_response(raw)

        if parsed["has_issues"]:
            subject, body = render_email(
                parsed["findings"], parsed["summary"], latest_commit, repo=UPSTREAM_REPO
            )
            send_email(
                SMTP_HOST,
                SMTP_PORT,
                os.environ["SMTP_USER"],
                os.environ["SMTP_PASSWORD"],
                os.environ["RECIPIENT_EMAIL"],
                subject,
                body,
            )

        write_state(state_path, latest_commit["sha"], now)
        return 0
    except Exception as exc:
        print(f"openlab-watcher failed: {exc}", file=sys.stderr)
        return 1


def _now_iso():
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _github_headers(gh_token):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "openlab-watcher",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
    return headers


def _fetch_json(url, headers):
    return json.loads(_fetch_bytes(url, headers).decode())


def _fetch_text(url, headers):
    return _fetch_bytes(url, headers).decode()


def _fetch_bytes(url, headers):
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"GitHub request failed for {url}: {exc}") from exc


def _commit_summary(commit):
    sha = commit["sha"]
    message = commit.get("commit", {}).get("message", "").splitlines()[0]
    return {
        "sha": sha,
        "short_sha": sha[:7],
        "message": message,
        "url": commit.get("html_url", f"https://github.com/{UPSTREAM_REPO}/commit/{sha}"),
    }


def _fetch_repo_file(repo, path, ref, gh_token):
    headers = _github_headers(gh_token)
    headers["Accept"] = "application/vnd.github.raw"
    return _fetch_text(
        f"https://api.github.com/repos/{repo}/contents/{quote(path)}?ref={quote(ref)}",
        headers,
    )


def _fetch_directory_listing(repo, directory, ref, gh_token):
    path = quote(directory.rstrip("/"))
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={quote(ref)}"
    entries = _fetch_json(url, _github_headers(gh_token))
    if not isinstance(entries, list):
        return []
    return sorted(entry["name"] + ("/" if entry.get("type") == "dir" else "") for entry in entries)


def _fetch_stable_context(repo, gh_token, ref=UPSTREAM_BRANCH):
    return {
        "conventions": CONVENTIONS_PATH.read_text(),
        "sidebar": _optional_repo_file(repo, "_sidebar.md", ref, gh_token),
        "navbar": _optional_repo_file(repo, "_navbar.md", ref, gh_token),
        "tree": _repo_tree(repo, ref, gh_token),
    }


def _optional_repo_file(repo, path, ref, gh_token):
    try:
        return _fetch_repo_file(repo, path, ref, gh_token)
    except RuntimeError:
        return ""


def _repo_tree(repo, ref, gh_token):
    headers = _github_headers(gh_token)
    commit = _fetch_json(f"https://api.github.com/repos/{repo}/commits/{quote(ref)}", headers)
    tree_sha = commit["commit"]["tree"]["sha"]
    tree = _fetch_json(
        f"https://api.github.com/repos/{repo}/git/trees/{quote(tree_sha)}?recursive=1",
        headers,
    )
    filtered = []
    for item in tree.get("tree", []):
        path = item["path"] + ("/" if item.get("type") == "tree" else "")
        if _include_tree_path(path, item.get("type")):
            filtered.append(path)
    return sorted(filtered)


def _include_tree_path(path, item_type):
    stripped = path.rstrip("/")
    return (
        path.endswith(".md")
        or (item_type == "tree" and "/" not in stripped)
        or path.startswith(TREE_INCLUDE_PREFIXES)
    )


def _validate_llm_payload(payload):
    if not isinstance(payload, dict):
        raise MalformedLLMResponse("tool payload is not an object")
    required = {"has_issues", "summary", "findings"}
    if not required.issubset(payload):
        missing = ", ".join(sorted(required - set(payload)))
        raise MalformedLLMResponse(f"tool payload missing keys: {missing}")
    if not isinstance(payload["has_issues"], bool):
        raise MalformedLLMResponse("has_issues must be a boolean")
    if not isinstance(payload["summary"], str):
        raise MalformedLLMResponse("summary must be a string")
    if not isinstance(payload["findings"], list):
        raise MalformedLLMResponse("findings must be a list")

    findings = []
    for finding in payload["findings"]:
        if not isinstance(finding, dict):
            raise MalformedLLMResponse("finding must be an object")
        finding_required = {"severity", "file", "message", "suggestion"}
        if not finding_required.issubset(finding):
            missing = ", ".join(sorted(finding_required - set(finding)))
            raise MalformedLLMResponse(f"finding missing keys: {missing}")
        if finding["severity"] not in {"nudge", "concern"}:
            raise MalformedLLMResponse(f"invalid severity: {finding['severity']}")
        normalized = {key: finding[key] for key in finding_required}
        if not all(isinstance(value, str) and value for value in normalized.values()):
            raise MalformedLLMResponse("finding fields must be non-empty strings")
        findings.append(normalized)

    return {
        "has_issues": payload["has_issues"],
        "summary": payload["summary"],
        "findings": findings,
    }


if __name__ == "__main__":
    raise SystemExit(main())
