"""Tests for backend.app.services.word_diff.word_diff (the authoritative
Python implementation; backend/app/static/studio-diff.js mirrors it for
the live client-side diff).

Word-level inline diff (Word track-changes style): the text is tokenized
into words + whitespace runs, LCS-aligned, and the per-token ops are
coalesced into runs. Insertions render green, deletions render red +
strikethrough, equal text renders plain — all in one flowing block.
"""

from backend.app.services.word_diff import word_diff


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
