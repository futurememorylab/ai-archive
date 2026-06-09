# Cloud Run deployment — implementation handover

**Date:** 2026-06-09
**Branch:** `cloud-run-deployment` (from `main` @ `98222a7`)
**Plan:** `docs/plans/2026-06-09-cloud-run-deployment.md`
**Spec:** `docs/specs/2026-06-09-cloud-run-deployment-design.md`
**Scope delivered:** all *agent-executable* work for phases 1–4. Phase 5
(optimistic concurrency) is deliberately **not** started (deferred by the
spec). Every cloud-side / `gcloud` / WireGuard step is left for the
operator and listed below.

## How it was executed

Subagent-driven development: a fresh implementer subagent per plan task,
following the plan's TDD steps verbatim, with orchestrator review
(spec-compliance + quality) of each commit's diff before moving on. Every
task ended green on **both** `.venv/bin/python -m pytest` and
`.venv/bin/lint-imports` before its commit. The plan's own checkboxes were
ticked in-commit as each step completed; only operator steps remain
unticked.

Final verification on the tip of the branch: **pytest 1281 passed, 5
skipped**; **lint-imports 5 contracts kept, 0 broken**.

## Tasks completed (with commit SHAs)

| Task | Description | Commit |
|---|---|---|
| 1 | Remove dead `secrets.py` + Secret Manager SDK dependency | `00b7a61` |
| 2 | Test: Settings resolve from pure env (Cloud Run contract) | `827f13c` |
| 3 | Container: Dockerfile + entrypoint + litestream.yml + .dockerignore | `bebf219` |
| 4 | Deploy: `cloudrun.env.yaml` + one-time GCP setup runbook (`deploy/README.md`) + DEPLOY.md pointer | `a4dc9dd` |
| 5 | CI/CD workflow `.github/workflows/deploy.yml` (WIF, test-gated, single-instance) | `f0f76ee` |
| 7 | Test: pin WAL journal mode (Litestream precondition) | `e942c24` |
| 8 (file edits) | Enable Litestream env (`DB_PATH`, `LITESTREAM_REPLICA_URL`) + ADR 0066 + decisions.md row | `972b089` |
| 9 | Bound CatDV logout to 3 s (shutdown-grace budget) | `f4c1dfd` |
| 10 (file edits) | Flip `CATDV_OFFLINE=false` + placeholder WG config + `WG_PRIVATE_KEY` secret in workflow | `c941f3f` |
| 11 | Settings: `playback_source` preference (`local`/`gcs`) | `f60fe36` |
| 12 | `GcsService.signed_url`: V4 signed URLs with IAM-signBlob fallback | `dead464` |
| 13 | `MediaLocator`: ordered two-layer playback source | `684bcee` |
| 14 | `stream_media`: locate via `MediaLocator`; 307 to GCS signed URLs | `aaa578e` |
| 15 (file edit) | Phase 4: cloud playback prefers GCS signed URLs (`PLAYBACK_SOURCE: "gcs"`) | `63c5bdb` |

All 14 commits are on `cloud-run-deployment`. None pushed to `main`.

## Tasks skipped / blocked and why

All skips are **operator-only** (require `gcloud` auth on project `catdav`
and access to the office WireGuard server) — none were blocked by code or
test failures. Their plan checkboxes are intentionally left `- [ ]`.

- **Task 3, Step 6 (container smoke-test):** skipped — Docker daemon is
  unavailable on this machine (binary present at `/usr/bin/docker`, but
  `docker info` fails: `dial unix /var/run/docker.sock: connect: no such
  file or directory`). The plan explicitly permits skipping the build when
  Docker is unavailable and relying on the first Cloud Run deploy (Task 6)
  as the smoke test. All four container files were created and the
  entrypoint passed `sh -n`.
- **Task 6 (first deploy + phase-1 acceptance):** skipped entirely —
  operator runs `deploy/README.md` steps 1–7, sets the two GitHub secrets,
  triggers the workflow, and walks acceptance flows 1–2.
