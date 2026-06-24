# 0103. Promote-by-tag: main deploys to staging, v* tags promote to production

**Date:** 2026-06-19
**Status:** Accepted
**Lifespan:** Invariant

## Context

`.github/workflows/deploy.yml` deployed straight to **production**
(`catdv-annotator`) on every push to `main`, gated only on the test job. There
was no automatic staging step: `catdv-annotator-staging` was only ever updated
by hand via `deploy/deploy-staging.sh` (and the `deploy-staging` skill). So the
sole soak environment for a cloud/IAP change was whatever a developer remembered
to deploy locally, and prod moved on every merge with no online pre-flight on a
real Cloud Run + IAP surface.

We wanted continuous staging (every merge visible in the cloud within minutes)
without making prod releases automatic — prod should move on a deliberate human
gesture, and should run bytes that already soaked on staging.

## Alternatives

- **Keep main → prod (status quo).** Zero friction, but no staging soak and
  prod churns on every merge. Rejected: the whole point was a pre-prod gate.
- **main → staging *and* prod (staging first, then prod gated on staging
  health).** Continuous prod with a soak step, but still no human in the loop
  and health-gating a `--no-allow-unauthenticated --iap` service from CI is
  awkward (the external health probe needs an IAP-authorized token; we already
  rely on Cloud Run's internal readiness gate for exactly this reason).
- **A long-lived `staging` branch that deploys to staging.** Extra branch to
  keep in sync with `main`; promotion becomes a merge rather than a tag. More
  moving parts than a tag.
- **Promote-by-tag (chosen).** `main` → staging continuously; a `v*` tag
  promotes a specific, already-built, staging-tested commit to prod.

## Decision

Split the single deploy job by `github.ref`:

- **`refs/heads/main`** (push or `workflow_dispatch` from main) → `deploy-staging`.
  This is the **only** build in the pipeline: it builds + pushes `$IMAGE:$SHA`
  (and `:latest`), then `gcloud run deploy`s that image to
  `catdv-annotator-staging` with the staging config (`deploy/staging.env.yaml`,
  **no** `--set-secrets` — CatDV is offline on staging — and **no**
  `--no-cpu-throttling` — staging has no Litestream replica to flush). `--iap`
  is passed every deploy (idempotent) to keep IAP enforced.

- **`refs/tags/v*`** → `deploy-prod`. It does **not** rebuild: it deploys the
  exact `$IMAGE:$SHA` that the commit's main build already produced, to
  `catdv-annotator` with the unchanged prod config (`cloudrun.env.yaml`,
  `--set-secrets`, `--no-cpu-throttling`, ADR 0077 flags). A guard step first
  asserts `$IMAGE:$SHA` exists in Artifact Registry and fails fast with an
  actionable message if you tagged a commit that never came through main.

Gating on `github.ref` (rather than `github.event_name`) means a
`workflow_dispatch` from `main` re-runs the staging path, and dispatching a
selected `v*` tag promotes — a manual escape hatch that falls out for free.

Releasing is: `git tag v1.4.0 <commit-on-main> && git push origin v1.4.0`.

## Consequences

- Every merge to `main` is live on staging within minutes; prod only moves on a
  `v*` tag. Tags are immutable history of exactly which SHA went to prod, and
  rollback is re-tagging a known-good SHA.
- Prod runs byte-identical bytes to what staging soaked (build once, promote the
  SHA image — no second build that could drift).
- New contract: **only tag commits that came through main.** Tagging an
  un-built commit fails the guard step instead of deploying. The image-SHA tag
  is the join between the two jobs.
- The staging service must already have its IAP IAM bindings from a prior
  `deploy/deploy-staging.sh --init-iap` (IAP agent `run.invoker` + tester
  `iap.httpsResourceAccessor`). The CI `--iap` flag enables IAP on the service
  but does not create those bindings.
- `deploy/deploy-staging.sh` still exists for deploying an *uncommitted working
  tree* to staging (CI only deploys what's merged to main); the two paths target
  the same service with the same config and don't conflict.
