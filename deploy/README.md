# Cloud Run deployment — one-time GCP setup

Everything here is run once by an operator with `gcloud` auth on
project `catdav`. Day-to-day deploys are the GitHub Actions workflow
(`.github/workflows/deploy.yml`). Spec:
`docs/specs/2026-06-09-cloud-run-deployment-design.md`.

```bash
export PROJECT_ID=catdav
export REGION=europe-west3
export REPO=catdv-annotator
export RUNTIME_SA=catdv-annotator@${PROJECT_ID}.iam.gserviceaccount.com
export DEPLOYER_SA=github-deployer@${PROJECT_ID}.iam.gserviceaccount.com

# 1. APIs
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com \
  storage.googleapis.com --project=$PROJECT_ID

# 2. Artifact Registry
gcloud artifacts repositories create $REPO --repository-format=docker \
  --location=$REGION --project=$PROJECT_ID

# 3. Runtime service account (reuse if scripts/setup-gcp.sh already made it)
gcloud iam service-accounts create catdv-annotator --project=$PROJECT_ID || true
# Media bucket (exists) + Litestream bucket (created in phase 2)
gsutil iam ch serviceAccount:${RUNTIME_SA}:roles/storage.objectAdmin gs://catdv-proxies
# Signed URLs via IAM signBlob (phase 4): the SA signs as itself
gcloud iam service-accounts add-iam-policy-binding $RUNTIME_SA \
  --member=serviceAccount:$RUNTIME_SA \
  --role=roles/iam.serviceAccountTokenCreator --project=$PROJECT_ID

# 4. Secrets (values prompted; never on the command line via args)
printf '%s' "$CATDV_PASSWORD_VALUE" | gcloud secrets create catdv-password \
  --data-file=- --replication-policy=automatic --project=$PROJECT_ID
printf '%s' "$GEMINI_API_KEY_VALUE" | gcloud secrets create gemini-api-key \
  --data-file=- --replication-policy=automatic --project=$PROJECT_ID
for s in catdv-password gemini-api-key; do
  gcloud secrets add-iam-policy-binding $s \
    --member=serviceAccount:$RUNTIME_SA \
    --role=roles/secretmanager.secretAccessor --project=$PROJECT_ID
done

# 5. Workload Identity Federation for GitHub Actions (no SA keys)
gcloud iam workload-identity-pools create github --location=global \
  --project=$PROJECT_ID
gcloud iam workload-identity-pools providers create-oidc github-oidc \
  --location=global --workload-identity-pool=github \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping=google.subject=assertion.sub,attribute.repository=assertion.repository \
  --attribute-condition="assertion.repository=='futurememorylab/ai-archive'" \
  --project=$PROJECT_ID
gcloud iam service-accounts create github-deployer --project=$PROJECT_ID
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER_SA \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github/attribute.repository/futurememorylab/ai-archive" \
  --role=roles/iam.workloadIdentityUser --project=$PROJECT_ID
for role in roles/run.admin roles/artifactregistry.writer roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member=serviceAccount:$DEPLOYER_SA --role=$role
done
# The workflow's verify step calls the private URL with an id token:
gcloud run services add-iam-policy-binding catdv-annotator --region=$REGION \
  --member=serviceAccount:$DEPLOYER_SA --role=roles/run.invoker \
  --project=$PROJECT_ID   # run AFTER the first deploy creates the service

# 6. Operator access to the private service
gcloud run services add-iam-policy-binding catdv-annotator --region=$REGION \
  --member=user:peter.hora@gmail.com --role=roles/run.invoker \
  --project=$PROJECT_ID   # run AFTER the first deploy

# 7. GitHub repo secrets (Settings -> Secrets -> Actions)
#    GCP_WIF_PROVIDER = projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github/providers/github-oidc
#    GCP_DEPLOYER_SA  = github-deployer@catdav.iam.gserviceaccount.com
```

## Accessing the deployed app

```bash
gcloud run services proxy catdv-annotator --region europe-west3
# then open http://localhost:8080
```

## Phase 2 (run when phase 2 lands)

```bash
gsutil mb -p $PROJECT_ID -l $REGION gs://catdv-annotator-db
gsutil iam ch serviceAccount:${RUNTIME_SA}:roles/storage.objectAdmin gs://catdv-annotator-db
```

## Phase 3 (run when phase 3 lands)

On a trusted machine: `wg genkey | tee /tmp/wg-cloud.key | wg pubkey`.
Add the public key as a peer on the office WireGuard server with
`AllowedIPs = <WG_SOURCE_IP>/32` for the new peer, and ensure the
cloud peer's allowed route covers only `192.168.1.41/32` (least
privilege — CatDV, not the whole LAN). Then:

```bash
gcloud secrets create wg-private-key --data-file=/tmp/wg-cloud.key \
  --replication-policy=automatic --project=$PROJECT_ID
gcloud secrets add-iam-policy-binding wg-private-key \
  --member=serviceAccount:$RUNTIME_SA \
  --role=roles/secretmanager.secretAccessor --project=$PROJECT_ID
shred -u /tmp/wg-cloud.key
```

Fill `WG_ENDPOINT` / `WG_PEER_PUBKEY` / `WG_SOURCE_IP` in
`deploy/cloudrun.env.yaml` from the office server's public host:port,
its public key, and the tunnel IP assigned to the new peer.
