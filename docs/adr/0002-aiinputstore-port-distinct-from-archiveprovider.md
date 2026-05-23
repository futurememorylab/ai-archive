# 0002. AIInputStore port distinct from ArchiveProvider

- **Date:** 2026-05-19
- **Status:** Accepted

## Context

Vertex AI Gemini needs media bytes available at a URI it can
read (today: GCS). The same clip's bytes can live on a CatDV server
(archive), on the annotator host's disk (proxy cache), and in a GCS bucket
(AI input). Conflating "where the archive is" and "where Gemini reads from"
would force a CatDV install and a filesystem-archive install to share the
same upload code, and would make adding the Gemini Files API a rewrite of
the annotator rather than a new adapter.

## Alternatives

Merge AI upload into ArchiveProvider; rename
`GcsService` to a more abstract `MediaCdn` without a Protocol.

## Decision

Introduce `AIInputStore` Protocol parallel to `ArchiveProvider`,
with adapter packages under `backend/app/archive/ai_stores/`. The GCS
adapter ships today; a Gemini Files API stub proves the Protocol shape.

## Consequences

Two ports with one responsibility each beats one port with two
responsibilities. Switching the AI store is one adapter swap; switching
the archive is another; neither cascades into the worker.
