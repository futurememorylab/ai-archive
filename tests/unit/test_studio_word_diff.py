"""Python mirror of the JS wordDiff function. Tests pin the algorithm
shape; the JS version (backend/app/static/studio-diff.js) is a
character-for-character port of this implementation and shares these
fixtures.

Word-level inline diff (Word track-changes style): the text is tokenized
into words + whitespace runs, LCS-aligned, and the per-token ops are
coalesced into runs. Insertions render green, deletions render red +
strikethrough, equal text renders plain — all in one flowing block.
"""

import re
from typing import Any


def _tokenize(s: str) -> list[str]:
    """Split into word + whitespace tokens, preserving everything so the
    text is reconstructable by concatenation. Empty pieces are dropped."""
    if not s:
        return []
    return [t for t in re.split(r"(\s+)", s) if t != ""]


def word_diff(a_text: str, b_text: str) -> list[dict[str, Any]]:
    """LCS-aligned word diff from a_text (old) to b_text (new). Returns a
    list of coalesced segments:
      {"type": "eq",  "text": <unchanged text>}
      {"type": "del", "text": <removed text>}   # only in a_text (old)
      {"type": "ins", "text": <added text>}      # only in b_text (new)
    """
    A = _tokenize(a_text)
    B = _tokenize(b_text)
    n, m = len(A), len(B)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if A[i] == B[j]:
                lcs[i][j] = lcs[i + 1][j + 1] + 1
            else:
                lcs[i][j] = max(lcs[i + 1][j], lcs[i][j + 1])
    ops: list[tuple[str, str]] = []
    i = j = 0
    while i < n and j < m:
        if A[i] == B[j]:
            ops.append(("eq", A[i]))
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            ops.append(("del", A[i]))
            i += 1
        else:
            ops.append(("ins", B[j]))
            j += 1
    while i < n:
        ops.append(("del", A[i]))
        i += 1
    while j < m:
        ops.append(("ins", B[j]))
        j += 1
    # Coalesce adjacent ops of the same type into one segment.
    segs: list[dict[str, Any]] = []
    for typ, text in ops:
        if segs and segs[-1]["type"] == typ:
            segs[-1]["text"] += text
        else:
            segs.append({"type": typ, "text": text})
    return segs


def _reconstruct(segs, want_old):
    """Old text = eq+del; new text = eq+ins. Concatenation round-trips."""
    keep = {"eq", "del"} if want_old else {"eq", "ins"}
    return "".join(s["text"] for s in segs if s["type"] in keep)


def test_empty_inputs_produce_no_segments():
    assert word_diff("", "") == []


def test_handles_none_safely():
    assert word_diff(None, None) == []  # type: ignore[arg-type]


def test_identical_text_is_one_eq_segment():
    text = "the quick brown fox"
    segs = word_diff(text, text)
    assert segs == [{"type": "eq", "text": text}]


def test_single_word_change_is_inline_del_then_ins():
    segs = word_diff("the quick brown fox", "the quick red fox")
    assert segs == [
        {"type": "eq", "text": "the quick "},
        {"type": "del", "text": "brown"},
        {"type": "ins", "text": "red"},
        {"type": "eq", "text": " fox"},
    ]


def test_pure_insertion_is_green_only():
    segs = word_diff("hello world", "hello brave world")
    types = [s["type"] for s in segs]
    assert "del" not in types
    assert {"type": "ins", "text": "brave "} in segs


def test_pure_deletion_is_red_only():
    segs = word_diff("hello brave world", "hello world")
    types = [s["type"] for s in segs]
    assert "ins" not in types
    assert {"type": "del", "text": "brave "} in segs


def test_segments_roundtrip_to_old_and_new():
    a = "alpha beta gamma delta"
    b = "alpha BETA gamma epsilon delta"
    segs = word_diff(a, b)
    assert _reconstruct(segs, want_old=True) == a
    assert _reconstruct(segs, want_old=False) == b


def test_newlines_are_preserved_in_tokens():
    a = "line one\nline two"
    b = "line one\nline 2"
    segs = word_diff(a, b)
    # The newline is part of an equal whitespace token, not lost.
    assert _reconstruct(segs, want_old=True) == a
    assert _reconstruct(segs, want_old=False) == b
    assert any("\n" in s["text"] for s in segs if s["type"] == "eq")
