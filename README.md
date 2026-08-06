# openLab Watcher

`openlab-watcher` watches [davidmalawey/openLab](https://github.com/davidmalawey/openLab) and emails small consistency notes when new commits introduce documentation issues.

It is intentionally narrow: it does not review code quality or style in general. It looks for practical documentation problems such as broken internal links, navigation/sidebar drift, filename inconsistency, orphaned pages, contradictory specs, and large changes that would be better split into clearer pages.

## How It Works

Every scheduled run:

1. Reads `state.json` to find the last upstream openLab commit it reviewed.
2. Asks the GitHub API for commits after that SHA.
3. Exits immediately if there are no new commits.
4. Fetches the diff, edited markdown files, nearby sibling filenames, sidebar/navbar files, and a compact repository tree.
5. Estimates the prompt size locally (characters / 3, a deliberately conservative token ratio). If it exceeds `MAX_INPUT_TOKENS` (200,000), the run skips the paid model call and emails the commit count plus a compare link instead, so an oversized week is never silently swallowed.
6. Sends the change context to Claude using a forced structured-output tool.
7. Sends an email only if Claude returns `has_issues: true`. Each email subject is `[openLab Watcher] #<n>`, where `<n>` is a serial number that increments by one per delivered email so the recipient can see the order at a glance.
8. Advances `state.json` and commits it back to this repository from GitHub Actions.

If the model response comes back truncated or partially malformed, the run degrades instead of failing: `max_tokens` is 8192 with one retry on truncation; a partial payload is salvaged (well-formed findings kept, broken ones dropped) and emailed with an explicit "report was cut short" note plus a compare link to the full change set; a totally unusable response still emails a minimal factual note with the commit count and compare link. All of these degraded runs advance state and exit 0. Only infrastructure failures (GitHub API, Anthropic API/network, SMTP) fail the workflow loudly with state untouched, so the next run retries the same batch.

The scheduled workflow scans once a week, Thursday at 9(ish) AM Central time (+/- daylight saving), and can also be triggered manually from GitHub Actions.

## Repository Context

The watcher uses `prompts/conventions.md` as its stable, hand-authored description of openLab conventions. That file tells Claude what patterns matter, including:

- `_sidebar.md` as the navigation source of truth
- root markdown pages as the curated page layer
- asset directories such as `img/`, `docs/`, `manuals/`, `ref/`, and `pano/`
- naming drift in asset directories
- recurring bloat risk in pages such as `tools.md` and `methods.md`

This local conventions file is used instead of the upstream README because it is operational guidance for the watcher, not public-facing site copy.

## Setup

This project uses `uv`.

```bash
uv run pytest
```

GitHub Actions needs these repository secrets:

```text
ANTHROPIC_API_KEY
SMTP_USER
SMTP_PASSWORD
RECIPIENT_EMAIL
```

For Gmail SMTP, `SMTP_PASSWORD` must be a Google App Password, not the normal Gmail account password.

The workflow uses GitHub's built-in token for public GitHub API reads:

```yaml
GH_TOKEN: ${{ github.token }}
```

## Local Smoke Tests

Normal tests do not call live services:

```bash
uv run pytest
```

Live smoke tests are gated behind environment variables because they call GitHub, Anthropic, and SMTP:

```bash
set -a
source .env
set +a
uv run pytest tests/live
```

Expected `.env` variables for live tests:

```text
RUN_LIVE_TESTS=1
LIVE_RECIPIENT=you@example.com
ANTHROPIC_API_KEY=...
SMTP_USER=...
SMTP_PASSWORD=...
GH_TOKEN=...
```

`tests/live/test_e2e_live.py` sends to `LIVE_RECIPIENT`, not the production `RECIPIENT_EMAIL`, so local smoke tests do not accidentally email the production recipient.

## Operational Notes

`state.json` is the production cursor. It contains the last upstream openLab commit SHA that was reviewed (`last_seen_sha`) and `email_count`, the running serial used for email subjects. The serial only advances when an email is actually delivered, so consecutive emails are numbered consecutively. If you need to force a known commit to be reviewed again, set `last_seen_sha` to the upstream commit immediately before it, commit that state change, and manually run the workflow.

The run logs intentionally include minimal diagnostics:

```text
openlab-watcher: commits_found=...
openlab-watcher: diff_context files=... bytes=... latest_sha=...
openlab-watcher: prompt_tokens_estimated tokens=... budget=200000
openlab-watcher: token_guard_triggered tokens=... budget=200000   (only when over budget)
openlab-watcher: llm_response stop_reason=... input_tokens=... output_tokens=...
openlab-watcher: llm_result has_issues=... findings=... complete=...
openlab-watcher: rung=0|2|3
openlab-watcher: email_send_success to=d***@example.com serial=...
```

`rung` records how the run degraded: 0 = normal full review, 2 = salvaged a partial model response, 3 = model response unusable, minimal factual email sent.

Compare `prompt_tokens_estimated` against the `input_tokens` on the following `llm_response` line to see how the local estimate is tracking reality. The estimate is deliberately biased high — measured against live openLab content it runs about 6% above the real tokenizer, so the guard trips slightly early rather than slightly late. `tests/live/test_token_estimate_live.py` re-checks that margin against the real API; run it after any change to `build_prompt`, the model, or the divisor.

Recipient addresses are masked in logs. Secrets are never printed.

No Anthropic tokens are used when there are no new upstream commits. On a normal complete review, no email is sent unless Claude reports at least one finding; degraded runs (rung 2 or 3) always email, because a truncated "no issues" cannot be trusted.

## Development

Run the default suite:

```bash
uv run pytest
```

Run with coverage:

```bash
uv run pytest --cov=scripts --cov-report=term-missing
```

Run only workflow structure checks:

```bash
uv run pytest tests/test_workflow.py
```
