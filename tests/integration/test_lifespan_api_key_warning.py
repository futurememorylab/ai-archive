"""Startup logs how Live audio authenticates.

The raw GEMINI_API_KEY is NO LONGER shipped to the browser: Live sessions
mint short-lived, config-bound ephemeral tokens server-side (ADR 0112,
supersedes 0043). So boot logs the secure posture when the key is present,
and warns only when the key is missing (Live audio is then unavailable)."""

import logging
from unittest.mock import MagicMock

from backend.app.startup import log_live_token_mode


def test_logs_ephemeral_mode_when_key_set(caplog):
    settings = MagicMock()
    settings.gemini_api_key = "AIza-redacted"
    caplog.set_level(logging.INFO)
    log_live_token_mode(settings)
    # Confirms the secure mode at INFO — and crucially does NOT warn about
    # exposure, because there is none anymore.
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("ephemeral" in r.message.lower() for r in infos), caplog.records
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("exposed" in r.message.lower() for r in warnings)


def test_warns_when_gemini_api_key_unset(caplog):
    settings = MagicMock()
    settings.gemini_api_key = None
    caplog.set_level(logging.WARNING)
    log_live_token_mode(settings)
    relevant = [r for r in caplog.records if "GEMINI_API_KEY" in r.message]
    assert relevant, f"expected a missing-key warning: {caplog.records}"
    assert relevant[0].levelno == logging.WARNING
