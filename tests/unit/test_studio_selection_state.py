# tests/unit/test_studio_selection_state.py
"""Studio selection state — the store exposes selection methods used by the
navigator checkboxes and the bulk-run bar."""

from pathlib import Path

SRC = Path("backend/app/static/studioStore.js").read_text()


def test_store_declares_selection_api():
    for token in (
        "selectedClipIds",
        "toggleClip(",
        "toggleSet(",
        "clearSelection(",
        "setFullySelected(",
        "setBadge(",
        "runOnSelectedClips(",
        "_runOne(",
    ):
        assert token in SRC, token


def test_bulk_run_bounded_concurrency_constant():
    # The bulk loop must cap in-flight runs (seat/quota protection).
    assert "BULK_RUN_CONCURRENCY" in SRC
