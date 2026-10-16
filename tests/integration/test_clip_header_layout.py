"""Clip page header + topbar layout (decluttered 2026-10-16).

The topbar no longer carries the spend pill, the "N to review" pill or an
idle "✓ Synced" chip; the sync chip only shows while writes are publishing or
failed, so the Sync drawer stays reachable when it matters. The clip header
drops its duplicate timecode readout (the player HUD and transport keep one)
and puts its actions on the right: an Annotate button plus a ⋯ menu holding
the cache control (Cache / Purge) and Live.
"""

from fastapi.testclient import TestClient

from tests._helpers.live_ctx import install_live_ctx
from tests.integration.test_clip_detail_draft import FakeArchive, _canonical, _make_app


def _page(monkeypatch, tmp_path) -> str:
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, archive=FakeArchive((_canonical(101),)))
        r = client.get("/clips/101")
        assert r.status_code == 200
        return r.text


def test_topbar_drops_spend_review_and_synced_pills(monkeypatch, tmp_path):
    html = _page(monkeypatch, tmp_path)
    topbar = html.split('class="topbar"', 1)[1].split("</header>", 1)[0]
    assert 'id="usage-pill"' not in topbar
    assert 'id="review-pill"' not in topbar
    assert "sync-chip-ok" not in topbar  # no idle "✓ Synced"
    assert 'id="sync-chip"' in topbar  # container stays as the swap target


def test_clip_header_actions_on_the_right_with_overflow_menu(monkeypatch, tmp_path):
    html = _page(monkeypatch, tmp_path)
    row = html.split('class="detail-hdr-row"', 1)[1].split('class="live-bar"', 1)[0]
    assert "tc-readout" not in row
    left, right = row.split('class="grow"', 1)
    assert "cache-ctl" not in left
    assert 'data-test="clip-more"' in right
    more = right.split('data-test="clip-more"', 1)[1]
    assert "cache-ctl" in more  # Cache / Purge lives in the ⋯ menu
    assert "liveSession.start()" in more  # Live too
