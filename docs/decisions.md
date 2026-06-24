# Architecture Decisions

Individual decisions are recorded as MADR-style files under
[`docs/adr/`](./adr/), one decision per file. This file is the index.

**The ADR log is the audit trail, not the guidelines.** The distilled,
always-true rules live in
[`docs/architecture-invariants.md`](./architecture-invariants.md) — read
that to understand the system; read an ADR only for the *why* behind a
specific rule. ADRs are immutable history; the invariants page is the
living canon.

## How decisions are tracked

Documentation is tiered by **lifespan**, and each kind of write goes to
its natural home — so the ADR log stays sparse and the invariants page
stays short:

| Lifespan | Lives in | Example |
|---|---|---|
| Always true | `architecture-invariants.md` | "three cache layers, graceful miss" |
| A pivotal/irreversible choice | an **ADR** | "split AppContext into CoreCtx + LiveCtx" |
| True until the screen is redesigned | `docs/specs/` | "batches hub layout" |
| A debugging lesson | the PR + a `*-lessons.md` | "the MTU black-hole root cause" |

### When to write an ADR (the admission bar)

Write an ADR **only** if the decision does at least one of:

1. **Establishes or changes an invariant** (then also update
   `architecture-invariants.md`).
2. Is **irreversible or expensive to reverse** (schema/migration, a public
   contract, a cloud-topology choice).
3. Is **cross-cutting** — future PRs in unrelated areas must conform to it.

If the decision only shapes one screen or fixes one bug, it is **not** an
ADR — it's a spec or a PR note. "Would a future contributor ask *why*?" is
necessary but not sufficient; the bar above is the test.

### ADR template additions

Every ADR header carries a **`Lifespan:`** classifier so the synthesis
pass (below) is mechanical:

- `Invariant` — established/changed a rule on the invariants page (cite it).
- `Feature` — a design call scoped to one capability (candidate to fold
  into a spec).
- `Lesson` — a root-cause/postmortem kept for the record.
- `Superseded` — replaced; points forward to the entry that replaced it.

Keep the existing MADR-lite sections (`Context` / `Alternatives` /
`Decision` / `Consequences`).

### Synthesis pass (the regular architecture review)

Every **~20 new ADRs, or monthly**, run a synthesis pass and log it under
*Synthesis log* below. The checklist:

1. **Promote** — did any new ADR establish a durable invariant? Add/edit
   the line in `architecture-invariants.md` and footnote the ADR.
2. **Collapse** — did a refinement chain (`refines`/`supersedes`)
   converge? The invariants page points to the live head; mark the
   intermediate ADRs' `Lifespan: Superseded` and banner them forward.
3. **Prune** — fix the index annotations so the live entry of each chain
   is obvious at a glance.

The artifact you review is the ~25-line invariants page, not 100+ ADRs —
that's what makes the review actually happen.

## Synthesis log

| Date | Through ADR | Notes |
|---|---|---|
| 2026-06-24 | 0114 | Initial synthesis. Extracted `architecture-invariants.md` (26 invariants) from the first 114 ADRs. Tagged every ADR with a `Lifespan` (62 Invariant / 42 Feature / 6 Lesson / 2 Superseded). Bannered the write-back (0091–0098), versions (0099–0101) and timeline-band (0106–0107) chains forward to their invariant; marked 0043 `Superseded` by 0112 and 0074 `Lesson` (root cause corrected by 0076). No ADR content was rewritten — history preserved. Spec-fold backlog recorded below. |

### Spec-fold backlog (from the 2026-06-24 pass)

The 42 `Lifespan: Feature` ADRs are screen/flow designs — durable until that
surface is redesigned, not architecture invariants. They are **not** rewritten
or deleted (the audit trail stays intact); a future pass may consolidate each
cluster into one living `docs/specs/` document and leave the ADRs as the
historical record. Candidate clusters, largest first:

- **Prompt Studio** — 0033, 0034, 0037, 0038, 0039, 0040, 0049, 0050, 0051, 0055, 0061
- **Draft review** — 0035, 0036, 0054, 0057
- **Clip annotate / clip-list UI** — 0012, 0013, 0017, 0030, 0031
- **Write-back status surfaces** (also Invariant-8 chain members) — 0092, 0094, 0095, 0096
- **Timeline & playback bands** — 0106, 0107, 0108
- **Batches hub** — 0052, 0053
- **Image (still) clips** — 0028, 0029
- **Standalone** (no cluster) — 0003, 0005, 0008, 0009, 0011, 0045, 0072, 0073, 0078, 0102, 0114

## Index

