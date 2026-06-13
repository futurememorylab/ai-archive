---
name: deploy-staging
description: Deploy the current branch/working tree to the catdv-annotator-staging Cloud Run service to test cloud + IAP behaviour WITHOUT pushing to main or touching production. Use whenever the user wants to try a change "in the cloud", test IAP / the deployed app, or validate a branch before merging. Encodes the isolation rules that keep staging from damaging prod.
---

# Deploy to Staging

`catdv-annotator-staging` is a persistent, scale-to-zero Cloud Run service for
testing cloud + IAP behaviour from any branch, without pushing to `main` and
without touching the production `catdv-annotator` service. The deployed image is
built from the **current working tree** via Cloud Build (no local Docker).

## How to deploy

The mechanics live in `deploy/deploy-staging.sh` (build → deploy) using
`deploy/staging.env.yaml`. The operator runs it from a terminal authenticated
as a **project Owner** — NOT the runtime service account, which lacks
build/deploy/IAP permissions:

```bash
gcloud auth login            # if the active account isn't an owner
./deploy/deploy-staging.sh   # build current tree + deploy to staging
./deploy/deploy-staging.sh --init-iap   # first time only: enable IAP + grant invoker
```

You (the agent) generally **cannot** run these from your shell — it is usually
authenticated as the runtime SA. Hand the commands to the user via `!` or ask
them to run them.

## Isolation rules — do NOT break these (they protect production)

Staging shares a GCP project with prod, so it must stay clear of prod's
**shared singletons** (ADR 0066/0077). These are baked into
`deploy/staging.env.yaml`; never override them on staging:

- **`CATDV_OFFLINE=true`** — CatDV has ~1 free *global* license seat. Staging
  must never connect, or it competes with prod and the human web client. So
  staging cannot test live CatDV reads/writes — do those locally.
- **No `LITESTREAM_REPLICA_URL`** — staging uses an ephemeral DB. It must NEVER
  point at prod's `gs://catdv-annotator-db` path: two Litestream writers on one
  path corrupts the database.
- Don't run annotation jobs on staging that would write blobs into prod's
  `gs://catdv-proxies` media bucket.

## Cost

Scales to zero (`--min-instances=0`) → ~no cost when idle. Per-deploy cost is a
Cloud Build run (same as a `main` deploy) + a few cents of image storage.

## Discovering the IAP audience

The exact JWT audience for direct Cloud Run IAP is not authoritatively
documented, so it is **discovered from a live token on staging**, never
guessed. With `AUTH_BACKEND=iap` and `IAP_AUDIENCE` unset, sign in and read the
token's `aud` claim (a one-time signature-only decode logged by the iap
adapter, or via the Cloud Run logs), then set `IAP_AUDIENCE` in
`deploy/staging.env.yaml` and redeploy. See ADR 0078.

## Teardown

`gcloud run services delete catdv-annotator-staging --region europe-west3` if
the environment is no longer wanted (it costs ~nothing idle, so keeping it is
fine).