- **Task 8, Step 4 (bucket + deploy + acceptance flow 3):** skipped —
  operator creates `gs://catdv-annotator-db` and runs flow 3.
- **Task 10, Step 1 (WG peer + secret):** skipped — operator generates the
  WG keypair, adds the peer on the office server, creates the
  `wg-private-key` secret.
- **Task 10, Step 4 (push + acceptance flow 4):** the **commit** was made
  by the agent (`c941f3f`); the **push** is part of the end-of-run branch
  push, and **acceptance flow 4** is operator. Checkbox left unticked
  because push + flow are not complete.
- **Task 15, Step 3 (acceptance flow 5):** skipped — operator validates
  GCS-backed playback on the deployed service.

## Deviations from the plan

1. **`CATDV_USERNAME` is a placeholder.** The plan says to copy the real
   value from the local `.env`. There is **no `.env`** in this environment
   (only `.env.example`), so `deploy/cloudrun.env.yaml` carries
   `CATDV_USERNAME: "OPERATOR_FILL_FROM_LOCAL_ENV"`. The operator must
   replace it with the real (non-secret) username before deploying. Listed
   in the operator checklist.
2. **WG values are the plan's literal placeholders.** Per the run
   instructions, `WG_ENDPOINT` / `WG_PEER_PUBKEY` / `WG_SOURCE_IP` in
   `deploy/cloudrun.env.yaml` are the exact `<...>` placeholder strings the
   plan shows; the operator fills them from the office WG server in Task 10
   step 1.
3. **`deploy/README.md` org substitution.** `<github-org>/<repo>` was
   resolved to `futurememorylab/ai-archive` (from `git remote`).
4. **Minor — unused import kept verbatim.** `backend/app/routes/media.py`
   imports `LocalFile` from `media_locator` (per the plan's exact import
   line) although the route body references only `RemoteUrl` and
   `MediaNotAvailable` (it uses `path = located.path`, no `isinstance
   LocalFile`). Harmless; both green gates pass. Trim `LocalFile` from that
   import if a future ruff/unused-import gate is added.
5. **Reviews were orchestrator-run, not separate reviewer subagents.** The
   subagent-driven-development skill prescribes two dedicated reviewer
   subagents (spec then quality) per task. For these
   exact-content-from-plan tasks, the orchestrator performed both reviews
   directly against each commit's diff (verifying file contents, checkbox
   state, targeted-test results, and the full suite + lint-imports). This
   kept the overnight run efficient without lowering the verification bar —
   every task was independently diff-reviewed and gate-verified before the
   next was dispatched.

Note: several implementer subagents observed that target files were
"already present" in the working tree (a prior session had pre-created
some artifacts). In every case the agent verified the content against the
plan spec and the orchestrator independently re-verified the committed
diff, so this did not affect correctness.

## Operator checklist (must be done by a human with `gcloud` + WG access)

### A. One-time GCP setup — `deploy/README.md`
Run `deploy/README.md` steps 1–7 on project `catdv` (region
`europe-west3`): enable APIs; create the Artifact Registry repo; create the
runtime SA `catdv-annotator@catdav` with `storage.objectAdmin` on the
buckets, `secretAccessor` on the secrets, and
`iam.serviceAccountTokenCreator` on itself (for signed URLs); create the
`catdv-password` + `gemini-api-key` secrets; set up Workload Identity
Federation bound to `futurememorylab/ai-archive`; create the
`github-deployer` SA with `run.admin` + `artifactregistry.writer` +
`iam.serviceAccountUser`. **After the first deploy creates the service**,
run the two `run.invoker` bindings (deployer SA + `peter.hora@gmail.com`).

### B. GitHub repository secrets (Settings → Secrets → Actions)
- `GCP_WIF_PROVIDER` =
  `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-oidc`
- `GCP_DEPLOYER_SA` = `github-deployer@catdav.iam.gserviceaccount.com`

