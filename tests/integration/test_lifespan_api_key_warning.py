"""Startup logs a WARNING when GEMINI_API_KEY is configured, because the
key is shipped to the browser by live_sessions.mint_ephemeral_token.
Surfaces an accepted risk (see ADR 0043) that today is documented only
in a code comment."""

import logging
from unittest.mock import MagicMock

from backend.app.startup import warn_browser_secret_exposure


def test_warning_fires_when_gemini_api_key_set(caplog):
    settings = MagicMock()
    settings.gemini_api_key = "AIza-redacted"
    caplog.set_level(logging.WARNING)
    warn_browser_secret_exposure(settings)
    relevant = [r for r in caplog.records if "GEMINI_API_KEY" in r.message]
    assert relevant, f"no GEMINI_API_KEY warning in records: {caplog.records}"
    assert relevant[0].levelno == logging.WARNING


def test_no_warning_when_gemini_api_key_unset(caplog):
    settings = MagicMock()
    settings.gemini_api_key = None
    caplog.set_level(logging.WARNING)
    warn_browser_secret_exposure(settings)
    relevant = [r for r in caplog.records if "GEMINI_API_KEY" in r.message]
    assert not relevant
