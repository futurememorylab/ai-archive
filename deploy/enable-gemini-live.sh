#!/usr/bin/env bash
set -euo pipefail
PROJECT="${GCP_PROJECT_ID:?set GCP_PROJECT_ID}"

echo "→ Enabling Generative Language API on $PROJECT"
gcloud services enable generativelanguage.googleapis.com --project="$PROJECT"

echo "→ Creating API key 'catdv-live-tokens'"
gcloud alpha services api-keys create \
  --display-name="catdv-live-tokens" \
  --api-target="service=generativelanguage.googleapis.com" \
  --project="$PROJECT"

echo "→ Printing key value (paste into .env as GEMINI_API_KEY):"
KEY_NAME="$(gcloud alpha services api-keys list \
  --filter='displayName=catdv-live-tokens' \
  --format='value(name)' --project="$PROJECT" | head -1)"
gcloud alpha services api-keys get-key-string "$KEY_NAME" --project="$PROJECT"
