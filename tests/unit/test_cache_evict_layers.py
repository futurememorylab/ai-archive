"""Guards for the cache-deletion fixes.

Two bugs these pin against regressing:
  1. The clip-page "clear cache" must remove BOTH media layers (local + AI),
     not just the local proxy — otherwise an AI-store copy lingers and the
     clip is still treated as cached.
  2. The cache-management page "Purge selected" must evict the layer matching
     the active tab (AI tab → media-ai), not a hardcoded media-local that
     silently no-ops on the AI tab.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "backend" / "app" / "templates" / "pages"
TPL_ROOT = ROOT / "backend" / "app" / "templates"
STATIC = ROOT / "backend" / "app" / "static"


def test_clip_page_purge_clears_both_media_layers():
    js = (STATIC / "cacheActions.js").read_text()
    # purge() must send both media layers in one bulk-evict call.
    assert "'media-local', 'media-ai'" in js or "'media-local','media-ai'" in js


def test_clip_cache_actions_clear_button_appears_when_either_media_present():
    # In local dev a clip can hold a local proxy AND an AI-store copy; the
    # clear button must show (and clear both) whenever either is present — so
    # an AI-only leftover (local already evicted) is still purgeable here.
    html = (TPL / "_cache_actions.html").read_text()
    # The local-mode branch shows the clear button when EITHER media layer is
    # present (an OR), so an AI-only leftover is purgeable.
    assert "media_local.present or clip.cache.media_ai.present" in html
    # And the button delegates to purge() (which clears both layers).
    assert 'purge()">Purge cache' in html


def test_cache_page_bulk_evict_is_tab_aware():
    html = (TPL_ROOT / "cache_page.html").read_text()
    # The request body sends the tab-derived layers, not a hardcoded array.
    assert "layers: plan.layers" in html
    # Derives the layer from the active tab (read live from the URL) and can
    # target the AI layer.
    assert "URLSearchParams" in html
    assert "['media-ai']" in html


def test_cache_page_passes_tab_or_reads_it_at_click_time():
    html = (TPL_ROOT / "cache_page.html").read_text()
    # The tab must be resolved at click time (URL), not frozen at render —
    # tab switches are HTMX swaps of the table only, leaving cacheSel mounted.
    assert "location.search" in html
