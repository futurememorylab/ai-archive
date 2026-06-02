# Studio aligned scene compare + linked timeline

**Date:** 2026-06-02
**Status:** Design (approved verbally)
**Branch:** `polish/prompt-output`

## Problem

Comparing two prompt versions' **output** in Studio currently shows two
side-by-side Output panes plus a flowing word-diff. The user wants instead:

1. **A single aligned scene table** (one two-column table) where the two
   versions' structured output is aligned row-by-row into scenes, each row
   showing a per-scene diff status (UNCHANGED / CHANGED / ADDED / REMOVED) and
   word-level inline highlights (red strike for removed words on the left,
   green for added words on the right). See mock 1.
2. **A linked comparison timeline**: the existing two-track marker overlay
   gains visible scene labels and status-colored borders, and selecting a
   scene (in the table or on the timeline) highlights the matching block on
   the other. See mock 2.

Both views are driven by **one shared model**: the two versions' annotations
aligned into scenes with a per-scene diff status. The table renders it; the
timeline colors and links to it.

This supersedes the rendered-text Output diff briefly prototyped earlier on
this branch (now rolled back).

## Scope

- Covers **markers (scenes) + fields + notes**.
- The aligned table **replaces the two Output panes when comparing**; the
  Prompt tab keeps its existing two-body word diff unchanged.
- All labels in **English**.
- The table + timeline linkage ship together on this branch.
- **No "evidence" line.** The mock showed a "● evidence M:SS" sub-line
  cross-referencing a CatDV marker, but the current annotation model has no
  such field (markers carry only `in_secs / out_secs / name / category /
  description`) and no source for it. It is dropped from scope.

## Architecture

### 1. Shared model — `backend/app/services/output_compare.py`

Pure function, no I/O, fully unit-tested:

```
build_output_compare(cur_panels: dict, cmp_panels: dict) -> dict
```

`cur_panels` / `cmp_panels` are the dicts `build_draft_view` already returns
(markers / fields / notes). Output model:

```
{
  "scene_count": int,                       # number of aligned scene rows
  "scenes": [
    {
      "key": "scene-<i>",                   # stable per-row id for linkage
      "status": "unchanged"|"changed"|"added"|"removed",
      "cmp": {"in_secs","out_secs","tc","dur_s"} | None,   # left (older)
      "cur": {"in_secs","out_secs","tc","dur_s"} | None,   # right (newer)
      "segs": [ {type:"eq"|"ins"|"del", text} ... ],       # one diff, both sides
    }, ...
  ],
  "fields": [
    {"key":"field-<ident>", "identifier", "status",
     "cmp_segs":[...]|None, "cur_segs":[...]|None}, ...
  ],
  "notes": {"segs":[...], "changed": bool} | None,
}
```

- **Scene `segs`** is a single `word_diff(cmp_text, cur_text)` over the marker
  name (description appended on a second line if present). The **left cell
  renders `eq`+`del`** (removed words struck red); the **right cell renders
  `eq`+`ins`** (added words green). For `added` rows `cmp` is None and segs are
  all-`ins`; for `removed`, `cur` is None and segs are all-`del`.
- `tc` is SMPTE-ish `M:SS`; `dur_s` is `round(out-in)` for the "· 7s" label.

#### Scene alignment algorithm

Greedy two-pointer interval merge over the two **time-ordered** marker lists
(markers already come sorted by `in_secs` from `build_draft_view`):

```
i, j = 0, 0
while i < len(cmp) and j < len(cur):
    if overlaps(cmp[i], cur[j]):        # in/out ranges intersect
        pair -> status = changed if text differs else unchanged; i++, j++
    elif cmp[i].out <= cur[j].in:       # cmp scene ends first, no match
        cmp[i] -> removed; i++
    else:
        cur[j] -> added; j++
drain remaining cmp -> removed; remaining cur -> added
```