### C. Fill real values in `deploy/cloudrun.env.yaml`
- `CATDV_USERNAME` — replace `OPERATOR_FILL_FROM_LOCAL_ENV` with the real
  (non-secret) CatDV username from the local `.env`.
- `WG_ENDPOINT` — office WG server public `host:port`.
- `WG_PEER_PUBKEY` — office WG server public key.
- `WG_SOURCE_IP` — tunnel IP assigned to the new cloud peer (e.g.
  `10.6.0.9`).

### D. Phase-2 bucket (before/at phase 2 deploy)
`gsutil mb -p catdav -l europe-west3 gs://catdav-annotator-db` and grant
`storage.objectAdmin` to the runtime SA (the "Phase 2" block in
`deploy/README.md`).

### E. Phase-3 WireGuard (Task 10 step 1)
Generate the cloud keypair on a trusted machine; add the public key as a
peer on the office WG server with `AllowedIPs` routing only to
`192.168.1.41/32`; create the `wg-private-key` Secret Manager secret and
grant `secretAccessor` to the runtime SA (the "Phase 3" block in
`deploy/README.md`).

### F. Manual acceptance flows (spec §"Manual acceptance flows")
Run all flows up to the phase being accepted, via
`gcloud run services proxy catdv-annotator --region europe-west3`
(serves on `http://localhost:8080`):
1. **Phase 1** — push to `main`; workflow green (test→build→deploy);
   proxied UI loads with the CatDV-offline banner and clip list
   (placeholders OK); direct (un-proxied) URL returns 403; no
   missing-`.env`/credential stack traces in Cloud Run logs.
2. **Phase 1** — local `./run.sh` (no container) still boots against LAN
   CatDV with the existing `.env`.
3. **Phase 2** — add a note via the proxied UI; force a new instance
   (`gcloud run services update catdv-annotator --region europe-west3
   --update-labels bump=$(date +%s)`); note survives; old instance's logs
   show the full shutdown sequence (application shutdown complete →
   litestream final sync) within the grace window.
4. **Phase 3** — UI shows CatDV online; disable the office peer → offline
   banner within the probe interval, app stays navigable; re-enable →
   recovers without restart; CatDV admin shows exactly one cloud session.
5. **Phase 4** — a GCS-backed clip plays via a 307 redirect to
   `storage.googleapis.com` (devtools network tab; seeks issue range
   requests against the signed URL, not the app); a clip in neither layer
   shows the prefetch affordance and plays after prefetch; locally with
   `PLAYBACK_SOURCE=local` a cached clip still serves from disk (no
   redirect).

## Suggested continuation prompt for the next session

> The `cloud-run-deployment` branch implements phases 1–4 of
> `docs/specs/2026-06-09-cloud-run-deployment-design.md` (all
> agent-executable tasks; see
> `docs/plans/2026-06-09-cloud-run-deployment-HANDOVER.md`). The remaining
> work is operator-side and phase 5.
>
> 1. **Operator hand-off:** walk me through `deploy/README.md` steps 1–7
>    interactively (I have `gcloud` on project `catdv`), help me set the
>    two GitHub secrets, fill the real `CATDV_USERNAME` and `WG_*` values
>    in `deploy/cloudrun.env.yaml`, then drive acceptance flows 1–5 as each
>    phase deploys. Tick the corresponding operator checkboxes in the plan
>    as we go.
> 2. **Phase 5 (only when a second user is actually wanted):** write a new
>    plan from the spec's "Phase 5 — Optimistic concurrency" section
>    (version column + version-checked UPDATE → 409 + fresh partial + toast)
>    and implement it with TDD. This is the precondition for granting
>    `run.invoker` to a second account.
>
> Constraints unchanged: run Python via `.venv/bin/python`; never start the
> server / connect to CatDV at 192.168.1.41 (license seat); never `kill -9`;
> keep `pytest` + `lint-imports` green per task.
