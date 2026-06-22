"""Issue #78: the active queue panel shows a percentage for downloading rows
with a known total, and falls back to size-only when the total is unknown."""

from backend.app.routes.pages.templates import templates


def _render(rows) -> str:
    tmpl = templates.get_template("pages/_cache_queue_active.html")
    return tmpl.render(queue_active=rows)


def _row(**kw):
    base = {
        "id": 1, "provider_id": "catdv", "provider_clip_id": "5",
        "status": "downloading", "requested_at": "t", "started_at": "t",
        "error": None, "bytes_downloaded": 0, "bytes_total": 0,
        "clip_name": "clip.mov",
    }
    base.update(kw)
    return base


def test_downloading_with_total_shows_percentage():
    html = _render([_row(bytes_downloaded=12_000_000, bytes_total=27_000_000)])
    assert "44%" in html  # 12/27 -> 44
    assert "11.4 MB" in html  # 12_000_000 / 1048576


def test_downloading_without_total_shows_size_only():
    html = _render([_row(bytes_downloaded=5_000_000, bytes_total=0)])
    assert "%" not in html.split("queue-row")[1].split("</tr>")[0]


def test_done_row_unchanged_size_only():
    html = _render([_row(status="done", bytes_downloaded=27_000_000, bytes_total=27_000_000)])
    # done rows are not "downloading" → size only, no percentage cell text
    seg = html.split("queue-row")[1].split("</tr>")[0]
    assert "%" not in seg