`overlaps(a,b) = a.in_secs < b.out_secs and b.in_secs < a.out_secs` (treat a
missing `out` as `in + 1`). Verified by hand against mock 1: cmp v4
[Celkový 0-7, Ženy 7-28, Žena stojí 28-42] vs cur v5 [Celkový 0-7, Žena v
šátku 7-17, Dítě 17-24, Žena vzor 24-35, Detail 35-42] →
`[unchanged, changed, added, changed, added]` = **5 aligned scenes**, matching
the mock exactly.

Documented limitation: a 1→N split (one cmp scene overlapping two cur scenes)
pairs with the first and marks the rest added. Acceptable for v1.

- **Fields:** align by `identifier` over the union of keys (sorted). Per key:
  `added` (cur only), `removed` (cmp only), else `changed`/`unchanged` by value
  word-diff.
- **Notes:** one `word_diff(cmp_notes, cur_notes)` over the joined note text;
  `changed` = any non-eq segment. Rendered as one flowing block.

### 2. `word_diff` promoted to `backend/app/services/word_diff.py`

The Python implementation currently living in
`tests/unit/test_studio_word_diff.py` becomes the real module (the test
imports it and stays the authoritative spec — no behaviour change). A Jinja
helper registered on the shared env renders segments → HTML:

```
diff_html(segs, side="both")   # side: "left" (eq+del) | "right" (eq+ins) | "both"
```

Escapes text, wraps `ins`/`del` in `<ins class="diff-ins">` / `<del
class="diff-del">` (same classes/CSS as today). The **client `wordDiff` in
`studio-diff.js` stays** for the live Prompt-tab diff (it reads textareas, must
stay client-side); only the Output/compare diff moves server-side.

### 3. Table partial + route

- **`backend/app/templates/pages/_studio_compare_table.html`** renders the
  model: a header (`SCENES → CatDV markers` · `N aligned scenes`), then scene
  rows (two cells; left/right SMPTE `M:SS · Ns`, diff body, status pill on the
  right cell; a hatched `— no scene —` placeholder on the missing side), then a
  Fields section (two-column rows), then a Notes block. Status pill uses the
  existing `.pill` system: `unchanged`→neutral, `changed`→new `.pill.changed`
  (purple), `added`→`.pill.ok` (green), `removed`→`.pill.bad` (red).
- **Route `GET /studio/_compare?version_id=&compare_id=&clip_id=`** builds both
  versions' panels via the existing `_load_studio_panels`, calls
  `build_output_compare`, renders the partial. Returns the same "focus a clip"
  empty state as the per-card output when `clip_id` is absent.
- **Wiring:** in the compare layout, when `mode === 'output'` **and**
  `compareVersionId !== null`, hide the two per-card output panes and show one
  full-width `.studio-compare-output` region that loads `/studio/_compare`.
  Prompt mode and single-version (non-compare) Output are unchanged.

### 4. Timeline linkage (mock 2)

Enhances the existing `_player_overlay.html` rows — no new timeline:

- **Labels:** render `m.name` inside each `.range` block (truncated with
  ellipsis via CSS), replacing the title-only tooltip.
- **Status borders:** each range carries a status class
  (`range-st-unchanged` / `-changed` / `-added` / `-removed`) → border color
  (unchanged = blue `--info`, changed = purple `--changed`, added = green
  `--good`, removed = red `--bad`). Keeps the existing per-track fill
  (`range-cur` blue, `range-cmp` amber).
- **Shared keys:** when comparing, `_studio_player`'s row builder derives both
  rows from `build_output_compare` so every range gets `data-scene-key` +
  status matching the table. Non-compare (single version) keeps the current
  plain row builder.
- **Selection linkage:** `selectedSceneKey` lives in the studio store
  (`Alpine.store('studio')`). Table rows set it on hover/click via Alpine;
  timeline ranges are HTMX-injected plain divs, so a small vanilla bridge
  (mirroring the existing `window.studio` shim pattern) toggles an `.is-linked`
  class on every `[data-scene-key="<key>"]` element — table row and timeline
  block alike — whenever the selection changes. Bidirectional: hovering a
  timeline range sets the same store key.

