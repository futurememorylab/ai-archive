# 0026. Prompt Studio: testbenches, runs, optional gold, CatDV-optional

- **Date:** 2026-05-26
- **Status:** Accepted

## Context

The annotator's only way to iterate on a prompt today is to publish a new
version (via the prompts UI from 0010), kick off a regular annotation job
against real CatDV clips, and eyeball the results in the annotate panels.
That couples prompt experimentation to the production write path, requires
CatDV to be online and a seat to be free (see the seat discipline in
`CLAUDE.md`), and offers no way to A/B two prompt versions over the same
fixed clip set. A dedicated "Prompt Studio" surface is being added on top
of existing prompt-management (0010), the proxy/cache layer (0014, 0017),
and the Gemini annotator pipeline.

Eight load-bearing design calls fell out of the brainstorm:
(a) whether Studio reuses the existing `jobs` / `annotations` tables or
introduces its own; (b) the shape of a "testbench" (flat vs nested,
clip-only vs upload-also); (c) where the optional gold/reference lives
and how it is typed, since a future evals layer will read it;
(d) whether to snapshot the prompt body into a run row or rely on prompt
versions being immutable; (e) how comparison renders two runs (or run vs
gold) — diff engine vs side-by-side; (f) whether Studio requires CatDV
to be online; (g) where uploaded videos live and how they're cleaned up;
(h) whether comparison supports run-vs-run, run-vs-gold, or both.

## Alternatives

(a) Reuse `jobs` + `job_items` + `annotations` (smallest schema delta,
worker code shared verbatim), versus introduce parallel `studio_runs` +
`studio_run_items` tables that call into the same per-item pipeline
primitives. (b) Flat list of clip refs with tags; nested folders + clip
refs; or saved CatDV query that resolves at run time. (c) Gold as a TEXT
column for "manual description"; gold as a JSON column with arbitrary
shape; or no gold at all and rely on run-vs-run only. (d) Snapshot
prompt body into each run row (full reproducibility even if version
mutability ever regresses); or FK to `prompt_versions.id` and rely on
the immutability invariant established by 0010. (e) Per-field structured
diff (compute agreement metrics against gold); unified text diff; or
side-by-side with no automatic highlighting. (f) Studio requires CatDV;
or Studio runs standalone with uploads, and gracefully degrades when
CatDV is unreachable mid-flight. (g) Local disk under a Studio uploads
dir; ephemeral per-session; or a pluggable storage interface (local
now, object-store later). (h) Run-vs-run only; run-vs-gold only; or
both (gold optional per item).

## Decision

(a) **Separate Studio tables.** New `testbenches`, `testbench_folders`,
`testbench_items`, `studio_runs`, `studio_run_items` (schema in the
spec). The per-item pipeline (`proxy_resolver` → `ai_store` →
`gemini.annotate`) is reused as-is from the annotator service, factored
into a small helper if needed. Studio gets its own serial worker mirroring
`services/annotator.run_job` rather than enqueuing into `jobs` — keeps
Studio outputs out of the production `annotations` table and avoids
polluting the CatDV-bound review/write pipeline with sandbox results.
(b) **Nested folders + clip refs.** A testbench owns a tree of folders;
each folder holds testbench items. Each item has a `source_kind`
discriminator (`upload` | `catdv_clip`) so an uploaded MP4 and a CatDV
clip ref can sit side-by-side in the same folder. Saved-query testbenches
were rejected because the clip set could drift between runs, breaking
the reproducibility evals will eventually depend on.
(c) **Gold is an optional JSON column on the testbench item.** Today's
usage is a hand-authored description; tomorrow's evals will add
structured expected fields or rubric scores without a migration. TEXT
was rejected for that reason. Gold lives on `testbench_items.gold_json`,
not on the clip — the same CatDV clip can have different gold values in
different testbenches.
(d) **FK only, no body snapshot.** `studio_runs.prompt_version_id`
references `prompt_versions.id`. Reproducibility piggybacks on the
existing 0010 invariant: production and archived versions are immutable;
editing forces a new version. Snapshotting the body again here would
duplicate the same information and rot if the two ever drifted.
(e) **Side-by-side render, no automatic diff.** Comparison view renders
both outputs (or output vs gold) using the same Jinja partials the
production annotate panels use; reviewer eyeballs the difference.
Per-field structured diff was deferred — once evals exist, the scorer
can compute agreement rates without re-implementing a diff UI.
(f) **CatDV is optional.** The app boots and serves Studio routes when
CatDV is unreachable or unconfigured. CatDV clip refs in a testbench
resolve via a fallback chain at run time: (1) live archive lookup,
(2) `proxy_cache` for media + cached `archive.get_clip` results,
(3) `ai_store` for a previously uploaded reference. If all three miss,
the run item is marked `unacceptable` with `unacceptable_reason` and the
run continues with the next item. The production annotator routes
keep their existing CatDV-required behavior; only Studio is relaxed.
(g) **Local disk, append-only for now.** Uploads land in
`var/studio_uploads/<uuid>.<ext>`; the testbench item stores a relative
path. No garbage collection in this iteration — uploads survive testbench
edits so a historical run can still resolve its inputs. A retention /
GC policy is explicitly deferred (open item in the spec). Pluggable
storage was rejected as premature; the local path is a one-line
abstraction to widen later if needed.
(h) **Both, gold optional.** Run-vs-gold compares any item that has
`gold_json` set; run-vs-run compares two runs of the same testbench
regardless of gold. The UI exposes both modes; the data model doesn't
distinguish them (same `studio_run_items` rows feed both views).

