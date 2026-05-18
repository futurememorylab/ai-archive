#!/usr/bin/env bash
# One-time GCP infrastructure setup for the CatDV Annotator project.
# Usage:
#   export PROJECT_ID=pragafilm-catdv-annotator
#   export REGION=europe-west3
#   export BUCKET_NAME=${PROJECT_ID}-proxies
#   ./scripts/setup-gcp.sh

set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REGION:=europe-west3}"
: "${BUCKET_NAME:=${PROJECT_ID}-proxies}"

SA_NAME="catdv-annotator"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "== Project: $PROJECT_ID  region: $REGION  bucket: $BUCKET_NAME =="

echo "Enabling APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com \
  --project="$PROJECT_ID"

echo "Creating bucket (idempotent)..."
if ! gsutil ls -b "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
  gsutil mb -p "$PROJECT_ID" -l "$REGION" "gs://${BUCKET_NAME}"
else
  echo "  bucket exists"
fi

echo "Creating service account (idempotent)..."
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="CatDV Annotator" \
    --project="$PROJECT_ID"
else
  echo "  service account exists"
fi

echo "Granting bucket objectAdmin..."
gsutil iam ch "serviceAccount:${SA_EMAIL}:objectAdmin" "gs://${BUCKET_NAME}"

echo "Granting Vertex AI user..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/aiplatform.user" \
  --quiet

echo "Creating secrets (idempotent)..."
for secret in CATDV_USERNAME CATDV_PASSWORD; do
  if ! gcloud secrets describe "$secret" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$secret" --replication-policy=automatic --project="$PROJECT_ID"
  fi
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID" --quiet
done

echo
echo "Done. Next steps:"
echo "  1. Populate secrets:"
echo "     echo -n 'klientAI' | gcloud secrets versions add CATDV_USERNAME --data-file=- --project=$PROJECT_ID"
echo "     echo -n '<password>' | gcloud secrets versions add CATDV_PASSWORD --data-file=- --project=$PROJECT_ID"
echo "  2. Generate a service-account key for local dev:"
echo "     gcloud iam service-accounts keys create ~/.gcp/${SA_NAME}-key.json \\"
echo "       --iam-account=${SA_EMAIL} --project=${PROJECT_ID}"
echo "  3. Point GOOGLE_APPLICATION_CREDENTIALS at that key in your .env."