### CSS

New tokens/classes in `app.css` (reusing existing tokens):
`--changed: #b794f6` (purple) — used for **both** the CHANGED pill
(`.pill.changed`) and the changed timeline-range border (`.range-st-changed`),
so the two views agree; `.studio-compare-*` table layout; `.range` label text +
`.range-st-*` borders; `.is-linked` / `.is-selected` highlight. Use tokens, not
raw hex, per `docs/design-language.md`.

## Alternatives considered

- **Client-side alignment + diff** (compute in JS from two embedded JSONs).
  Rejected: the alignment is non-trivial and the codebase prefers
  Python-authoritative, unit-tested logic; server-render keeps the table a
  plain HTMX partial.
- **A new third "Compare" tab** instead of replacing Output. Rejected by the
  user — the table replaces Output when comparing.
- **Markers-only.** Rejected by the user — fields + notes included.

## Risks

- **Layout breakout.** The full-width table must escape the two-card grid only
  in output+compare mode. Guarded by tests asserting the two output panes are
  hidden and the table region shown only in that state.
- **Key drift table ↔ timeline.** Both must derive `data-scene-key` from the
  same `build_output_compare` call shape. Guarded by a test asserting the
  scene keys rendered in the table match those on the timeline ranges for the
  same (cur, cmp, clip).
- **Alignment correctness.** The crux. Heavily unit-tested with the mock's
  exact data plus edge cases (added-only, removed-only, all-unchanged, empty).

## Tests (TDD)

Unit (Python, authoritative):
1. `test_word_diff.py` — move/keep the existing mirror tests against the new
   module; add `diff_html` side-filtering (left=eq+del, right=eq+ins).
2. `test_output_compare.py` — the alignment algorithm: the mock-1 fixture
   yields exactly `[unchanged, changed, added, changed, added]`; plus
   added-only, removed-only, all-unchanged, empty-vs-nonempty, fields
   add/remove/change, notes change/no-change.

Integration:
3. `test_studio_compare_route.py` — `/studio/_compare` renders scene rows with
   status pills, `— no scene —` placeholders, `diff-ins`/`diff-del` spans, and
   `data-scene-key` on each row; empty state without `clip_id`.
4. `test_studio_compare_layout.py` — output+compare hides the two per-card
   panes and shows the single table region; prompt mode and single-version
   output are unaffected.
5. `test_studio_timeline_linkage.py` — comparing renders `.range` blocks with
   visible labels, `range-st-*` status classes, and `data-scene-key`s that
   match the table's for the same (cur, cmp, clip).

## Manual acceptance flows

1. **Aligned scene table replaces Output when comparing.**
   - Setup: a prompt with two versions both run on the same focused clip with
     differing scenes. Open
     `/studio?prompt_id=<P>&version_id=<v2>&compare_version_id=<v1>`, click the
     **Output** tab.
   - Expected: one full-width table (not two panes). Aligned scene rows show
     `M:SS · Ns` per side, word-level highlights (left red-strike, right
     green), a status pill (UNCHANGED/CHANGED/ADDED/REMOVED), and `— no scene —`
     where a side has no matching scene. Header reads `… · N aligned scenes`.
     Fields and notes appear below in the same two-column diff style.
2. **Prompt diff still works (regression).** Same compare view, **Prompt** tab,
   **Diff** on → unchanged two-body word diff.
3. **Timeline shows labeled, status-colored scenes.** With the compare open,
   the timeline shows two tracks with scene-name labels inside each block and
   borders colored by status (green added, purple changed, blue unchanged).
4. **Selection links table ↔ timeline.** Hover/click a scene row in the table
   → the matching timeline block highlights. Hover a timeline block → the
   matching table row highlights. Works both directions, no page reload.
5. **Single-version Output unaffected.** Open one version (no compare),
   **Output** tab → the normal per-card panels render (no table, no breakout).
