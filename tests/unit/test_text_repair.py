"""Tests for CatDV marker mojibake repair (backend/app/archive/providers/catdv/text_repair.py)."""

import pytest

from backend.app.archive.providers.catdv.text_repair import demojibake, demojibake_marker


def _mangle(s: str, layers: int) -> str:
    """Reproduce CatDV's corruption: our UTF-8 bytes read back as latin-1,
    `layers` times over (one layer per write/round-trip). latin-1 maps all 256
    bytes, matching the observed "ÃÂÃÂ…©" pattern (invisible C1 controls)."""
    for _ in range(layers):
        s = s.encode("utf-8").decode("latin-1")
    return s


@pytest.mark.parametrize("layers", [1, 2, 3, 4, 6])
@pytest.mark.parametrize("clean", ["Interiér", "Exteriér", "Měsíc", "Portrét", "Detail — celek"])
def test_demojibake_inverts_n_layers(clean, layers):
    assert demojibake(_mangle(clean, layers)) == clean


def test_demojibake_leaves_clean_text_untouched():
    for clean in ["Interiér", "Žena s krátkými vlasy", "Detail", "", "ABC 123"]:
        assert demojibake(clean) == clean


def test_demojibake_handles_none_and_non_str():
    assert demojibake(None) is None
    assert demojibake(123) == 123  # type: ignore[arg-type]


def test_demojibake_does_not_corrupt_genuine_non_mojibake_with_tilde_A():
    # "São Paulo" contains 'Ã' but is NOT mojibake: re-encoding to cp1252 and
    # decoding as UTF-8 is not a valid peel, so it must be returned unchanged.
    assert demojibake("São Paulo") == "São Paulo"


def test_demojibake_is_idempotent():
    once = demojibake(_mangle("Interiér", 5))
    assert demojibake(once) == once == "Interiér"


def test_demojibake_marker_repairs_text_fields_only():
    raw = {
        "name": _mangle("Žena", 2),
        "category": _mangle("Interiér", 3),
        "description": _mangle("Detail", 1),
        "in": {"frm": 100, "secs": 4.0, "fmt": 25.0},  # untouched
        "color": "#ff0000",
    }
    out = demojibake_marker(raw)
    assert out["name"] == "Žena"
    assert out["category"] == "Interiér"
    assert out["description"] == "Detail"
    assert out["in"] == {"frm": 100, "secs": 4.0, "fmt": 25.0}
    assert out["color"] == "#ff0000"
    # original not mutated
    assert raw["category"] != "Interiér"
