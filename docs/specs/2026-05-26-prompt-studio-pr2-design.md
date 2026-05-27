# Prompt Studio — PR2 (version compare)

**Date:** 2026-05-26
**Status:** Approved (design)
**Supersedes / extends:** `docs/specs/2026-05-26-prompt-studio-design.md`
**Predecessor:** PR1 — `docs/adr/0033-prompt-studio-pr1-shell-and-run-loop.md`

## Problem

PR1 shipped the studio shell plus a single-clip run loop with one prompt
card (cur only). The iteration loop now works for a single version on a
single clip, but the core motivation of Studio — *backtesting old
versions side by side* — is not yet exercised. PR2 adds the second
prompt-card, line-level diffs of prompt body and structured output, and
overlays the cmp version's scenes onto the player timeline.

This spec covers PR2 only. PR3 (visual polish + run-state UX) is out of
scope and will land separately.

## Goals

- A version picker chip on each prompt-card header. Picking on the cur
  card retargets the page's active version (Run-button label and URL
  follow); picking on the cmp card is local to that card.
- `+ Compare` button materializes a second prompt-card to the right of
  cur, defaulting to the next-most-recent non-cur version.
- Each card has Prompt / Output tabs; the mode is shared across cards so
  picking one switches both.
- `Diff vs {cur.label}` toggle on the cmp card swaps its body into a
  line-diff of the active mode (Prompt body, or
  `JSON.stringify(output, null, 2)`).
- Player timeline gains a second range row underneath the existing one,
  for the cmp version's scenes.
- Extract the existing clip-detail timeline overlay into a shared
  partial reused by both clip_detail and the studio player.
- Studio Output tab reuses `_anno_panels.html` — the same partial that
  renders markers / fields / notes on `clip_detail.html` and
  `_anno_draft.html` — instead of its current bespoke `.ro-scene` /
  `.ro-field` markup. Visual parity with clip view falls out for free
  and the studio Markers list inherits clip-jump-on-click.

## Non-goals (PR2)

- Stacked / unified diff layouts (still side-by-side only).
- Run history viewer / multiple runs per (version, clip).
- Visual polish pass against the React prototype's `styles.css` (PR3).
- Tab-sync state surviving page reload (no `?mode=` URL param).
- New player behavior beyond the overlay (no new transport, no frame
  extraction, no scrubbing changes).
- Server-side diff. All diffing is client-side from already-loaded text.

## Design source

PR2 components in the React prototype: `VersionPicker`, the cmp side of
`PromptCard`, `PromptDiff`, `OutputDiff`, `UnifiedDiffPane`, and the
player marker overlay. Original bundle was at `/tmp/catdv_design/`;
re-fetch only if a question can't be answered from this spec.

The line-LCS algorithm (`lineDiff(a, b)` in `studio.jsx`) is ported
verbatim to `studio-diff.js`. No other prototype internals are copied;
the rest is reimplemented in Jinja + Alpine + HTMX.

## Locked decisions