## Consequences

(a) Two parallel run pipelines (`jobs` for production, `studio_runs` for
sandbox) is more code than reusing `jobs`, but the alternative would
require either pushing sandbox runs into a table that also feeds CatDV
writeback (a footgun — sandbox output could leak into a draft and then a
publish), or carrying a `mode = 'studio' | 'production'` flag through
every join in `annotator.py`, `routes/review.py`, and the draft view.
The Studio worker is a ~50-line clone of `annotator.run_job` over the
new tables; the shared per-item pipeline is what actually does the work.
(b) Folder trees add recursive UI (folder rename, drag-into-folder, etc.)
that flat lists wouldn't. Accepted because the operator is curating a
small number of testbenches by hand and trees match how CatDV organizes
material; the implementation cost is one self-referential
`testbench_folders.parent_id` column and a recursive CTE for listing.
(c) Storing gold per testbench item (not per clip) means the same CatDV
clip can carry one gold for an "indoor scenes" testbench and a different
gold for a "1930s exteriors" testbench — intentional, because the
"correct" annotation is task-dependent. JSON gives the future evals layer
freedom; the API today validates only that it parses as JSON and applies
no shape constraints.
(d) Relying on prompt-version immutability means a Studio run is
reproducible exactly as long as 0010's invariants hold. If a future
change ever permitted in-place edits on production versions, Studio
historical comparisons would silently lie. The `idx_one_prod_per_prompt`
partial index and the route-level rejection of mutations on
production/archived versions are the two guards keeping this honest.
(e) Side-by-side with no diff is the lowest-cost rendering and gets the
feature shippable. It pushes the cognitive work onto the reviewer for
now; once evals exist, automatic agreement metrics replace eyeballing
for the bulk-comparison case and side-by-side is reserved for spot
checks. The decision to keep diff out of v1 is explicitly revisitable.
(f) Boot-without-CatDV is a behavioral change that touches startup wiring
(`backend/app/context.py` already handles the boot-failure case per 0023
by keeping the client alive for retry; Studio just needs to not refuse
to serve when the client never logged in). Production routes keep
asserting CatDV availability via the existing guards; Studio routes do
not. The resolver chain is intentionally fail-soft: marking an item
`unacceptable` is preferable to failing the whole run, because Studio
runs are often the only way the operator notices a clip's proxy was
never cached in the first place.
(g) Append-only uploads means the disk grows monotonically with use.
Acceptable for the single-operator workload (a few MB per upload, a few
hundred uploads expected over a year). When this becomes painful, a
sweep can identify orphans (no referencing `testbench_items` row, no
referencing `studio_run_items` row) and delete them; the data model
supports this query without schema change.
(h) Gold-optional means run-vs-gold queries skip items lacking gold and
the comparison UI labels them ("no reference" rather than "no diff").
The evals layer (out of scope here) will likely require gold for the
items it scores, but that's a runtime filter, not a schema constraint.

**Evals-readiness checklist** (so a future contributor knows the bones
were laid with evals in mind): gold is JSON (extensible without
migration); run items snapshot the clip set at execution time (testbench
edits don't retroactively change history); prompt versions are immutable
(0010 invariant carried forward); uploaded files are not GC'd
(historical runs stay resolvable); the per-item `unacceptable` state
gives an eval scorer a way to exclude unresolvable inputs without
losing them. A scorer table or per-item `score_json` column can be
added later as pure addition.
