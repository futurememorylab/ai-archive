"""Repair compounding UTF-8 mojibake on CatDV marker text.

CatDV re-encodes some marker string fields (observed: ``category``) on every
``replaceMarkers`` write — it reads our UTF-8 bytes as cp1252 and stores the
result. Because CatDV replaces the markers array wholesale, every publish /
version-switch re-sends the clip's markers, so a field gains one mis-encoding
layer per write:

    "Interiér" -> "InteriÃ©r" -> "InteriÃÆ'Ã‚Â©r" -> ...

Left unchecked the string grows without bound until it overflows CatDV's
length-limited column and the write 500s ("Data too long"). We cannot fix
CatDV, so we make the round-trip self-correcting: we ``demojibake`` the value
we READ and the value we WRITE, so what we send is always the cleanest form.
CatDV can then add at most one layer, never a runaway.

``demojibake`` is deliberately conservative: it only touches strings that carry
the mojibake signature ("Ã"/"Â"), peels one layer at a time, and stops the
instant a peel is not valid UTF-8 — so clean text (and genuine non-mojibake
strings that merely contain "Ã") is returned unchanged.
"""

from __future__ import annotations

_MAX_LAYERS = 10
# Try latin-1 first (the observed corruption: 0x80-0x9F become invisible C1
# controls, the "ÃÂÃÂ" pattern) then cp1252 (the variant where those bytes
# become printable punctuation like "…"/"ƒ"). For a given layer exactly one of
# them re-encodes to valid UTF-8; the other raises and is skipped.
_PEEL_ENCODINGS = ("latin-1", "cp1252")


def _peel(s: str) -> str | None:
    """Undo ONE layer of UTF-8-bytes-misread-as-8bit. Returns None when no
    encoding yields valid UTF-8 — i.e. the string is already clean."""
    for enc in _PEEL_ENCODINGS:
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return None


def demojibake(value: str | None, *, max_layers: int = _MAX_LAYERS) -> str | None:
    """Undo repeated UTF-8→8-bit mis-decoding. No-op on clean / non-string input.

    Safe by construction: clean text (including legitimately-accented strings
    like "Interiér" or "São Paulo") does not re-encode to valid UTF-8, so the
    peel returns None and the original is returned unchanged. Only genuinely
    double-encoded text peels.
    """
    if not isinstance(value, str) or all(ord(c) <= 0x7F for c in value):
        return value
    cur = value
    for _ in range(max_layers):
        peeled = _peel(cur)
        if peeled is None or peeled == cur:
            break
        cur = peeled
    return cur


def demojibake_marker(raw: dict) -> dict:
    """Return a copy of a CatDV marker dict with its free-text fields repaired.

    Touches only the text fields that round-trip through CatDV's broken encoder;
    timecodes / colours are left untouched. Safe to call on already-clean dicts.
    """
    if not isinstance(raw, dict):
        return raw
    out = dict(raw)
    for key in ("name", "category", "description"):
        if key in out:
            out[key] = demojibake(out[key])
    return out