| Decision | Choice | Reason |
|---|---|---|
| Version picker location | Inside each prompt-card header, replacing the hardcoded `v{n}` label | Spec: "cur card always present, bound to whichever version is active in its own version picker." |
| Editable when non-draft? | No — falls back to `<pre>` (PR1's rule still applies) | Editing rules are owned by the version's lifecycle, not by Studio. |
| Cur picker = page-active version? | Yes. Updates `studioPage.activeVersionId/Num`, Run-button label, and URL `?version_id=N`. cmp picker updates URL `?compare_version_id=M`. Both are read back by the page route on initial load. | Spec says cur is "the active version". Symmetric deep-linking falls out for free and makes integration tests trivial. |
| Version-switch mechanism | HTMX partial swap of the prompt-card body | Same shape as existing `/studio/_run` swap. Server owns the draft↔readonly DOM transition. |
| Output-diff data source | Embed raw JSON in `_studio_run_output.html` as `<script type="application/json" data-run-json>` | Zero extra fetches; tiny payload bump on a partial that is already loaded. |
| Player overlay | Extract clip_detail's `.transport`/`.timeline`/`.ranges`/`.playhead` into a shared partial; upgrade `_studio_player.html` to use the existing `Alpine.data("player", ...)` + the shared partial | Reuses proven player behavior, deduplicates an already-2-row timeline pattern (markers + draft-ranges → cur + cmp), satisfies "no new player behavior". |
| Annotation cards (Output tab) | Reuse `_anno_panels.html` from clip_detail / anno-draft. `_studio_run_output.html` becomes a thin adapter that converts `(studio_run.output_json, prompt_version.target_map)` into the `panels` dict the existing partial expects, then `{% include %}`s it. | Same reasoning as the player overlay extraction: the rendering already exists and is battle-tested on clip view. Free visual parity, free marker-click-to-seek, no parallel-evolution risk. |
| Diff toggle | Pure Alpine, reads from sibling card's DOM | Data is already loaded; no need to round-trip. |

## Architecture

### Components — new and changed

```
NEW templates
├ pages/_studio_version_picker.html     chip + dropdown, reused by cur & cmp
├ pages/_player_overlay.html            shared timeline overlay
├ pages/_studio_compare.html            wraps the 1-or-2 cards row
└ pages/_studio_diff.html               diff-view body (renders inside cmp card)

CHANGED templates
├ pages/_studio_prompt_card.html        side-aware (cur|cmp); chip in header;
│                                        diff-view slot in body; cmp gets
│                                        "Diff vs v{cur}" toggle
├ pages/_studio_run_output.html         REWRITE: drops bespoke
│                                        .ro-scene/.ro-field markup;
│                                        builds `panels` dict from
│                                        output_json + target_map and
│                                        {% include %}s _anno_panels.html.
│                                        Still emits the <script
│                                        type="application/json"
│                                        data-run-json> block.
├ pages/_anno_panels.html               + `show_history` flag (default
│                                        true; studio passes false to
│                                        hide the clip-history tab).
├ pages/_studio_player.html             replaces native <video controls>
│                                        with x-data="player(...)" +
│                                        {% include "pages/_player_overlay.html" %}
├ pages/studio.html                     uses _studio_compare.html
└ pages/clip_detail.html                consumes _player_overlay.html
                                         (no behavior change — extraction only)

NEW routes
├ GET /studio/_prompt_card?side=cur|cmp&prompt_version_id=N&clip_id=M
│       Renders one prompt-card. Returns 404 on missing version.
│       For side=cur, the route doesn't update page state — that's
│       Alpine's job on the HTMX after-swap event.
└ GET /studio/_player?clip_id=N&version_id=A&compare_id=B
        Augments the existing /studio/_player. compare_id is optional;
        when present, the rendered overlay carries a second range row
        built from B's latest run scenes.

NEW static
└ studio-diff.js                         lineDiff() port + Alpine `cmpDiff`
                                          component. Loaded only on /studio.
```

### Data flow

**Cur card version switch:**
1. User picks v3 in the cur chip.
2. HTMX: `GET /studio/_prompt_card?side=cur&prompt_version_id=3&clip_id=<focused>` swaps the card.
3. `htmx:afterSwap` handler reads the new version's `id` / `version_num` from a data attribute on the swapped root, writes them onto `studioPage.activeVersionId` / `activeVersionNum`, and `history.replaceState`s `?version_id=3` into the URL.
4. `pendingRunSwap++` re-fetches the Output tab (already wired in PR1).
5. The player partial is re-fetched with the new `version_id` so the cur range row reflects v3's scenes.

**Cmp card materialization:**
1. `+ Compare` button on cur → page Alpine sets `compareVersionId = <default>`. Default = first non-cur version preferring `state='draft'`, else the most-recent `state='production'`, else simply the most-recent non-cur version.
2. An empty `<div data-cmp-slot></div>` next to the cur card becomes visible. HTMX loads `/studio/_prompt_card?side=cmp&prompt_version_id=…&clip_id=…` into it.
3. Player partial is re-fetched with `compare_id=<compareVersionId>`; the overlay now renders two range rows.
4. `× Close` button on the cmp card sets `compareVersionId = null`; the cmp slot empties and the player drops back to one row.

**Cmp picker switch:** Same as cur, but only the cmp card is swapped and `compareVersionId` is the only state updated. URL is not touched.

**Tab sync:**
- `mode` (`'prompt' | 'output'`) moves from per-card state into `studioPage`. Each card binds its tabs to `$root.mode`; picking on one card flips both. (PR1 had it per-card; this is a small refactor.)

**Diff toggle (cmp only):**
- `cmpDiff` boolean on the cmp card. When true, the card's body shows `{% include "pages/_studio_diff.html" %}` instead of the Prompt/Output content. The toggle button reads `Diff vs v{cur.version_num}` and pulls cur's version number from `$root.activeVersionNum`.
- The `cmpDiff` Alpine component reads from DOM rather than re-fetching:
  - **Prompt diff:** find cur card's `<textarea>` or `<pre>` text; same on cmp.
  - **Output diff:** find each card's `<script type="application/json" data-run-json>` block, `JSON.parse(...)`, then `JSON.stringify(_, null, 2)`.
- Both strings are fed to `lineDiff(a, b)` which yields an array of
  `{type: 'eq'|'del'|'ins', a?, b?}` rows. Rendered as a two-column
  table with `.del` / `.ins` highlighting on differing rows.
- Re-runs whenever cur or cmp content changes (Alpine `$watch` on
  `$root.pendingRunSwap` and on the cur/cmp version ids).

### Player overlay (the shared component)

`clip_detail.html` already implements a custom timeline with two stacked
range rows: `.ranges` for production markers and `.ranges.draft-ranges`
for draft markers (`clip_detail.html:118-138`). PR2 extracts this into a
shared partial.

```jinja
{# pages/_player_overlay.html #}
{# Required scope (from caller's x-data="player(...)"):
   - duration_secs, fps, current, isMarkerActive(m), pct(secs), seek(secs),
     seekFromEvent(e), tc(secs), frameStr(secs), quintileTc(i)
   Caller passes `rows` as a list of dicts:
     {key, ranges: [{in_secs, out_secs, name}], cls}
   Up to two rows in v1. #}
<div class="transport">
  <div class="timeline" @click="seekFromEvent($event)">
    <div class="ticks"></div>
    {% for row in rows %}
      <div class="ranges {{ row.cls }}">
        {% for m in row.ranges %}
          <div class="range" style="left:…%; width:…%;" title="{{ m.name }}"></div>
        {% endfor %}
      </div>
    {% endfor %}
    <div class="playhead" :style="`left: ${pct(current)}%`"></div>
    <div class="tc-labels">… (unchanged) …</div>
  </div>
  {# Optional legend below the transport #}
  {% if rows|selectattr('ranges')|list %}
    <div class="timeline-legend mono-cell muted">
      {% for row in rows %}
        <span class="legend-{{ row.cls }}">● {{ row.key }} · {{ row.ranges|length }} scenes</span>
      {% endfor %}
    </div>
  {% endif %}
</div>
```

`clip_detail.html` is updated to pass
`rows = [{key:'markers', ranges:markers, cls:'range-cur'}, {key:'draft', ranges:draft_markers, cls:'range-draft'}]`.
Behavior is preserved exactly — same range count, same positions, same
`active` highlighting. A snapshot-style integration test guards against
regressions.

`_studio_player.html` upgrades from native `<video controls>` to:

```jinja
<div class="studio-player"
     x-data="player({{ fps }}, {{ duration_secs }}, [], [])">
  <video x-ref="video" class="video" src="/api/media/{{ clip_id }}"
         preload="metadata"></video>
  {% include "pages/_player_overlay.html" with context %}
</div>
```

Caller passes `rows` built from each version's latest run scenes:

```python
# in /studio/_player route
cur_scenes = (cur_run.output_json or {}).get("scenes") or []
rows = [{"key": f"v{cur.version_num}", "ranges": cur_scenes, "cls": "range-cur"}]
if compare_id is not None:
    cmp_scenes = (cmp_run.output_json or {}).get("scenes") or []
    rows.append({"key": f"v{cmp.version_num}", "ranges": cmp_scenes, "cls": "range-cmp"})
```

### Annotation cards (the shared `_anno_panels.html` partial)

The Output tab today is a custom renderer in `_studio_run_output.html`:
`.ro-scene` rows for `output_json.scenes` and `.ro-field` rows for
everything else. The clip view has been rendering the same conceptual
content — markers, fields, notes — through `_anno_panels.html` since
PR3 of the original UI MVP, and that partial is already reused by
`_anno_draft.html` for the draft scope. PR2 deletes the studio-bespoke
renderer and routes the studio Output tab through the same partial.

**Adapter.** A small Python helper converts a studio run + version into
the `panels` dict shape `_anno_panels.html` expects:

```python
# backend/app/services/studio_panels.py  (new)
def panels_from_studio_run(
    run: StudioRun | None,
    version: PromptVersion | None,
    fps: float,
) -> dict:
    """Return the panels dict consumed by templates/pages/_anno_panels.html.

    Maps:
      output_json.scenes[]            → panels['markers']
      output_json[k] for k != scenes  → panels['fields']  (identifier from
                                                            version.target_map[k])
      no separate notes mapping in v1 → panels['notes'] = None
    """
```

Scenes carry `in_secs`, `out_secs`, `name`, and optionally `description`
and `category` (when the prompt's output_schema emits them) — exactly
what `_anno_panels.html` already renders on markers. Non-scenes keys are
looked up in `version.target_map` to get a CatDV field identifier (e.g.
`summary_cz → pragafilm.popis.materialu`); if a key has no mapping, the
field row shows the raw key as the identifier.

**Rewritten `_studio_run_output.html`:**

```jinja
{% if not version %}
  <div class="run-empty muted">Unknown version.</div>
{% elif not run %}
  <div class="run-empty muted">
    No run yet. Hit <b>Run</b> to execute v{{ version.version_num }} on the focused clip.
  </div>
{% elif run.status == 'error' %}
  <div class="run-error">…</div>
{% elif run.status in ('pending', 'running') %}
  <div class="run-empty muted">⟳ Running…</div>
{% else %}
  {% include "pages/_anno_panels.html" %}   {# panels passed in by the route #}
  <script type="application/json" data-run-json>{{ run.output_json|tojson }}</script>
  <div class="run-stats mono-cell muted">…</div>
{% endif %}
```

Both the route serving `/studio/_run` and the route serving
`/studio/_prompt_card` (which includes this partial) call
`panels_from_studio_run(...)` and pass `panels` in the template context.

**`show_history` flag on `_anno_panels.html`.** The History tab in the
clip view loads clip-history HTML and is not meaningful for studio
output. The shared partial gains a `show_history` argument (default
`true`); studio includes pass `show_history=False`. Clip detail's
existing two includes don't pass the flag and so behave unchanged.

**Alpine scope contract.** `_anno_panels.html` reads from its enclosing
Alpine `x-data`:

| Used by partial | clip_detail provides | studio provides |
|---|---|---|
| `tab` ('markers'\|'fields'\|'notes'\|'history') | yes, on the aside `x-data` | added to `studioPromptCard()` Alpine data (distinct from page-level `mode`) |
| `seek(secs)` (marker-click jump) | yes (on the player wrap) | proxied via `$root.seekFocusedClip(secs)` → walks to the player Alpine instance and calls `seek(secs)` |
| `historyLoaded`, `loadHistory()`, `historyHtml` | yes | not needed (`show_history=false` removes the tab) |

The `seek` proxy is one method on `studioPage` that finds the player's
Alpine root and forwards. Same pattern as `window.studio._root()`
already used in `studio.js`. Defined once; tested once.

**Why this matters.** Without this reuse, two separate templates
evolve in parallel — every CSS tweak, every accessibility fix, every
field type added to `_anno_panels.html` (e.g. a new "BIG_NOTES" widget,
or marker categories getting badge colors) has to be re-applied to the
studio renderer. Consolidating now while the studio renderer is one
day old is much cheaper than the eventual divergence cleanup.

**Test coverage.**
- Unit: `panels_from_studio_run` — empty output, scenes-only,
  fields-only, mixed, target_map hit vs miss.
- Integration: `/studio/_run` response contains `class="anno-tabs"`
  (proves the shared partial is rendering) and no `class="ro-scene"`
  (proves the bespoke markup is gone).
- Integration: clip detail unchanged — existing
  `tests/integration/test_clip_detail*.py` still pass without
  modification (the `show_history` default protects them).

### `lineDiff` algorithm

Port of `studio.jsx`'s `lineDiff(a, b)`:

```js
// Standard LCS over lines, walked back to produce an alignment.
function lineDiff(aText, bText) {
  const A = (aText || "").split("\n");
  const B = (bText || "").split("\n");
  const n = A.length, m = B.length;
  const lcs = Array.from({length: n + 1}, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      lcs[i][j] = A[i] === B[j] ? lcs[i+1][j+1] + 1
                                : Math.max(lcs[i+1][j], lcs[i][j+1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j])           { out.push({type: 'eq', a: A[i], b: B[j]}); i++; j++; }
    else if (lcs[i+1][j] >= lcs[i][j+1]) { out.push({type: 'del', a: A[i]}); i++; }
    else                                 { out.push({type: 'ins', b: B[j]}); j++; }
  }
  while (i < n) out.push({type: 'del', a: A[i++]});
  while (j < m) out.push({type: 'ins', b: B[j++]});
  return out;
}
```

Tested in isolation (see Testing strategy).

### CSS

Minimum needed for PR2; visual polish defers to PR3.

- `.pc-vchip` — version chip on card header (replaces `.pc-vlbl`).
- `.pc-vchip .menu` — dropdown body (one row per version with state badge).
- `.cmp-card` — visual marker on the cmp side (close button slot).
- `.btn-compare` — the `+ Compare` button on cur card.
- `.btn-diff-toggle` — `Diff vs v{n}` toggle on cmp card.
- `.pc-diff` — diff table container; `.diff-row`, `.del`, `.ins`, `.eq`.
- `.range-cur`, `.range-cmp`, `.range-draft` — distinct colors for the
  overlay rows. Existing `.range` styling becomes the base.
- `.timeline-legend`, `.legend-range-cur`, `.legend-range-cmp` — legend
  row underneath the transport.

## Page route

`GET /studio` (existing) gains two optional query params:

```
GET /studio?prompt_id=N&version_id=A&compare_version_id=B
                       ──────────────  ──────────────────────
                       PR2: cur picker  PR2: cmp picker
```

- `version_id`: if present and belongs to `prompt_id`, becomes the
  active version (cur). Otherwise the PR1 default applies (draft if
  exists, else first version).
- `compare_version_id`: if present and belongs to `prompt_id` and
  differs from cur, the cmp card is server-rendered alongside cur on
  initial load. Otherwise single-card mode.

Both params are written back to the URL via `history.replaceState` when
the user changes a picker, so reload-and-share-link both work.

## REST API

No new endpoints. PR2 uses the existing
`/api/studio/runs?prompt_version_id=&clip_id=&latest=1` lookup
implicitly via the partial routes; the partials embed the JSON inline
so the browser doesn't have to fetch it again.

## Migrations

None. PR2 is UI + routes only; schema set in PR1 is sufficient.

## Server routes

```
GET /studio/_prompt_card
  ?side=cur|cmp
  &prompt_version_id=N
  &clip_id=M               (optional — when omitted, output tab shows
                            empty-state prompt; editor still renders)
  → templates/pages/_studio_prompt_card.html

  Resolves version + latest run for (version, clip), renders editor or
  readonly view, embeds run JSON via _studio_run_output.html include.
  404 if version not found.

GET /studio/_player
  ?clip_id=N
  &version_id=A             (cur version)
  &compare_id=B             (optional cmp version)
  → templates/pages/_studio_player.html

  Resolves latest run for each (version, clip), builds `rows` list with
  scenes, renders the player wrapper + shared overlay.
```

## Testing strategy

**Unit — `lineDiff`:**
A Python port of `lineDiff` lives in `tests/unit/test_studio_line_diff.py`
and is golden-tested against fixtures: empty/empty, identical, all-add,
all-del, interleaved. The actual JS is then validated against the same
fixtures via a small Node script (or, if Node isn't reliably available
on this machine, by checking lineDiff at a structural level — same
input/output shapes — in a Vitest-less inline test runner). Plan picks
one of those; preference is the Python port for hermeticity.

**Unit — `_studio_prompt_card` route:**
- 200 for valid (side, version, clip)
- 404 for missing version
- draft version → `<textarea>` present
- non-draft → `<pre>` present, no `<textarea>`
- with clip → output partial included with `data-run-json` block
- without clip → "Click a clip…" empty state, no run JSON

**Unit — `_studio_player` route:**
- one-row overlay when only `version_id` given
- two-row overlay when `compare_id` also given
- empty `rows` when no scenes
- 404 on missing version (cur or cmp)

**Integration — overlay extraction safety:**
`tests/integration/test_clip_detail_player.py` renders `clip_detail.html`
before and after the partial extraction and asserts the rendered
`.transport` block is structurally equivalent — same range count, same
left/width percentages, same `active` bindings, same playhead. This is
one of two places where PR2 touches non-Studio surfaces; the test
guards the seam.

**Unit — `panels_from_studio_run` adapter:**
Cases: empty output, scenes-only, fields-only, mixed, target_map hit,
target_map miss (raw key falls through), missing `out_secs` on a scene,
non-string field values (numbers, lists) survive `_anno_panels.html`'s
expectations.

**Integration — annotation-card reuse:**
- `/studio/_run` response includes `class="anno-tabs"` and does NOT
  include `class="ro-scene"` or `class="ro-field"` (proves the bespoke
  markup is gone and the shared partial is in).
- Existing `tests/integration/test_clip_detail*.py` pass without
  modification (proves the `show_history` default-true doesn't break
  the clip view).

**Integration — Studio compare flow:**
`tests/integration/test_studio_compare.py` (new) drives:
1. Load `/studio?prompt_id=N` and assert one prompt card.
2. Load `/studio?prompt_id=N&compare_version_id=M` and assert both
   cards are server-rendered with the correct version chips.
3. Load `/studio?prompt_id=N&version_id=A&compare_version_id=B` and
   assert cur is A, cmp is B, Run button labels with `v{A.version_num}`.
4. Assert both cards' run JSON is embedded when runs exist.

**Integration — overlay scenes:**
`tests/integration/test_studio_player_overlay.py` hits
`/studio/_player?clip_id=…&version_id=A&compare_id=B` with seeded runs
and asserts two `.ranges` rows are rendered with correct scene counts
and percentages.

**Integration — version-picker route round trip:**
`tests/integration/test_studio_prompt_card_route.py` POSTs each
combination of side × draft/non-draft and inspects the response.

## Manual acceptance flows

Each numbered flow corresponds to one PR2 capability. A reviewer (or
the implementer at the end of the work) walks through them in order;
all should pass before the PR is considered done. Test data assumption:
a prompt with ≥ 2 versions (one draft, one production) and a folder
with ≥ 1 clip that has at least one prior run on each version.

1. **Cur-card version picker — switch**
   - Open `/studio?prompt_id=N`. Focus a clip.
   - Click the version chip on the cur card → dropdown lists all
     versions with state badges (draft / production / archived).
   - Pick a different version. Card body swaps. Run-button label
     updates to the new `v{n}`. URL gains `?version_id=N`.
   - Output tab refreshes to that (version, clip) pair's latest run
     (or empty-state if no run).

2. **Cur-card editable / read-only**
   - With cur on a **draft** version, Prompt tab shows a `<textarea>`;
     typing debounces and the footer toggles `draft · saving… → saved`.
   - Switch cur to a **production** version. Prompt tab now shows a
     `<pre>` block (no editor, no save indicator).
   - Switch back to draft → textarea returns.

3. **Compare materialization**
   - Single-card mode initially. Click `+ Compare` on the cur card.
   - A second card appears to the right with its own version chip and
     tab strip. Default version = "next-most-recent non-cur, preferring
     draft, else production".
   - The player timeline now shows two stacked range rows. Legend
     below the transport reads `v{cur} · X scenes` and
     `v{cmp} · Y scenes`.
   - Click `×` on the cmp card → cmp disappears, overlay drops to one
     row, legend collapses.

4. **Cmp-card version picker — local**
   - With cmp showing, click its chip and pick a different version.
   - Only the cmp card swaps. Run button label and `?version_id=`
     remain on cur. URL gains/updates `?compare_version_id=M`.

5. **Tab sync across cards**
   - With both cards visible, click `Output` on the cur card → both
     cards switch to Output. Click `Prompt` on cmp → both switch back.

6. **Diff vs cur — Prompt mode**
   - With both cards visible, on the Prompt tab, click `Diff vs v{cur}`
     on the cmp card.
   - Cmp card's body switches to a two-column line-diff. Identical
     lines are neutral; lines present only in cur are highlighted as
     `del`; lines present only in cmp are highlighted as `ins`.
   - Toggle the diff button off → cmp body returns to the Prompt
     `<pre>` view.

7. **Diff vs cur — Output mode**
   - Switch both cards to the Output tab (per flow 5). Click
     `Diff vs v{cur}` on cmp.
   - Cmp body shows a line-diff of `JSON.stringify(output, null, 2)`
     for the two latest runs. Scenes with the same `in_secs/out_secs`
     but different names show as paired del/ins lines.
   - Edge case: if either side has no run yet, the diff view shows an
     "empty on one side" placeholder rather than crashing.

8. **Annotation-card visual parity**
   - On the Output tab of either card, the markers/fields rendering
     looks visually identical to what `/clips/{id}` shows on the
     published-scope panel. Same tabs (Markers / Fields / Notes),
     same marker article markup, same field-row layout. No
     history tab in Studio (hidden via `show_history=false`).
   - Click any marker in the Markers list → the player seeks to that
     `in_secs`. (Cross-component reuse working end-to-end.)

9. **Player overlay updates on version switch**
   - With both cards visible, switch the cmp version to a different
     version. The bottom range row redraws with the new version's
     scenes. Cur (top) row is unchanged.

10. **Run on this clip — label follows cur**
    - Switch cur to v3 → Run button reads `▶ Run on this clip · v3`.
    - Click Run. While running, button reads `⟳ Running… 00:0X`.
      On completion, cur output refreshes and any open diff view
      re-renders against the new run.

11. **Deep linking**
    - Copy the URL after configuring cur=v3 and cmp=v5 (e.g.
      `/studio?prompt_id=N&version_id=3&compare_version_id=5`).
    - Open the URL in a new tab. Both cards are present on first
      paint, chips pre-selected to v3 / v5, Run button labels v3.

12. **Single-card mode reload safety**
    - Load `/studio?prompt_id=N` with no `compare_version_id`. One
      card. Player overlay has one row. No regressions vs PR1
      behavior.

13. **Clip-detail regression (the only non-Studio surface touched)**
    - Open any clip's detail page (`/clips/{id}`). Player works as
      before — timeline, scrubbing, marker ranges, draft-ranges,
      playhead, keyboard shortcuts. Anno panels on the right show
      Markers / Fields / Notes / History. No visual or behavioral
      diff vs `main` before PR2.

## Risks & mitigations

- **Risk:** Extracting clip_detail's timeline breaks the existing UI.
  **Mitigation:** Snapshot-style integration test (above) gates the
  refactor. The extraction is mechanical; behavior delta should be zero.
- **Risk:** Routing the studio Output tab through `_anno_panels.html`
  reveals subtle scope coupling (the partial reads `tab`, `seek`,
  `historyHtml` from its parent Alpine context).
  **Mitigation:** The Alpine scope contract is documented above;
  `studioPromptCard()` gains `tab` and `seekFocusedClip` proxies; the
  `show_history` flag elides the history-tab requirements. Coverage
  comes from the integration test asserting `anno-tabs` renders inside
  `/studio/_run` and that marker click triggers the player seek (the
  latter via a small DOM-event integration test, or a manual
  verification entry in PR3's polish pass — pick at plan time).
- **Risk:** `lineDiff` performance on very long prompts / outputs.
  **Mitigation:** v1 prompts cap at a few KB. Algorithm is O(n·m) which
  is fine at that scale; revisit if we hit prompts >10K lines.
- **Risk:** Diff renders stale data after the underlying run completes.
  **Mitigation:** `cmpDiff` watches `$root.pendingRunSwap` and re-runs
  on increment (PR1 already increments this on run completion).
- **Risk:** HTMX swap of the prompt card loses Alpine state mid-edit.
  **Mitigation:** The card swap only fires on version *change* — the
  editing context is by definition switching anyway, so losing in-flight
  unsaved edits is correct behavior. The PR1 auto-save on debounce
  flushes before the user can pick a new version in practice; an
  explicit `save()` call on `htmx:beforeSwap` belt-and-suspenders the
  case where they don't wait.
- **Risk:** Tab-sync coupling on `$root.mode` means cards mounted via
  HTMX must re-bind after swap.
  **Mitigation:** Cards explicitly use `:class="$root.mode === 'prompt' && 'active'"`
  on their tab buttons and `x-show="$root.mode === 'prompt'"` on body
  regions. Alpine re-binds on insertion; tested by the integration test.

## Slicing

PR2 lands as a single PR with the following commit boundaries
(suggested, for reviewer convenience):

1. **Extract `_player_overlay.html`.** Move clip_detail's timeline into
   the shared partial; verify clip_detail unchanged with the regression
   test.
2. **`_studio_player.html` upgrade.** Replace native controls with the
   custom transport + shared overlay; one row only at this stage.
3. **Annotation-card reuse.** Add `show_history` flag to
   `_anno_panels.html`. Add `panels_from_studio_run` adapter. Rewrite
   `_studio_run_output.html` to include the shared partial. Both clip
   view and studio Output tab render via one path now.
4. **`_studio_prompt_card` route + version picker.** Side-aware route
   serving the cur card via HTMX; chip in the header swaps the body.
5. **Cmp card materialization.** `+ Compare` button, cmp slot, second
   range row in overlay.
6. **Tab sync.** Lift `mode` from card to page.
7. **`lineDiff` + diff view.** Port the algorithm, add `cmpDiff`
   component, render `_studio_diff.html` inside cmp body on toggle.
8. **CSS for new affordances.** Diff colors, chip styling, legend.
9. **Tests + ADR.**

If any of these split poorly under TDD, the implementation plan will
re-slice them; this list is a hint, not a contract.

## Open questions

None blocking. Items deferred to later PRs:

- Deep-linking `?mode=prompt|output` and `?diff=1` to also survive
  reload. PR2 only deep-links the version selectors; mode/diff stay
  ephemeral. Cheap to add later if it's worth it.
- Stacked / unified diff layouts: deferred per the umbrella spec.
- Run history viewer: still latest-only per umbrella spec.
