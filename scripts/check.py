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


UPSTREAM_REPO = "davidmalawey/openLab"
UPSTREAM_BRANCH = "main"
DEFAULT_STATE_PATH = Path("state.json")
CONVENTIONS_PATH = Path(__file__).resolve().parents[1] / "prompts" / "conventions.md"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
TREE_INCLUDE_PREFIXES = ("manuals/", "docs/", "ref/")
LLM_MAX_TOKENS = 8192

# Cost guardrail: a week whose prompt exceeds this never reaches the API.
# Estimated locally (chars // 4) so the check itself costs nothing.
MAX_INPUT_TOKENS = 200_000

CONTACT_NOTE = (
    "If this problem persists, and/or is bothering you, please contact "
    "Joe Cardoso, the master of *agent* puppets, he'll be happy to help."
)


class MalformedLLMResponse(ValueError):
    """Raised only when the model response contains no report_findings tool call at all."""


def read_state(path):
    path = Path(path)
    if not path.exists():
        return {"last_seen_sha": None, "last_run": None, "email_count": 0}

    payload = json.loads(path.read_text())
    return {
        "last_seen_sha": payload.get("last_seen_sha"),
        "last_run": payload.get("last_run"),
        "email_count": payload.get("email_count", 0),
    }


def write_state(path, sha, ts, email_count=0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"last_seen_sha": sha, "last_run": ts, "email_count": email_count}, indent=2
        )
        + "\n"
    )


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


def estimate_prompt_tokens(messages):
    """Rough local token count: every text block's characters // 3.

    Deliberately biased high. Measured against count_tokens on a real openLab
    batch, the usual chars/4 rule of thumb read 25% LOW on this content
    (markdown, filenames, diff punctuation all tokenize densely). A wallet
    guard that reads low spends more than its budget promises, so round the
    divisor down instead.
    """
    chars = 0
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                chars += len(block.get("text", ""))
    return chars // 3


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
                {"type": "text", "text": stable_text},
                {"type": "text", "text": f"Diff:\n{diff}"},
                {"type": "text", "text": f"Full edited markdown files:\n{edited_text}"},
                {"type": "text", "text": f"Sibling filenames in affected directories:\n{sibling_text}"},
            ],
        }
    ]


def call_llm(messages, api_key):
    client = anthropic.Anthropic(api_key=api_key)
    response = _create_message(client, messages)
    if getattr(response, "stop_reason", None) == "max_tokens":
        _log("llm_truncated_retry")
        response = _create_message(client, messages)
    return response


def _create_message(client, messages):
    return client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=LLM_MAX_TOKENS,
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
            return _salvage_llm_payload(payload)
    raise MalformedLLMResponse("model did not call report_findings")


def render_email(findings, summary, commits, repo, serial, complete=True, compare_url=None):
    latest_commit = commits[-1]
    subject = f"[openLab Watcher] #{serial}"
    lines = [
        summary,
        "",
        f"This covers {len(commits)} commit(s) from openLab this week:",
    ]
    # Message only — David reads changes by their description, not by sha.
    lines.extend(f"- {commit.get('message', '')}" for commit in commits)
    lines.append("")
    if not complete:
        lines.extend(
            [
                "Note: this report was cut short, so some findings may be missing.",
                "",
            ]
        )
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
    lines.extend(
        [
            "You can review the full change set here:",
            compare_url or "",
        ]
    )
    if not complete:
        lines.extend(["", CONTACT_NOTE])
    return subject, "\n".join(lines).strip() + "\n"


