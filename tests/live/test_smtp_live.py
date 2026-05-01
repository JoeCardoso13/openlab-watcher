"""Live SMTP smoke test. Enable with RUN_LIVE_TESTS=1."""
import os

import pytest

from scripts import check


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_TESTS") != "1",
    reason="set RUN_LIVE_TESTS=1 to run live smoke tests",
)


def test_smtp_live_sends_to_configured_recipient():
    recipient = os.environ["LIVE_RECIPIENT"]

    check.send_email(
        check.SMTP_HOST,
        check.SMTP_PORT,
        os.environ["SMTP_USER"],
        os.environ["SMTP_PASSWORD"],
        recipient,
        "openlab-watcher live smoke test",
        "This is a live SMTP smoke test from openlab-watcher.\n",
    )
