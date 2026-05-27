"""Python mirror of the JS lineDiff function. Tests pin the algorithm
shape; the JS version (backend/app/static/studio-diff.js) is a
character-for-character port of this implementation and shares these
fixtures."""

from typing import Any


def line_diff(a_text: str, b_text: str) -> list[dict[str, Any]]:
    """LCS-aligned line diff. Output rows are dicts with:
      {"type": "eq", "a": <line>, "b": <line>}
      {"type": "del", "a": <line>}
      {"type": "ins", "b": <line>}
    """
    A = (a_text or "").split("\n")
    B = (b_text or "").split("\n")
    n, m = len(A), len(B)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if A[i] == B[j]:
                lcs[i][j] = lcs[i + 1][j + 1] + 1
            else:
                lcs[i][j] = max(lcs[i + 1][j], lcs[i][j + 1])
    out: list[dict[str, Any]] = []
    i = j = 0
    while i < n and j < m:
        if A[i] == B[j]:
            out.append({"type": "eq", "a": A[i], "b": B[j]}); i += 1; j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            out.append({"type": "del", "a": A[i]}); i += 1
        else:
            out.append({"type": "ins", "b": B[j]}); j += 1
    while i < n:
        out.append({"type": "del", "a": A[i]}); i += 1
    while j < m:
        out.append({"type": "ins", "b": B[j]}); j += 1
    return out


def test_empty_inputs_produce_empty_output():
    rows = line_diff("", "")
    assert rows == [{"type": "eq", "a": "", "b": ""}]


def test_identical_text_is_all_eq():
    text = "a\nb\nc"
    rows = line_diff(text, text)
    assert all(r["type"] == "eq" for r in rows)
    assert [r["a"] for r in rows] == ["a", "b", "c"]


def test_all_insert():
    rows = line_diff("", "x\ny")
    types = [r["type"] for r in rows]
    assert types == ["del", "ins", "ins"]
    assert rows[0]["a"] == ""
    assert [r["b"] for r in rows[1:]] == ["x", "y"]


def test_all_delete():
    rows = line_diff("x\ny", "")
    types = [r["type"] for r in rows]
    assert types == ["del", "del", "ins"]
    assert [r["a"] for r in rows[:2]] == ["x", "y"]
    assert rows[2]["b"] == ""


def test_interleaved():
    a = "a\nb\nc\nd"
    b = "a\nX\nc\nd"
    rows = line_diff(a, b)
    assert [r["type"] for r in rows] == ["eq", "del", "ins", "eq", "eq"]
    assert rows[0]["a"] == "a"
    assert rows[1]["a"] == "b"
    assert rows[2]["b"] == "X"
    assert rows[3]["a"] == "c" and rows[3]["b"] == "c"
    assert rows[4]["a"] == "d" and rows[4]["b"] == "d"


def test_handles_none_safely():
    rows = line_diff(None, None)  # type: ignore[arg-type]
    assert rows == [{"type": "eq", "a": "", "b": ""}]
