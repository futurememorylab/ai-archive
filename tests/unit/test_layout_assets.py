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


_CSS = _STATIC / "app.css"


def test_layout_does_not_reference_google_fonts():
    html = _LAYOUT.read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_css_self_hosts_fonts():
    css = _CSS.read_text(encoding="utf-8")
    assert "@font-face" in css
    assert "/static/vendor/fonts/inter-latin-wght-normal.woff2" in css
    assert "/static/vendor/fonts/jetbrains-mono-latin-wght-normal.woff2" in css


def test_vendored_fonts_exist_and_nonempty():
    for rel in (
        "vendor/fonts/inter-latin-wght-normal.woff2",
        "vendor/fonts/jetbrains-mono-latin-wght-normal.woff2",
    ):
        p = _STATIC / rel
        assert p.exists(), f"missing vendored font: {rel}"
        assert p.stat().st_size > 1024, f"vendored font too small: {rel}"