| NNNN | Date | Title |
| ---- | ---- | ----- |
| 0001 | 2026-05-18 | [Python-only stack, no Node frontend](./adr/0001-python-only-stack-no-node-frontend.md) |
| 0002 | 2026-05-19 | [AIInputStore port distinct from ArchiveProvider](./adr/0002-aiinputstore-port-distinct-from-archiveprovider.md) |
| 0003 | 2026-05-19 | [PR 3 — single migration file, clip TTL keyed off CanonicalClip.fetched_at](./adr/0003-pr3-single-migration-file-clip-ttl.md) |
| 0004 | 2026-05-19 | [PR 4 — enqueue is atomic with mark_applied; conflict locus is the adapter](./adr/0004-pr4-enqueue-atomic-conflict-locus-adapter.md) |
| 0005 | 2026-05-19 | [PR 5 — primary pin vs. workspace_clips, FK migration, no fetch_media](./adr/0005-pr5-primary-pin-workspace-clips-fk-migration.md) |
| 0006 | 2026-05-19 | [PR 6 — cache-layer signal sources, audit semantics, and LRU safety](./adr/0006-pr6-cache-layer-signals-audit-lru.md) |
| 0007 | 2026-05-19 | [PR 7 — Filesystem archive adapter](./adr/0007-pr7-filesystem-archive-adapter.md) |
| 0008 | 2026-05-20 | [UI MVP — five decisions](./adr/0008-ui-mvp-five-decisions.md) |
| 0009 | 2026-05-20 | [Media prefetch + cache UI wiring (PR 8)](./adr/0009-pr8-media-prefetch-cache-ui-wiring.md) |
| 0010 | 2026-05-21 | [Prompt management: replace templates with versioned prompts](./adr/0010-prompt-management-versioned-prompts.md) |
| 0011 | 2026-05-21 | [Prompt management: post-merge polish (styling, alpine init, duplicate dialog)](./adr/0011-prompt-management-post-merge-polish.md) |
| 0012 | 2026-05-21 | [Clip Annotate UI: Draft view, scope toggle, in-page annotate flow](./adr/0012-clip-annotate-ui-draft-view-scope-toggle.md) |
| 0013 | 2026-05-22 | [Clip list filters: Cache + Annotations dropdowns, local-first resolution](./adr/0013-clip-list-filters-cache-annotations-dropdowns.md) |
| 0014 | 2026-05-22 | [Local-filesystem proxy resolution (deploy on the CatDV host)](./adr/0014-local-filesystem-proxy-resolution.md) |
| 0015 | 2026-05-22 | [Offline fallback: auto-degrade + manual reconnect](./adr/0015-offline-fallback-auto-degrade-manual-reconnect.md) |
| 0016 | 2026-05-23 | [Gemini Live clip assistant: browser-direct + Developer API](./adr/0016-gemini-live-clip-assistant-browser-direct.md) |
| 0017 | 2026-05-23 | [Offline mode: keep Annotate available when proxy is cached; marker nav follows active scope](./adr/0017-offline-mode-annotate-available-marker-nav-scope.md) |
| 0018 | 2026-05-23 | [Gemini Live clip assistant: browser-direct WSS, separate view-model](./adr/0018-gemini-live-clip-assistant-wss-view-model.md) |
| 0019 | 2026-05-23 | [Tier 1 tooling: ruff format, basedpyright with baseline, pre-commit](./adr/0019-tier1-tooling-ruff-basedpyright-precommit.md) |
| 0020 | 2026-05-23 | [Typed `get_ctx` accessor (PR E of arch plan)](./adr/0020-typed-get-ctx-accessor.md) |
| 0021 | 2026-05-23 | [PR H — cache services construction collapse](./adr/0021-pr-h-cache-services-construction-collapse.md) |
| 0022 | 2026-05-24 | [Tier 2 architecture execution](./adr/0022-tier-2-architecture-execution.md) |
| 0023 | 2026-05-25 | [Boot-time login failures keep the CatDV client alive for retry](./adr/0023-boot-login-failures-keep-client-for-retry.md) |
| 0024 | 2026-05-25 | [Browser-triggered graceful shutdown (shutdown button)](./adr/0024-shutdown-button.md) |
| 0025 | 2026-05-25 | [Unified video-list component + CatDV poster thumbnails](./adr/0025-video-list-thumbnails-and-shared-component.md) |
| 0028 | 2026-05-26 | [Image (still) clip support via original-media fetch](./adr/0028-image-clip-support-via-original-media.md) |
| 0029 | 2026-05-26 | [Image annotation prompt + prompt media_kind](./adr/0029-image-annotation-prompt-and-media-kind.md) |
| 0030 | 2026-05-26 | [UI responsiveness: local assets, click feedback, cache scroll](./adr/0030-ui-responsiveness-local-assets-feedback-scroll.md) |
| 0031 | 2026-05-26 | [Cache page pagination shared with Clips](./adr/0031-cache-pagination-shared-with-clips.md) |
| 0032 | 2026-05-26 | [Bound uvicorn graceful shutdown so open streams can't leak the seat](./adr/0032-shutdown-graceful-timeout.md) |
| 0033 | 2026-05-26 | [Prompt Studio PR1 — shell and run loop](./adr/0033-prompt-studio-pr1-shell-and-run-loop.md) |
| 0034 | 2026-05-27 | [Prompt Studio PR2 — version compare](./adr/0034-prompt-studio-pr2-version-compare.md) |
| 0035 | 2026-05-27 | [Draft review & accept UI](./adr/0035-draft-review-accept-ui.md) |
| 0036 | 2026-05-27 | [Fold draft review into the clips list (supersedes /review page)](./adr/0036-fold-review-into-clips-list.md) |
| 0037 | 2026-05-27 | [Studio: shared player chrome + focused clip in URL](./adr/0037-studio-shared-player-chrome-and-focused-clip-url.md) |
| 0038 | 2026-05-28 | [Prompt Studio output renders via review_items, not raw output_json](./adr/0038-studio-output-via-review-items.md) |
| 0039 | 2026-05-28 | [Prompt Studio PR3 — polish (run-button cancel, empty/error shells, design-language audit)](./adr/0039-prompt-studio-pr3-polish.md) |
| 0040 | 2026-05-28 | [Studio layout toggles (list / player / prompt-output position)](./adr/0040-studio-layout-toggles.md) |
| 0041 | 2026-05-29 | [Bound the boot-time CatDV login with a short, separate timeout](./adr/0041-bound-boot-login-timeout.md) |
| 0042 | 2026-05-30 | [Narrow provider errors — never treat exceptions as "not found"](./adr/0042-narrow-provider-errors-never-treat-exceptions-as-not-found.md) |
| 0043 | 2026-05-30 | [Gemini Live API key browser exposure — accepted risk](./adr/0043-gemini-live-api-key-exposure-accepted-risk.md) |
| 0044 | 2026-05-30 | [Migration numbering and the 0011 gap](./adr/0044-migration-numbering-and-the-0011-gap.md) |
| 0045 | 2026-05-30 | [Bulk "Annotate selected" — one job per media kind, ephemeral progress indicator](./adr/0045-bulk-annotate-selected.md) |
| 0046 | 2026-05-30 | [No N+1 — batch repository reads with WHERE IN](./adr/0046-no-n-plus-one-batch-with-where-in.md) |
| 0047 | 2026-05-30 | [Split AppContext into CoreCtx + LiveCtx; unify route deps](./adr/0047-corectx-livectx-split.md) |
| 0048 | 2026-05-31 | [Alpine.store (not _x_dataStack) for shared studio state; one HTMX↔Alpine lifecycle helper](./adr/0048-alpine-store-not-x-data-stack-for-shared-state.md) |
| 0049 | 2026-06-02 | [Studio prompt editing uses explicit save (matches the prompt screen)](./adr/0049-studio-explicit-save-matches-prompt-screen.md) |
| 0050 | 2026-06-02 | [Studio compare uses a word-level inline diff (Word track-changes), extended in-house](./adr/0050-word-level-inline-diff-in-house.md) |
| 0051 | 2026-06-02 | [Studio resizable panes — hand-rolled splitters, nested 3-column right layout](./adr/0051-studio-resizable-panes-hand-rolled.md) |
| 0052 | 2026-06-02 | [Batches hub — design calls](./adr/0052-batches-hub.md) |
| 0053 | 2026-06-02 | [New-batch picker (supersedes ADR 0052 redirect)](./adr/0053-batches-new-batch-picker.md) |
| 0054 | 2026-06-02 | [Draft review redesign — Alpine-data-driven cards + batch Review→](./adr/0054-draft-review-redesign.md) |
| 0055 | 2026-06-02 | [Studio output compare is an aligned scene table + linked timeline](./adr/0055-studio-aligned-scene-compare.md) |
| 0056 | 2026-06-04 | [Shared clip-picker component (studio archive picker reuses the batch picker)](./adr/0056-shared-clip-picker-component.md) |
| 0057 | 2026-06-04 | [Draft review: buffered Save/Cancel edits + applied/deleted item lifecycle](./adr/0057-draft-review-buffered-edit-and-item-lifecycle.md) |
| 0058 | 2026-06-07 | [Run telemetry — local-first with deferred cloud pipeline](./adr/0058-run-telemetry-local-first.md) |
| 0059 | 2026-06-07 | [Actual run cost on the UI — total-spend semantics + shared usd filter](./adr/0059-actual-run-cost-ui-surfaces.md) |
| 0060 | 2026-06-07 | [Structural erosion pre-commit gate (complexity-concentration ratchet)](./adr/0060-structural-erosion-gate.md) |
| 0061 | 2026-06-08 | [Prompt Studio uploaded clips — synthetic high-offset id + thin source guards](./adr/0061-prompt-studio-uploaded-clip-identity.md) |
| 0062 | 2026-06-09 | [Two-mode popover menu module + design-language enforcement guard](./adr/0062-popover-menu-module-and-design-language-guard.md) |
| 0063 | 2026-06-09 | [One modal vocabulary: ui.modal shell + canonical .modal-* classes](./adr/0063-one-modal-vocabulary-ui-modal.md) |
| 0064 | 2026-06-09 | [Clip card consolidation (Candidate D) explored and declined](./adr/0064-clip-card-consolidation-declined.md) |
| 0065 | 2026-06-09 | [Thumbnail service short-circuits when clip has no cached metadata](./adr/0065-thumbnail-service-skips-network-without-clip-cache.md) |
| 0066 | 2026-06-09 | [Cloud Run pinned to one instance; SQLite persisted via Litestream](./adr/0066-cloud-run-single-instance-litestream.md) |
| 0067 | 2026-06-10 | [onetun packaged from its GitHub release binary, not a container image](./adr/0067-onetun-packaged-from-release-binary.md) |
| 0068 | 2026-06-10 | [CatDV connection is manual on-demand on Cloud Run](./adr/0068-catdv-manual-connect.md) |
| 0069 | 2026-06-10 | [Cloud media cache: AI-store-only on GCP](./adr/0069-cloud-media-cache-ai-store.md) |
| 0070 | 2026-06-10 | [GCS proxy upload is content-aware, not presence-only](./adr/0070-gcs-content-aware-upload.md) |
| 0071 | 2026-06-11 | [Durable GCS-backed thumbnail cache](./adr/0071-durable-thumbnail-cache.md) |
| 0072 | 2026-06-11 | [Thumbnail poster cache + bounded download concurrency](./adr/0072-thumbnail-poster-cache.md) |
| 0073 | 2026-06-11 | [Cloud cache UI: hide local-media layer, act on the ai-store](./adr/0073-cloud-cache-ui-ai-store-only.md) |
| 0074 | 2026-06-11 | [Cloud CatDV writeback: MTU hygiene, and the real cause (WireGuard peer-key collision)](./adr/0074-onetun-mtu-1380-gcp-writeback.md) — *root cause partially superseded by 0076* |
| 0075 | 2026-06-11 | [onetun is app-supervised (VPN status + toggle), default off](./adr/0075-onetun-app-supervised.md) |
| 0076 | 2026-06-11 | [Cloud CatDV writeback: root cause corrected — outbound path-MTU black-hole, not a peer-key collision](./adr/0076-cloud-writeback-root-cause-corrected-path-mtu.md) |
| 0077 | 2026-06-12 | [Cloud Run scale-to-zero with graceful seat + VPN release](./adr/0077-cloud-run-scale-to-zero.md) |
| 0078 | 2026-06-12 | [Connection pill redesign + on-demand VPN re-probe](./adr/0078-vpn-on-demand-reprobe-and-connection-pill.md) |
| 0079 | 2026-06-14 | [GitHub Flow: kanban board + gh-design / gh-handoff skills](./adr/0079-github-flow-board.md) |
| 0080 | 2026-06-14 | [Centralised enumeration: code registry + EnumService + editable model catalog](./adr/0080-centralised-enumeration.md) |
| 0081 | 2026-06-15 | [Upload orphan-GC on set removal (reference-count)](./adr/0081-upload-orphan-gc-on-set-removal.md) |
| 0082 | 2026-06-15 | [Studio uploads always pushed to the AI store, not only in cloud mode](./adr/0082-uploads-always-pushed-to-ai-store.md) — *supersedes upload-gating part of 0069* |
| 0083 | 2026-06-15 | [HTMX-only wiring for fetch-injected subtrees that own an x-data root (compare Diff double-bind fix)](./adr/0083-htmx-only-wiring-for-injected-xdata-roots.md) — *refines 0048* |
| 0084 | 2026-06-13 | [Access control via Google Cloud IAP + an app-side roles layer (not app-level OAuth)](./adr/0084-iap-access-control.md) |
| 0085 | 2026-06-14 | [App-side roles + admin console on top of IAP (2 roles admin/member, default-deny gate, app-never-touches-the-Group)](./adr/0085-iap-roles-admin-console.md) |
| 0086 | 2026-06-15 | [Annotate-feedback resilience: SSE replay-on-connect + prefetch orphan recovery (re-read persisted truth, don't buffer events)](./adr/0086-annotate-feedback-resilience-sse-replay-and-prefetch-orphan-recovery.md) |
| 0087 | 2026-06-15 | [Resumable proxy download over the low-MTU cloud tunnel (Range-resume + completeness check; no truncated uploads)](./adr/0087-proxy-download-resume-over-tunnel.md) |
| 0088 | 2026-06-16 | [Filtered clip list skips un-hydratable clips instead of 502-ing the page (+ HTMX error toast)](./adr/0088-filtered-clip-list-skips-unhydratable-clips.md) — *refines 0042* |
| 0089 | 2026-06-16 | [Manual innerHTML/insertAdjacentHTML into a live Alpine tree uses wireHtmx, not reinit (avoid double-bound directives)](./adr/0089-manual-innerhtml-insertion-uses-wirehtmx-not-reinit.md) — *refines 0048* |
| 0090 | 2026-06-17 | [Write-back routes notes/bigNotes to top-level clip properties, not the user-fields map (fixes silent notes data loss)](./adr/0090-writeback-notes-are-top-level-clip-properties.md) |
| 0091 | 2026-06-17 | [Write-back: uniform retry ceiling, append idempotency, freshest-etag conflicts, legible sync status (drawer + draft poller)](./adr/0091-writeback-retry-ceiling-idempotency-and-status-legibility.md) — *refines 0042* |
| 0092 | 2026-06-17 | [Live write-back status surfaces: always-visible topbar sync chip, clip-grouped drawer + Retry all, transient-only connection-chip poll](./adr/0092-live-writeback-status-surfaces.md) — *refines 0091* |
| 0093 | 2026-06-17 | [review_items.synced_at server-confirm stamp (applied≠on-server) + topbar annotate-phase breakdown (Caching/Annotating/queued)](./adr/0093-synced-at-and-topbar-phase-breakdown.md) — *refines 0091* |
| 0094 | 2026-06-17 | [Accept & apply advances to the next clip in the review queue (syncing message becomes the terminal state) + batches-table last-column divider fix](./adr/0094-accept-apply-advances-the-review-queue.md) — *refines 0093* |
| 0095 | 2026-06-17 | [Write-back status surfaces stay honest + quiet while CatDV is offline: batch "Syncing N" (pending_operations-sourced, consistent with the chip), offline drawer banner, self-limiting/right-cadence chip polls, inline chip count (no flicker) as a rounded pill, clips-list OOB pill updates while running, synchronous retry reset, "Open" → batch files](./adr/0095-writeback-status-surfaces-honest-while-offline.md) — *refines 0092/0093/0094* |
| 0096 | 2026-06-17 | [Batches surface failed/conflict write-backs as a "N failed to sync" problem state (pending_operations-sourced) instead of masking a stuck queue as green "Applied"](./adr/0096-batch-surfaces-failed-conflict-writebacks.md) — *refines 0095* |
| 0097 | 2026-06-17 | [Write-back accumulates multiple appends to the same note/field within one ChangeSet (running per-target text) instead of the last append clobbering the rest](./adr/0097-writeback-accumulates-multiple-appends-per-target.md) — *refines 0091* |
| 0098 | 2026-06-17 | [Retrying a conflicted write-back re-bases on the live clip (clear stale expected_etag for conflict rows only) so the drawer's Retry actually resolves instead of re-conflicting forever](./adr/0098-conflict-retry-rebases-on-live-clip.md) — *refines 0091* |
| 0099 | 2026-06-17 | [Clip version history: publish snapshots (clip_versions, local-canonical + pragafilm.anno_version breadcrumb), re-run replaces the working draft, restore-forward, one publish-state headline (Live/Draft/Publishing/Failed); built as a snapshot layer on the existing write queue/SyncEngine](./adr/0099-clip-version-history-publish-snapshots.md) — *parts superseded by 0100* |
| 0100 | 2026-06-17 | [Publishing audit: drop the CatDV provenance field (it 500'd every publish), switch versions by re-activation (Make live, no new version) instead of publish-forward, parse-first CatDV error classification, mark_live supersedes orphaned publishing siblings, guarded history delegation](./adr/0100-publishing-audit-drop-provenance-and-reactivate-switching.md) — *supersedes parts of 0099* |
| 0101 | 2026-06-17 | [Faithful version switching via a new ReconcileMarkers op: "Make live" rolls back only the markers WE authored (drop our other-version markers, re-assert the target's, preserve pre-existing/human ones) and derives frames at the clip's real fps (fps=0.0 sentinel) — fixing leftover markers and non-25fps duplication; plus clip-detail header fixes (wrap .anno-scope-row; rename the two "History" controls to "Versions" and "Live sessions")](./adr/0101-faithful-version-switching-reconcile-markers.md) — *refines 0100* |
| 0102 | 2026-06-17 | [Drop "last sign-in" tracking from the admin console (UI + backend); gate the invited→active flip behind get_gate_state so browsing is read-only (fixes #73 per-request write contention)](./adr/0102-drop-last-seen-from-admin-console.md) — *supersedes last-seen aspects of 0085* |
| 0103 | 2026-06-19 | [Promote-by-tag deploys: push to main → staging (the single build, SHA-tagged); a v* tag promotes that exact SHA image to prod (no rebuild, guarded that the image exists); gated on github.ref so dispatch-from-main redeploys staging and dispatching a tag promotes](./adr/0103-promote-by-tag-deploys.md) |
| 0104 | 2026-06-19 | [Staging gets a real VPN/CatDV connection (CATDV_OFFLINE=false, manual connect-on-demand), reusing prod's WireGuard peer key + secrets — so staging & prod tunnels are mutually exclusive and staging writes to the real CatDV catalog; relaxes the staging-never-takes-the-seat property of 0066/0077](./adr/0104-staging-vpn-catdv-enabled.md) — *relaxes 0066/0077* |
| 0105 | 2026-06-19 | [Staging SQLite is persisted via its OWN Litestream replica path (gcs://catdv-annotator-db/staging, distinct from prod's .../litestream) + mandatory --no-cpu-throttling so it flushes under scale-to-zero; relaxes the ephemeral-staging-DB aspect of 0066/0077](./adr/0105-staging-db-persisted-litestream.md) — *relaxes 0066/0077* |
| 0106 | 2026-06-18 | [Published AND draft timeline bands are reactive (opt-in `x_for` Alpine loop, clip-detail only; studio stays static) and the view auto-switches to Published scope after a confirmed sync — so published markers turn orange without a reload, and freshly-annotated draft markers appear without a reload, instead of needing a refresh](./adr/0106-published-timeline-reactive-and-scope-auto-switch-on-publish.md) |
| 0107 | 2026-06-19 | [Draft scope shows the published band (orange) stacked above the draft band (blue) — split only when published markers exist (`has-published-markers` class = clip has markers in CatDV, not the stricter "we published a version" notion), so unpublished clips keep a full-height draft band; timecode labels get a token-based backdrop pill so they stay legible over any band in any theme](./adr/0107-draft-scope-split-band-and-timecode-contrast.md) — *revises 0106 R1* |
| 0108 | 2026-06-22 | [Annotation column follows playback: reuse `isMarkerActive` as the single active-predicate for timeline + column (highlight via inset box-shadow, no reflow); comfort-band nearest-edge minimal-movement auto-scroll (20% band / 4000ms manual-pause / 1-viewport smooth-vs-instant), anchor = first active card in `in_secs` order; clip-page-only (`review_mode`-gated, Studio excluded); pure helpers + Python text-scan guards, no JS runner](./adr/0108-annotation-follow-playback.md) |
| 0109 | 2026-06-22 | [Gemini Live stability: wait for the server's `setupComplete` before sending any content/audio (was a race that closed the socket 1007/1008 — the root of "sometimes works, sometimes not"); handle barge-in (`interrupted` → flush queued playback) and resume a suspended output AudioContext; split `initial_context_turn` out of `setup_payload` into its own `/session-config` field (pure setup frame, no frontend delete-dance) and drop the redundant `token` / unused `inactivity_s` / always-zero `search_calls`](./adr/0109-live-setup-complete-handshake-and-payload-split.md) |
| 0110 | 2026-06-22 | [Live output audio: create+resume+prime the playback `AudioContext` synchronously inside the Live-button gesture (WebKit only unlocks a suspended context during a user gesture; the prior `resume()` from the WS callback was ignored → silent Gemini voice on Safari, and on the 2nd session everywhere); reuse one long-lived context across sessions — teardown `suspend()`s it, never `close()`s it — so the next session inherits an already-unlocked context instead of a fresh suspended one](./adr/0110-live-output-audiocontext-gesture-unlock-and-reuse.md) |
| 0111 | 2026-06-22 | [Playwright walkthrough tests run the FastAPI app in-process (uvicorn on a daemon thread, real socket on 127.0.0.1:8766) and inject a numeric-keyed `FakeArchive` + ffmpeg-seeded proxy via `install_live_ctx`, because the fs provider's path-string keys fail `int(clip.key[1])` and can't render the UI; publish is exercised at the durable write-queue level (real `clip_versions`/`pending_operations` rows), not via a SyncEngine round-trip; entry point is the `/e2e` skill](./adr/0111-walkthrough-tests-in-process-injection.md) — *cross-ref 0001* |
| 0112 | 2026-06-22 | [Gemini Live: replace the browser-exposed raw `GEMINI_API_KEY` with a short-lived, single-use, config-bound **ephemeral token** minted server-side (`v1alpha auth_tokens`), presented by the browser via `?access_token=` against `BidiGenerateContentConstrained`; the raw key only auths the mint call. ADR 0043's "1007 / key not valid" was a wrong endpoint+version+param triad (`v1beta` + `BidiGenerateContent` + `?key=`), not a Google limit — proven by spike. System prompt + tool declarations are bound into the token and withheld from the browser](./adr/0112-gemini-live-ephemeral-tokens.md) — *supersedes 0043* |
| 0113 | 2026-06-22 | [Open the IAP edge to allAuthenticatedUsers (never allUsers) so per-user management is fully app-side; IAP keeps authentication, the app's default-deny gate is the sole authorization authority (app never edits the Google edge — no credential); includes the denial-page POST-redirect-GET + absolute-logout-link 405 fix found during cutover](./adr/0113-open-iap-edge-app-managed-access.md) — *supersedes the per-user-allowlist assumption of 0084/0085* |
| 0114 | 2026-06-23 | [Annotate caching writes a `prefetch_queue` *visibility row* (born `downloading`, never claimed by the worker) so it shows on the queue page with live %, identical to a Cache-button row; the annotator keeps its own inline download rather than routing through the single-at-a-time worker. Button % + reload-resume share `cacheProgressForClip()` / `GET /api/jobs/active-for-clip`. Resume hook is `_annotateInit()` via `x-init` — NOT `init()`, which Object.assign would clobber against player's](./adr/0114-annotate-cache-visibility-row.md) |
| 0115 | 2026-06-24 | [Annotation/studio batch runner gets real queue management (Tier 1 of the issue #100 review): cancel actually interrupts the in-flight task via `task.cancel()` (was DB-status only → the long Gemini call ran to completion), backed by a new idempotent `JobsRepo.cancel_job` that flips job + in-flight items to cancelled in one commit; both `_run_in_bg` wrappers reconcile on `CancelledError`; and `LiveCtx.aclose()` now `drain_running_jobs()` (cancel + bounded await) BEFORE closing the DB so fire-and-forget jobs — which uvicorn's connection draining does not cover — aren't abandoned mid-run](./adr/0115-job-cancel-and-shutdown-drain.md) — *Tier 2 unification tracked in #100* |
| 0116 | 2026-06-23 | [Calibration reuses the existing annotator run path with a `record_only` flag (telemetry-only: skips both `_finalize_studio`/`_finalize_annotation`, so no annotations/studio-runs/review-items) plus a `force_resolution` flag, instead of a new `calibration` run-kind (which would need a `run_telemetry.kind` CHECK migration + every kind-switch touched). Sweep = 6 `kind="studio"` jobs (3 resolutions × 2 repeats × 3 clips = 18 runs) tagged with a shared `run_group`, launched via the normal background-job machinery; defaults keep all existing callers unchanged](./adr/0116-calibration-record-only-run-path.md) — *implements §4 of the cost-prediction spec* |
| 0117 | 2026-06-23 | [CatDV text encoding is a missing-charset bug, not a repair problem: a charset-less `application/json` write is decoded by CatDV's servlet as ISO-8859-1, storing our UTF-8 as compounding mojibake. Declare `charset=utf-8` in `_call_json` (one place; fixes markers, notes AND fields) — confirmed against the live server (clean Czech marker written + read back raw byte-identical). Delete the ENTIRE `text_repair` module (read + write `demojibake`): with charset there is nothing to repair, and there is no production data yet to heal, so legacy-handling code is unwarranted (YAGNI)](./adr/0117-catdv-charset-utf8-write-correctly.md) — *resolves review findings #2/#5 at the HTTP layer* |
| 0118 | 2026-06-23 | [Centralize clip-version transitions in one `SyncEngine._advance_versions(rows, state)` chokepoint (replaces `_version_id_from_rows` + `_mark_version_failed` and five duplicated guard blocks, −32 lines), so no result/error branch can forget to advance a version (anomaly A9); and advance EVERY merged-publish sibling on conflict/failed (not just `max(version_id)`) — `mark_conflict`/`mark_failed` have no `mark_live`-style fan-out, so the older siblings of a merged PUT were stranded on `publishing` forever](./adr/0118-centralize-clip-version-transitions-advance-all-merged-siblings.md) — *cross-ref 0091, fixes anomalies A4/A9* |
| 0119 | 2026-06-23 | [Downgrade HIGH media_resolution→medium for non-image media on EVERY run (not just calibration): the general run path could 400 (`HIGH only for single images`) when a model's default resolved to high on a video; reuse `resolution_valid_for_kind` at the run-path chokepoint, resolve resolution before the in-run estimate (so est-vs-actual is resolution-consistent), and log the downgrade. Plus: surface missing pricing everywhere as "no rate card" instead of a misleading $0 (store NULL not 0.0; `pricing_missing` flag on the estimate; calibrate panel/projected-line/batches+studio estimate all branch on it). Plus UI parity: extract `_clip_picker_modal_body.html` so the calibrate picker is the shared Batches picker (was stacking vertically via a stray `.modal-body`), and Gemini-models page polish (kill number spinners, define `.pill.warn`, explain low/med/high)](./adr/0119-downgrade-high-resolution-for-non-image-and-surface-missing-pricing.md) — *extends 0116; reliability + polish pass* |
| 0120 | 2026-06-23 | [Auto-filter the calibration clip picker to the prompt's `media_kind` (image/video/any): since a clip's kind is path-derived (`is_image_path`) with no stored column, it can't be pushed into the provider's server-side pagination, so `query_clip_page` gains an opt-in `kind` param that, when set, fetches the full result set, filters in Python, then slices — keeping page AND total correct. Threaded `/batches/picker?kind=` → `clipPickerCore().kind` (null default, so Batches/Studio pickers send identical requests) → `openCalibrate(versionId, label, mediaKind)`. Plus: `stats_by_resolution` also sums `est_cost_usd_p50` so the results panel shows guessed→actual per resolution; and a one-line rough/fair/good confidence legend](./adr/0120-calibration-picker-media-kind-filter-and-guessed-vs-actual.md) — *reuses the shared picker (no fork); extends 0119* |
| 0121 | 2026-06-23 | [Seed rate cards for the full Gemini catalog (the 5 missing 3.x/3.5 models) at **Global standard** rates (not europe-west3 +10% — Global matches the existing 2.5 cards, is currently accurate pre-2026-07-01, and the 3.x family isn't pinned to europe-west3 single-region yet), verified against the official Vertex pricing page; add `test_rate_card_coverage.py` so any future catalog model lacking a `SEED_RATE_CARDS` entry fails CI; repoint "unpriced model" tests at synthetic ids. Adjacent: Prompts tab usage columns (annotated footage + est vs actual cost) via one batched `totals_by_prompt_version`](./adr/0121-seed-full-gemini-catalog-rate-cards-global.md) — *closes the "why only 3 cards" gap; extends 0119* |
| 0122 | 2026-06-24 | [Usage & budget (#30): a single monthly **soft** cap in `app_meta['budget_monthly_usd']` (no new table; clear = delete key) that colours the indicator + warns on launch surfaces but NEVER blocks a run (hard cap rejected — surprise-blocked work is worse than overspend); `UsageService` on CoreCtx (DB-only/offline-safe, injected clock) with `current_month` status none/ok/warn/over; spend = SUM(cost_usd) over occurred_at incl. calibration; partial-pricing "(N of M priced)" so a NULL-cost subtotal isn't read as complete; the spend overview (spend vs budget + by-day + budget editor) is merged INTO the "Gemini models" admin tab with per-model spend as table columns (no standalone "Usage" tab/route) + always-present topbar spend pill via `topbar_counts` + `/ui/usage-pill` poll (unchanged)](./adr/0122-usage-and-budget-soft-cap.md) — *delivers #30 on the cost-prediction foundation (0116–0124)* |
| 0123 | 2026-06-23 | [Merge the Gemini model catalog (`gemini_generation_model` enum) and per-model pricing (`model_config`) into ONE Admin "Gemini models" tab instead of the two-tab split PR1's spec specified: catalog is the spine, each row joins its rate card (absent → "no rate card" pill; Save upserts via new `ModelConfigRepo.set_rates` which creates/updates/revives), default/enable/delete + add-model inline (delete clears both stores), the generic enum tab retired (route still works, unlinked); both storage layers kept single-purpose — single-source consolidation deferred](./adr/0123-merge-gemini-model-catalog-and-pricing-tab.md) — *revises §1 of the cost-prediction spec (renumbered from 0114 — collided with main's 0114)* |
| 0124 | 2026-06-23 | [Resolution-aware estimates: key the estimator's LEARNED history on `(model, kind, resolution)` but keep the cold-start SEED constants resolution-blind (instead of the spec's "resolution-scaled seeds" — a fragile per-model-per-resolution token table that only affects the already-`rough` zero-history case). Accuracy comes from history, which self-corrects per resolution after ≥3 runs; effective resolution resolved server-side per estimate; resolution filter is an added WHERE clause (no extra query) so the N+1 guard holds (count 5→6 for one constant model_config read, N=10==N=100)](./adr/0124-resolution-keyed-history-blind-seeds.md) — *softens §3 of the cost-prediction spec (renumbered from 0115)* |
