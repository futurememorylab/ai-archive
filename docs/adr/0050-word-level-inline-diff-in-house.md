# 0050. Studio compare uses a word-level inline diff (Word track-changes), extended in-house

**Date:** 2026-06-02
**Status:** Accepted
**Lifespan:** Feature

## Context

The Studio compare view rendered a two-column, **line-level** diff
(`_studio_diff.html` table + `studio-diff.js::lineDiff`, mirrored by
`tests/unit/test_studio_line_diff.py`). A line was either equal, fully
removed, or fully added, shown side-by-side. For prose prompt bodies this is
coarse — a one-word edit lit up the whole line on both sides, and the
side-by-side layout doesn't read like the "track changes" view users expect.

The ask: make it look like Word — one flowing block, **new text green,
removed text red and struck through**, with changes marked at the *word*
level inline. The referenced example (`html-diff.lix.dev`) is a word-level
inline diff.

## Alternatives

1. **Vendor the html-diff (lix.dev) library** — npm/ESM package; diffs two
   HTML strings → marked-up HTML. Powerful, but it's a Node-toolchain
   dependency and a second diff engine.
2. **Vendor a precompiled single-file diff lib** (e.g. jsdiff
   `dist/diff.min.js`, used like the already-vendored `alpine.min.js`) and
   call `Diff.diffWords()`.
3. **Extend the in-house LCS diff to word granularity** — tokenize by words
   + whitespace instead of lines, reuse the existing LCS, emit inline
   `<ins>`/`<del>`. No new dependency.

## Decision

Chose **alternative 3**. ADR 0001 commits us to a Python-only stack with no
Node frontend; both library options add a frontend dependency (1 was
explicitly rejected by the user on those grounds). The existing diff is a
small LCS that already has a Python mirror test, so generalizing it from
lines to word tokens is ~40 lines and keeps the testable, dependency-free
shape.

- `studio-diff.js`: `lineDiff` → `wordDiff`. Tokenize via `split(/(\s+)/)`
  (words + whitespace runs, reconstructable by concatenation), run the same
  LCS, then **coalesce** adjacent same-type ops into segments
  `{type: eq|ins|del, text}`. A new `renderDiffHtml` escapes the text and
  wraps `ins`/`del` segments in `<ins class="diff-ins">` / `<del class="diff-del">`.
- The diff is computed **old → new = cmp → cur**: text only in the cur
  (active/target) version is a green insertion; text only in the cmp
  (baseline) version is a red struck-through deletion.
- `cmpDiff` now exposes `html` (injected via `x-html`), `changes`
  (non-equal segment count), and `hasContent`, replacing the `rows` table.
  `_studio_diff.html` renders one flowing `.pc-diff-body` block instead of
  the two-column `.pc-diff-table`.
- CSS: `.diff-ins` = `var(--good)` (green) + underline; `.diff-del` =
  `var(--bad)` (red) + line-through; the block is `white-space: pre-wrap`
  so source line breaks survive.
- The Python mirror moves to `tests/unit/test_studio_word_diff.py`
  (`word_diff`), staying the authoritative spec; the JS is a
  character-for-character port (verified by running both over shared
  fixtures).

## Consequences

- Edits are visible at word granularity in a single Word-style view; a
  one-word change marks only that word, not the whole line.
- `x-html` is used for the diff body. This is safe because the text is
  HTML-escaped before our own `<ins>`/`<del>` wrappers are added — no
  untrusted markup is injected.
- Builds on ADR 0049's diff fixes: the body still comes from the saved
  `baseline` (not the live buffer) and still refreshes on `savedTick` /
  the reactive `$store.studio.*` deps, so saving a draft updates the diff.
- No new frontend dependency; the no-Node-frontend stack (ADR 0001) holds.
- The old two-column line-diff (`lineDiff`, `.pc-diff-table`,
  `.diff-row`/`.diff-cell-*`, `test_studio_line_diff.py`) is removed; don't
  reintroduce a parallel renderer.
