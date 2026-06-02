"""LCS-aligned word-level inline diff (Word track-changes style).

Tokenizes into word + whitespace runs, LCS-aligns, and coalesces adjacent
same-type ops into segments {"type": "eq"|"ins"|"del", "text": ...}. This is
the authoritative implementation; tests/unit/test_studio_word_diff.py pins its
shape, and backend/app/static/studio-diff.js mirrors it for the live
client-side Prompt diff (keep the two in sync).
"""

from __future__ import annotations

import html as _html
import re
from typing import Any, Literal

from markupsafe import Markup


def tokenize(s: str | None) -> list[str]:
    """Split into word + whitespace tokens, preserving everything so the text
    is reconstructable by concatenation. Empty pieces are dropped."""
    if not s:
        return []
    return [t for t in re.split(r"(\s+)", s) if t != ""]


def word_diff(a_text: str | None, b_text: str | None) -> list[dict[str, Any]]:
    """LCS word diff from a_text (old) to b_text (new). Coalesced segments:
    {"type": "eq", ...} unchanged, {"type": "del", ...} only in old,
    {"type": "ins", ...} only in new."""
    A = tokenize(a_text)
    B = tokenize(b_text)
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
    segs: list[dict[str, Any]] = []
    for typ, text in ops:
        if segs and segs[-1]["type"] == typ:
            segs[-1]["text"] += text
        else:
            segs.append({"type": typ, "text": text})
    return segs


Side = Literal["left", "right", "both"]


def diff_html(segs: list[dict[str, Any]] | None, side: Side = "both") -> Markup:
    """Render coalesced segments to escaped HTML with <ins>/<del> wrappers.

    side="left"  -> eq + del   (older text; deletions struck red)
    side="right" -> eq + ins   (newer text; insertions green)
    side="both"  -> eq + ins + del (one flowing block, e.g. notes)
    Returns Markup so templates need no `| safe`.
    """
    if side == "left":
        keep = {"eq", "del"}
    elif side == "right":
        keep = {"eq", "ins"}
    else:
        keep = {"eq", "ins", "del"}
    out: list[str] = []
    for s in segs or []:
        if s["type"] not in keep:
            continue
        t = _html.escape(s["text"])
        if s["type"] == "ins":
            out.append(f'<ins class="diff-ins">{t}</ins>')
        elif s["type"] == "del":
            out.append(f'<del class="diff-del">{t}</del>')
        else:
            out.append(t)
    return Markup("".join(out))
