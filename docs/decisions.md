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
| 0042 | 2026-05-30 | [Bulk "Annotate selected" — one job per media kind, ephemeral progress indicator](./adr/0042-bulk-annotate-selected.md) |