def render_fallback_email(commit_count, compare_url, serial):
    subject = f"[openLab Watcher] #{serial}"
    body = "\n".join(
        [
            f"openLab had {commit_count} new commit(s) this week, but the automated "
            "reviewer couldn't produce consistency notes this time.",
            "",
            "You can review the full change set here:",
            compare_url,
            "",
            CONTACT_NOTE,
        ]
    )
    return subject, body + "\n"


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
        email_count = state["email_count"]
        _log(f"state_loaded last_seen_sha={state['last_seen_sha']} email_count={email_count}")
        gh_token = os.environ.get("GH_TOKEN")
        commits = fetch_new_commits(UPSTREAM_REPO, UPSTREAM_BRANCH, state["last_seen_sha"], gh_token)
        _log(f"commits_found={len(commits)}")
        if not commits:
            _log("no_new_commits")
            return 0

        bundle = fetch_diff_and_context(UPSTREAM_REPO, commits, gh_token)
        latest_commit = commits[-1]
        now = _now_iso()
        _log(
            "diff_context "
            f"files={bundle['num_files']} bytes={bundle['total_bytes']} latest_sha={latest_commit['sha']}"
        )

        compare_url = (
            f"https://github.com/{UPSTREAM_REPO}/compare/"
            f"{commits[0]['sha']}~1...{latest_commit['sha']}"
        )

        messages = build_prompt(
            bundle["stable_ctx"],
            bundle["diff"],
            bundle["edited_files"],
            bundle["sibling_lists"],
        )
        estimated_tokens = estimate_prompt_tokens(messages)
        _log(f"prompt_tokens_estimated tokens={estimated_tokens} budget={MAX_INPUT_TOKENS}")

        # Too big to review: say so out loud rather than swallowing the week.
        if estimated_tokens > MAX_INPUT_TOKENS:
            _log(f"token_guard_triggered tokens={estimated_tokens} budget={MAX_INPUT_TOKENS}")
            serial = email_count + 1
            subject, body = render_fallback_email(len(commits), compare_url, serial)
            _deliver(subject, body, serial, findings_count=0)
            email_count = serial
            write_state(state_path, latest_commit["sha"], now, email_count)
            _log(f"state_advanced sha={latest_commit['sha']}")
            return 0

        raw = call_llm(messages, os.environ["ANTHROPIC_API_KEY"])
        _log_llm_response(raw)

        try:
            parsed = parse_llm_response(raw)
        except MalformedLLMResponse as exc:
            _log(f"llm_unusable {exc}")
            _log("rung=3")
            serial = email_count + 1
            subject, body = render_fallback_email(len(commits), compare_url, serial)
            _deliver(subject, body, serial, findings_count=0)
            email_count = serial
            write_state(state_path, latest_commit["sha"], now, email_count)
            _log(f"state_advanced sha={latest_commit['sha']}")
            return 0

        findings_count = len(parsed["findings"])
        complete = parsed.get("complete", True)
        _log(f"llm_result has_issues={parsed['has_issues']} findings={findings_count} complete={complete}")
        _log(f"rung={0 if complete else 2}")

        # An incomplete response always emails: a truncated "no issues" can't be trusted.
        if parsed["has_issues"] or not complete:
            serial = email_count + 1
            subject, body = render_email(
                parsed["findings"],
                parsed["summary"],
                commits,
                UPSTREAM_REPO,
                serial,
                complete=complete,
                compare_url=compare_url,
            )
            _deliver(subject, body, serial, findings_count)
            email_count = serial

        write_state(state_path, latest_commit["sha"], now, email_count)
        _log(f"state_advanced sha={latest_commit['sha']}")
        return 0
    except Exception as exc:
        print(f"openlab-watcher failed: {exc}", file=sys.stderr)
        return 1


def _log(message):
    print(f"openlab-watcher: {message}", flush=True)


def _log_llm_response(raw):
    stop_reason = getattr(raw, "stop_reason", None)
    usage = getattr(raw, "usage", None)
    _log(
        f"llm_response stop_reason={stop_reason} "
        f"input_tokens={getattr(usage, 'input_tokens', None)} "
        f"output_tokens={getattr(usage, 'output_tokens', None)}"
    )


def _deliver(subject, body, serial, findings_count):
    masked_recipient = _mask_email(os.environ["RECIPIENT_EMAIL"])
    _log(f"email_send_start to={masked_recipient} findings={findings_count} serial={serial}")
    send_email(
        SMTP_HOST,
        SMTP_PORT,
        os.environ["SMTP_USER"],
        os.environ["SMTP_PASSWORD"],
        os.environ["RECIPIENT_EMAIL"],
        subject,
        body,
    )
    _log(f"email_send_success to={masked_recipient} serial={serial}")


def _mask_email(address):
    local, separator, domain = address.partition("@")
    if not separator:
        return "***"
    prefix = local[:1] or "*"
    return f"{prefix}***@{domain}"


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


def _salvage_llm_payload(payload):
    """Keep every well-formed part of a possibly truncated payload.

    A partial response (e.g. output cut at max_tokens) must never cost the run:
    salvage what arrived, mark the result complete=False, and let the caller
    disclose the gap instead of discarding usable analysis.
    """
    if not isinstance(payload, dict):
        payload = {}
    complete = isinstance(payload.get("has_issues"), bool)
    has_issues = payload["has_issues"] if complete else True

    summary = payload.get("summary")
    if not isinstance(summary, str):
        summary = ""
        complete = False

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raw_findings = []
        complete = False

    findings = []
    for finding in raw_findings:
        normalized = _well_formed_finding(finding)
        if normalized is None:
            complete = False
        else:
            findings.append(normalized)

    return {
        "has_issues": has_issues,
        "summary": summary,
        "findings": findings,
        "complete": complete,
    }


def _well_formed_finding(finding):
    if not isinstance(finding, dict):
        return None
    required = ("severity", "file", "message", "suggestion")
    if not all(key in finding for key in required):
        return None
    if finding["severity"] not in {"nudge", "concern"}:
        return None
    normalized = {key: finding[key] for key in required}
    if not all(isinstance(value, str) and value for value in normalized.values()):
        return None
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())
