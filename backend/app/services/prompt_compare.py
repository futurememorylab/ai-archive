"""Align two prompt-version bodies into a paragraph-level compare model — the
Prompt-tab analogue of output_compare. Produces the SAME row shape (status +
word_diff segments), so the shared `_studio_compare_table.html` renders it;
paragraph rows simply carry no timecodes. Pure, no I/O.

Paragraphs are blank-line-separated blocks; they are LCS-aligned, and runs of
deletions/insertions between matched paragraphs are paired into `changed` rows
(leftovers become `removed` / `added`). The word diff inside a `changed` row
highlights the intra-paragraph edits.
"""

from __future__ import annotations

import re
from typing import Any

from backend.app.services.word_diff import lcs_ops, word_diff


def _split_paragraphs(body: str | None) -> list[str]:
    if not body:
        return []
    parts = re.split(r"\n[ \t]*\n", body.strip())
    return [p.strip() for p in parts if p.strip()]


def _para_row(
    idx: int, status: str, cmp_text: str | None, cur_text: str | None
) -> dict[str, Any]:
    # Skip the O(n·m) word diff for unchanged paragraphs — the result is just a
    # single "eq" segment, which the template renders identically to the text.
    if status == "unchanged":
        segs: list[dict[str, Any]] = [{"type": "eq", "text": cmp_text or ""}]
    else:
        segs = word_diff(cmp_text or "", cur_text or "")
    return {
        "key": f"para-{idx}",
        "status": status,
        # Presence dicts only — no tc/in_secs, so the shared table renders the
        # diff text without a timecode line or seek affordance. The `text` keeps
        # the dict truthy for the template's `{% if row.cmp %}` check.
        "cmp": {"text": cmp_text} if cmp_text is not None else None,
        "cur": {"text": cur_text} if cur_text is not None else None,
        "segs": segs,
        "time_changed": False,
    }


def build_prompt_compare(cur_body: str | None, cmp_body: str | None) -> dict[str, Any]:
    """Paragraph-aligned compare model for two prompt bodies (cur = newer)."""
    ops = lcs_ops(_split_paragraphs(cmp_body), _split_paragraphs(cur_body))
    rows: list[dict] = []
    pend_del: list[str] = []
    pend_ins: list[str] = []

    def flush() -> None:
        # Pair deletions with insertions positionally -> changed rows; any
        # leftover on one side becomes a removed / added row.
        k = 0
        while k < len(pend_del) and k < len(pend_ins):
            rows.append(_para_row(len(rows), "changed", pend_del[k], pend_ins[k]))
            k += 1
        for d in pend_del[k:]:
            rows.append(_para_row(len(rows), "removed", d, None))
        for a in pend_ins[k:]:
            rows.append(_para_row(len(rows), "added", None, a))
        pend_del.clear()
        pend_ins.clear()

    for typ, a, b in ops:
        if typ == "eq":
            flush()
            rows.append(_para_row(len(rows), "unchanged", a, b))
        elif typ == "del":
            pend_del.append(a)  # type: ignore[arg-type]
        else:
            pend_ins.append(b)  # type: ignore[arg-type]
    flush()
    return {"scene_count": len(rows), "scenes": rows, "fields": [], "notes": None}
