# ADR 0132 — Cap Cloud Run at one instance service-wide, not only per revision

Date: 2026-11-12 · Status: accepted · Lifespan: Invariant (21) ·
Recorded after the fact for PR #132 (merged 2026-10-15) · Refines 0066, 0077

## Context

ADR 0066 pinned Cloud Run to one instance with `--max-instances=1`. That
flag limits each **revision**, not the service. During a deploy, or when an
old revision still holds traffic or is draining, a second instance can run
beside the first. On 2026-10-15 overlapping instances shared one WireGuard
peer identity and broke the CatDV tunnel (two peers with one key fight over
the endpoint), and the failed logouts that followed filled both CatDV seats
(see also ADR 0133).

PR #132 fixed it in `.github/workflows/deploy.yml` (prod and staging) and
`deploy/deploy-staging.sh`, inside a PR about cancelling cache downloads.
It changed invariant 21 without an ADR; this records it (SDLC audit,
2026-11-12).

## Decision

Every deploy passes the service-level scaling flags as well as the
revision-level ones:

```
--min-instances=0 --max-instances=1 --min=0 --max=1 --no-cpu-throttling
```

`--max=1` caps the **service** across all revisions, so no two instances
ever run at once. `--max-instances=1` stays as the per-revision cap. Both
are correctness, not cost: one CatDV seat, one Litestream writer, one
in-process write queue, one WireGuard peer identity. Never raise either;
never traffic-split.

## Consequences

- A deploy may wait for the old instance to go before the new one serves.
  Requests in that window queue or fail fast; we accept a short gap over a
  broken tunnel.
- A manual `gcloud run deploy` (or a new deploy script) must carry both
  flags. The deploy workflow and `deploy/deploy-staging.sh` are the two
  places today.
- Even with the cap, a replaced instance can lose the tunnel before it logs
  out of CatDV; ADR 0133 covers that seat.

## Alternatives considered

1. **Per-revision cap only (0066 as it was).** Proven insufficient on
   2026-10-15.
2. **A distinct WireGuard key per revision.** The CatDV side allows one
   peer for us; it would also double the seat demand. Rejected.
