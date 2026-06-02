"""Static markup guards for the studio layout toggles. The minimise (-)
and restore (square) single-purpose buttons are replaced by the three
header toggles; guard both the removals and the additions."""

from pathlib import Path

HDR = Path("backend/app/templates/pages/_studio_header.html")
SP = Path("backend/app/templates/pages/_studio_player.html")
CARD = Path("backend/app/templates/pages/_studio_prompt_card.html")


def test_minimise_button_removed_from_studio_player():
    sp = SP.read_text()
    assert "studio-player-min" not in sp
    assert "minimizePlayer" not in sp


def test_restore_button_removed_from_header():
    hdr = HDR.read_text()
    assert "studio-show-player" not in hdr
    assert "restorePlayer" not in hdr


def test_header_has_three_layout_toggles():
    hdr = HDR.read_text()
    assert 'class="studio-layout-toggles"' in hdr
    for which in ("list", "player", "layout"):
        assert f'data-studio-toggle="{which}"' in hdr, f"missing {which} toggle"


def test_compare_button_available_in_both_layouts():
    # ADR 0051 made the `right` layout a three-column Player | cur | cmp
    # arrangement, so compare is supported there too. The `+ Compare` button
    # must therefore NOT be gated to the under-player layout — it shows
    # whenever a compare isn't already open, in either layout.
    card = CARD.read_text()
    assert 'x-show="compareVersionId === null"' in card, (
        "+ Compare must be shown whenever not already comparing (any layout)"
    )
    assert "layout === 'under'" not in card, (
        "+ Compare must no longer be gated to the under-player layout"
    )
