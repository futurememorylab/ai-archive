"""Align two prompt-version bodies into a line-level compare model — the
Prompt-tab analogue of output_compare. Produces the SAME row shape (status +
word_diff segments), so the shared `_studio_compare_table.html` renders it;
the rows simply carry no timecodes. Pure, no I/O.

Hierarchical diff (the git / GitHub / track-changes approach): the bodies are
split into non-blank lines and aligned with `difflib.SequenceMatcher` to find
unchanged anchors (`equal`) plus the deleted / inserted / replaced regions
between them. Within a replaced region, lines are paired by *similarity* (not
by position) so a lightly-edited line lines up with its real counterpart and
shows a tight word-level diff, instead of a misleading wholesale red/green
block. The per-`changed`-row word diff (`word_diff`) highlights the in-line
edits.

We deliberately align on LINES rather than blank-line paragraphs: prompt
bodies don't use blank-line separators consistently (one version may collapse
several sections into a single block), which makes paragraph granularity align
badly. Line granularity is stable as long as the hard line breaks are
preserved across edits — reflowing/rewrapping a whole body would still
mis-align, which is the standard limitation of line diffs.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from backend.app.services.word_diff import word_diff

# Two lines in a replaced region are treated as one `changed` pair when their
# character-similarity ratio clears this bar; below it they read better as a
# separate removed + added pair than as a "changed" of unrelated text.
_PAIR_THRESHOLD = 0.5


def _split_lines(body: str | None) -> list[str]:
    if not body:
        return []
    return [ln.strip() for ln in body.splitlines() if ln.strip()]


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def _line_row(
    idx: int, status: str, cmp_text: str | None, cur_text: str | None
) -> dict[str, Any]:
    # Skip the O(n·m) word diff for unchanged lines — the result is just a
    # single "eq" segment, which the template renders identically to the text.
    if status == "unchanged":
        segs: list[dict[str, Any]] = [{"type": "eq", "text": cmp_text or ""}]
    else:
        segs = word_diff(cmp_text or "", cur_text or "")
    return {
        "key": f"line-{idx}",
        "status": status,
        # Presence dicts only — no tc/in_secs, so the shared table renders the
        # diff text without a timecode line or seek affordance. The `text` keeps
        # the dict truthy for the template's `{% if row.cmp %}` check.
        "cmp": {"text": cmp_text} if cmp_text is not None else None,
        "cur": {"text": cur_text} if cur_text is not None else None,
        "segs": segs,
        "time_changed": False,
    }


def _pair_replaced(
    cmp_block: list[str], cur_block: list[str]
) -> list[tuple[str, str | None, str | None]]:
    """Align the lines of one replaced region by similarity, preserving order.
    Returns (status, cmp_text|None, cur_text|None) tuples: `changed` pairs the
    corresponding old/new line, while an old/new line whose real match sits
    further along is emitted as `removed`/`added` so the rest realigns (handles
    a line inserted or dropped mid-edit)."""
    out: list[tuple[str, str | None, str | None]] = []
    i = j = 0
    n, m = len(cmp_block), len(cur_block)
    while i < n and j < m:
        a, b = cmp_block[i], cur_block[j]
        if _ratio(a, b) >= _PAIR_THRESHOLD:
            out.append(("changed", a, b))
            i += 1
            j += 1
            continue
        # a and b aren't a good pair here. If a matches a later new paragraph
        # better than b matches a later old one, b is an insertion (emit added);
        # the mirror case is a deletion (emit removed). Otherwise treat it as a
        # straight in-place replacement.
        a_future = max((_ratio(a, cur_block[jj]) for jj in range(j + 1, m)), default=0.0)
        b_future = max((_ratio(cmp_block[ii], b) for ii in range(i + 1, n)), default=0.0)
        if a_future >= _PAIR_THRESHOLD and a_future >= b_future:
            out.append(("added", None, b))
            j += 1
        elif b_future >= _PAIR_THRESHOLD:
            out.append(("removed", a, None))
            i += 1
        else:
            out.append(("changed", a, b))
            i += 1
            j += 1
    out.extend(("removed", cmp_block[k], None) for k in range(i, n))
    out.extend(("added", None, cur_block[k]) for k in range(j, m))
    return out


def build_prompt_compare(cur_body: str | None, cmp_body: str | None) -> dict[str, Any]:
    """Line-aligned compare model for two prompt bodies (cur = newer)."""
    cmp_lines = _split_lines(cmp_body)
    cur_lines = _split_lines(cur_body)
    rows: list[dict] = []
    sm = SequenceMatcher(None, cmp_lines, cur_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append(_line_row(len(rows), "unchanged", cmp_lines[i1 + k],
                                      cur_lines[j1 + k]))
        elif tag == "delete":
            for ln in cmp_lines[i1:i2]:
                rows.append(_line_row(len(rows), "removed", ln, None))
        elif tag == "insert":
            for ln in cur_lines[j1:j2]:
                rows.append(_line_row(len(rows), "added", None, ln))
        else:  # replace — pair the region's lines by similarity
            for status, ct, ut in _pair_replaced(cmp_lines[i1:i2], cur_lines[j1:j2]):
                rows.append(_line_row(len(rows), status, ct, ut))
    return {"scene_count": len(rows), "scenes": rows, "fields": [], "notes": None}
