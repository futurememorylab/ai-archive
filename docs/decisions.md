# Architecture Decisions

As of 2026-05-23, individual decisions are now recorded as MADR-style
files under [`docs/adr/`](./adr/). One decision per file.

To add a new decision, create `docs/adr/NNNN-slug.md` with the next
available number. See any existing ADR for the template.

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
