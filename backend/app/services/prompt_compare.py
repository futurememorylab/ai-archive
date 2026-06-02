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

from backend.app.services.word_diff import word_diff


def _split_paragraphs(body: str | None) -> list[str]:
    if not body:
        return []
    parts = re.split(r"\n[ \t]*\n", body.strip())
    return [p.strip() for p in parts if p.strip()]


def _para_row(
    idx: int, status: str, cmp_text: str | None, cur_text: str | None
) -> dict[str, Any]:
    return {
        "key": f"para-{idx}",
        "status": status,
        # Presence dicts only — no tc/in_secs, so the shared table renders the
        # diff text without a timecode line or seek affordance.
        "cmp": {"text": cmp_text} if cmp_text is not None else None,
        "cur": {"text": cur_text} if cur_text is not None else None,
        "segs": word_diff(cmp_text or "", cur_text or ""),
        "time_changed": False,
    }


def _diff_paragraphs(
    cmp: list[str], cur: list[str]
) -> list[tuple[str, str | None, str | None]]:
    """LCS over paragraph lists -> ops [(eq|del|ins, cmp_para, cur_para)]."""
    n, m = len(cmp), len(cur)
    lcs = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            lcs[i][j] = (
                lcs[i + 1][j + 1] + 1
                if cmp[i] == cur[j]
                else max(lcs[i + 1][j], lcs[i][j + 1])
            )
    ops: list[tuple[str, str | None, str | None]] = []
    i = j = 0
    while i < n and j < m:
        if cmp[i] == cur[j]:
            ops.append(("eq", cmp[i], cur[j]))
            i += 1
            j += 1
        elif lcs[i + 1][j] >= lcs[i][j + 1]:
            ops.append(("del", cmp[i], None))
            i += 1
        else:
            ops.append(("ins", None, cur[j]))
            j += 1
    while i < n:
        ops.append(("del", cmp[i], None))
        i += 1
    while j < m:
        ops.append(("ins", None, cur[j]))
        j += 1
    return ops


def build_prompt_compare(cur_body: str | None, cmp_body: str | None) -> dict[str, Any]:
    """Paragraph-aligned compare model for two prompt bodies (cur = newer)."""
    ops = _diff_paragraphs(_split_paragraphs(cmp_body), _split_paragraphs(cur_body))
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
