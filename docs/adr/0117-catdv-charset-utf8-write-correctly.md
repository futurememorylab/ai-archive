# 0117. CatDV text encoding: declare charset=utf-8 on writes; delete the mojibake repair

**Date:** 2026-06-23
**Status:** Accepted

## Context

CatDV stored our writes as compounding UTF-8 mojibake: marker/field/note text
gained one mis-encoding layer per write (`"Interiér" → "InteriÃ©r" → …`) until it
overflowed a length-limited column and 500'd. `text_repair.py` worked around this
by `demojibake`-ing on both read and write so what we sent was always the
cleanest form and CatDV could add "at most one layer." A strict review flagged
that the repair covered markers only — notes and field values round-tripped raw
(finding #2) — and that the peel heuristic could over-correct (finding #5).

Rather than extend the repair to more fields, we asked whether we could **write
correctly** and delete the machinery. Investigation:

- **Offline:** our write path sent `Content-Type: application/json` with **no
  charset**, as UTF-8 bytes. A Java servlet decodes a charset-less body as
  ISO-8859-1 (its default). Decoding our real httpx JSON body as latin-1
  reproduces the documented mojibake exactly (`"Interiér" → "InteriÃ©r"`, Czech
  `"Záběr řeky" → "ZÃ¡bÄ\x9br Å\x99eky"`). Declaring `charset=utf-8` sends
  byte-identical content — it only tells CatDV how to decode.
- **Live (authorised single write):** wrote a clean Czech marker
  (`"CHARSETTEST STŘÍLENÍ žřč ĚŠČ"`, category `"Žřč"`) to a zero-marker clip with
  the charset header, read the **raw** value back (no demojibake in path): it was
  **byte-identical** — clean. A pre-existing clip (888700) read back mojibaked,
  confirming the corruption is real and historical.

## Alternatives

- **Pre-compensate** by encoding the body as cp1252 to match CatDV's misread.
  Rejected: Czech characters (ř, č, ž, ě…) are not in cp1252, so they cannot be
  encoded at all — the approach fails for this app's actual language.
- **Extend `demojibake` to notes/fields** (the review's #2 fix). Rejected: treats
  the symptom; the charset header fixes every field at the HTTP layer with no
  per-field code.
- **Adopt the review's #5 gate** (peel only on `Ã`/`Â`). Rejected: wrong for
  Czech — `ř` mojibakes to `Å™` (no `Ã`), so that gate would break legitimate
  repair. The current "peel only if it yields valid UTF-8" heuristic is correct.
- **Keep write-side repair as belt-and-suspenders.** Rejected: the live test
  confirms it is redundant; dead code is a maintenance cost (YAGNI).

## Decision

1. Declare `Content-Type: application/json; charset=utf-8` on every write in
   `CatdvClient._call_json` (one place; covers markers, notes and fields).
2. Delete the **entire** mojibake-repair machinery — the `text_repair` module
   (`demojibake` / `demojibake_marker`), its write-side call in
   `payload.build_put_payload`, and its read-side calls in
   `mapping.from_catdv_clip`. With charset, CatDV stores and returns clean UTF-8,
   so there is nothing to repair on either path.

There is **no production deployment yet**, so there is no legacy mojibaked data
worth preserving. Carrying read-side repair (and a future "republish-all" sweep)
purely for data that will not exist in production is unwarranted (YAGNI). The
cost: clips written mojibaked during pre-production dev display raw until they are
rewritten — acceptable, since none of that data is real.

## Consequences

- New writes are stored clean across markers, notes and fields — finding #2 is
  resolved at the HTTP layer with zero per-field code, and the overflow-500 class
  of bug is gone (no layer is ever added).
- **Zero encoding code remains** anywhere — read or write. The round-trip is
  correct by construction.
- Any clip mojibaked during pre-production dev now displays raw; rewriting it (any
  publish) stores it clean permanently. No production data is affected.
- The over-peel risk (#5) is moot — the peel heuristic is gone.
