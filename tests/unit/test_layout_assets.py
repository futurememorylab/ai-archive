from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_LAYOUT = _ROOT / "backend" / "app" / "templates" / "pages" / "layout.html"
_STATIC = _ROOT / "backend" / "app" / "static"


def test_layout_does_not_reference_unpkg():
    html = _LAYOUT.read_text(encoding="utf-8")
    assert "unpkg.com" not in html


def test_layout_references_local_js_vendor():
    html = _LAYOUT.read_text(encoding="utf-8")
    assert "/static/vendor/htmx.min.js" in html
    assert "/static/vendor/alpine.min.js" in html


def test_htmx_loads_before_alpine():
    html = _LAYOUT.read_text(encoding="utf-8")
    assert html.index("htmx.min.js") < html.index("alpine.min.js")


def test_vendored_js_exists_and_nonempty():
    for rel in ("vendor/htmx.min.js", "vendor/alpine.min.js"):
        p = _STATIC / rel
        assert p.exists(), f"missing vendored asset: {rel}"
        assert p.stat().st_size > 1024, f"vendored asset too small: {rel}"
