#!/usr/bin/env bash
#
# Deploy the CURRENT working tree to the staging Cloud Run service so cloud/IAP
# behaviour can be tested WITHOUT pushing to main and WITHOUT touching prod.
#
# Builds the image with Cloud Build (no local Docker needed) and deploys it to
# `catdv-annotator-staging` using deploy/staging.env.yaml. Run it as an OWNER of
# the project (NOT the runtime service account):
#     gcloud auth login            # if your active account isn't an owner
#     ./deploy/deploy-staging.sh
#
# First time only, also run the IAP one-time setup at the bottom of this file
# (or: ./deploy/deploy-staging.sh --init-iap).
#
# See deploy/README.md "Staging" for the isolation rules and cost notes.
set -euo pipefail

PROJECT_ID="catdav"
PROJECT_NUMBER="204842536530"
REGION="europe-west3"
SERVICE="catdv-annotator-staging"
IMAGE="europe-west3-docker.pkg.dev/${PROJECT_ID}/catdv-annotator/app"
RUNTIME_SA="catdv-annotator@${PROJECT_ID}.iam.gserviceaccount.com"
IAP_AGENT="service-${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com"

init_iap() {
  echo ">> One-time IAP setup for ${SERVICE}…"
  gcloud run services update "$SERVICE" --project "$PROJECT_ID" --region "$REGION" --iap
  gcloud run services add-iam-policy-binding "$SERVICE" \
    --project "$PROJECT_ID" --region "$REGION" \
    --member="serviceAccount:${IAP_AGENT}" --role=roles/run.invoker
  echo ">> Now grant yourself access (repeat --member for each tester):"
  echo "   gcloud iap web add-iam-policy-binding --resource-type=cloud-run \\"
  echo "     --service=${SERVICE} --region=${REGION} \\"
  echo "     --member=user:YOU@example.com --role=roles/iap.httpsResourceAccessor"
}

if [[ "${1:-}" == "--init-iap" ]]; then
  init_iap
  exit 0
fi

TAG="staging-$(date +%Y%m%d-%H%M%S)"
echo ">> Building ${IMAGE}:${TAG} from the current tree via Cloud Build…"
gcloud builds submit --project "$PROJECT_ID" --tag "${IMAGE}:${TAG}" .

echo ">> Deploying ${SERVICE} (scale-to-zero, CatDV connect-on-demand, ephemeral DB)…"
# VPN/CatDV is enabled on staging (ADR 0104): inject the WireGuard key + CatDV
# password (staging reuses prod's secrets). Must match the CI staging deploy in
# .github/workflows/deploy.yml so a local deploy doesn't strip the tunnel config.
gcloud run deploy "$SERVICE" \
  --project "$PROJECT_ID" --region "$REGION" \
  --image "${IMAGE}:${TAG}" \
  --service-account "$RUNTIME_SA" \
  --no-allow-unauthenticated \
  --min-instances=0 --max-instances=1 \
  --memory=1Gi --cpu=1 \
  --env-vars-file=deploy/staging.env.yaml \
  --set-secrets="CATDV_PASSWORD=catdv-password:latest,WG_PRIVATE_KEY=wg-private-key:latest"

URL=$(gcloud run services describe "$SERVICE" --project "$PROJECT_ID" \
  --region "$REGION" --format='value(status.url)')
echo ">> Deployed. URL: ${URL}"
echo ">> If this is the first deploy, run:  ./deploy/deploy-staging.sh --init-iap"
