"""The headline pill shows the live version even when a draft is pending.

A pending draft is flagged by a dot on the Draft scope button instead, so the
pill never reads "Draft – unpublished" (it hid the live version and read as if
the whole clip were unpublished)."""

from backend.app.routes.pages.clips import _publish_pill


def test_draft_with_live_version_shows_live():
    assert _publish_pill("draft", 3) == ("Live v3", "ok")


def test_draft_without_live_version_shows_no_pill():
    assert _publish_pill("draft", None)[0] == ""


def test_other_states_unchanged():
    assert _publish_pill("live", 2) == ("Live v2", "ok")
    assert _publish_pill("failed", 2) == ("Failed", "bad")
    assert _publish_pill("publishing", None) == ("Publishing…", "accent")
