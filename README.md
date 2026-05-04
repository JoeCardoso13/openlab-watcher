# openLab Watcher

`openlab-watcher` watches [davidmalawey/openLab](https://github.com/davidmalawey/openLab) and emails small consistency notes when new commits introduce documentation issues.

It is intentionally narrow: it does not review code quality or style in general. It looks for practical documentation problems such as broken internal links, navigation/sidebar drift, filename inconsistency, orphaned pages, contradictory specs, and large changes that would be better split into clearer pages.

## How It Works

Every scheduled run:

1. Reads `state.json` to find the last upstream openLab commit it reviewed.
2. Asks the GitHub API for commits after that SHA.
3. Exits immediately if there are no new commits.
4. Fetches the diff, edited markdown files, nearby sibling filenames, sidebar/navbar files, and a compact repository tree.
5. Skips bulk changes above the configured file/byte thresholds.
6. Sends the change context to Claude using a forced structured-output tool.
7. Sends an email only if Claude returns `has_issues: true`.
8. Advances `state.json` and commits it back to this repository from GitHub Actions.

The scheduled workflow runs every four hours and can also be triggered manually from GitHub Actions.

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

`state.json` is the production cursor. It contains the last upstream openLab commit SHA that was reviewed. If you need to force a known commit to be reviewed again, set `last_seen_sha` to the upstream commit immediately before it, commit that state change, and manually run the workflow.

The run logs intentionally include minimal diagnostics:

```text
openlab-watcher: commits_found=...
openlab-watcher: llm_result has_issues=... findings=...
openlab-watcher: email_send_success to=d***@example.com
```

Recipient addresses are masked in logs. Secrets are never printed.

No Anthropic tokens are used when there are no new upstream commits. No email is sent unless Claude reports at least one finding.

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

## License

MIT. See `LICENSE`.
