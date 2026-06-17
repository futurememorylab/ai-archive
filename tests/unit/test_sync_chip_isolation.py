"""The topbar sync chip is embedded on every page, so its drawer must read its
OWN namespaced context (`sync_rows` / `sync_counts`) and never a page's generic
`rows` / `counts`. Regression: a page with its own `rows` (e.g. /cache cache
entries) used to crash the embedded drawer's groupby. See ADR 0093."""

from backend.app.routes.pages.templates import templates


def test_embedded_chip_ignores_page_level_rows_and_counts():
    tmpl = templates.get_template("_sync_chip.html")
    # Simulate a host page that defines its own `rows` (page rows, no
    # provider_clip_id) and `counts` — the chip must not pick them up.
    html = tmpl.render(
        rows=[{"name": "cache entry", "size": 1}],
        counts={"unrelated": 7},
        request=None,
    )
    assert "No pending writes" in html  # drawer empty, not grouping page rows
    assert "cache entry" not in html  # page rows ignored
    # No sync_counts yet (initial topbar paint) → neutral loading placeholder,
    # NOT a premature "✓ Synced" that would flicker once real counts load.
    assert "sync-chip-loading" in html
    assert "sync-chip-ok" not in html
    assert "has-problems" not in html


def test_drawer_renders_real_rows_under_sync_rows():
    # Sanity: when the chip route supplies sync_rows, the drawer renders them.
    tmpl = templates.get_template("sync_drawer.html")
    html = tmpl.render(
        sync_rows=[
            {
                "provider_id": "catdv",
                "provider_clip_id": "9",
                "op_kind": "AppendNote",
                "status": "failed",
                "last_error": "boom",
                "clip_name": "Clip Nine",
            },
        ],
        request=None,
    )
    assert "Clip Nine" in html
    assert "Failed" in html
    assert "/api/sync/clip/catdv/9/retry" in html


def _pending_row():
    return {
        "provider_id": "catdv",
        "provider_clip_id": "9",
        "op_kind": "AppendNote",
        "status": "pending",
        "last_error": None,
        "clip_name": "Clip Nine",
    }


def test_drawer_shows_offline_note_when_offline_with_pending_writes():
    # Offline → the queue can't drain; the drawer explains the wait instead of a
    # bare, action-less "Queued".
    tmpl = templates.get_template("sync_drawer.html")
    html = tmpl.render(sync_rows=[_pending_row()], offline=True, request=None)
    assert "sync-offline-note" in html
    assert "offline" in html.lower()


def test_drawer_hides_offline_note_when_online():
    tmpl = templates.get_template("sync_drawer.html")
    html = tmpl.render(sync_rows=[_pending_row()], offline=False, request=None)
    assert "sync-offline-note" not in html


def test_drawer_hides_offline_note_when_no_pending_writes():
    # Offline but nothing queued → no note (and no rows).
    tmpl = templates.get_template("sync_drawer.html")
    html = tmpl.render(sync_rows=[], offline=True, request=None)
    assert "sync-offline-note" not in html
    assert "No pending writes" in html
