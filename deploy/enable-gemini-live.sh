#!/usr/bin/env bash
# One-time GCP setup for the Gemini Live clip assistant.
# Idempotent — safe to re-run; existing API/key are reused.
#
# Prereqs:
#   - `gcloud` CLI installed and `gcloud auth login` already done
#   - `gcloud components install alpha` (api-keys lives under alpha)
#   - The signed-in account has roles/serviceusage.serviceUsageAdmin
#     and roles/serviceusage.apiKeysAdmin on $GCP_PROJECT_ID
#
# Usage:
#   GCP_PROJECT_ID=<your-project> ./deploy/enable-gemini-live.sh
#
# After it prints the key, paste into .env:
#   echo 'GEMINI_API_KEY=<printed-key>' >> .env
# then restart the app — schema migration 0010 and the Czech
# system-instruction seed run automatically on lifespan startup.

set -euo pipefail
PROJECT="${GCP_PROJECT_ID:?set GCP_PROJECT_ID, e.g. GCP_PROJECT_ID=pragafilm-catdv-annotator}"

# Preflight: ensure gcloud and the alpha component are available.
command -v gcloud >/dev/null 2>&1 || {
  echo "ERROR: gcloud not on PATH. Install Google Cloud SDK first." >&2
  exit 1
}
if ! gcloud alpha services api-keys --help >/dev/null 2>&1; then
  echo "ERROR: 'gcloud alpha' not installed. Run: gcloud components install alpha" >&2
  exit 1
fi
ACCOUNT="$(gcloud config get-value account 2>/dev/null || true)"
if [ -z "${ACCOUNT}" ]; then
  echo "ERROR: no gcloud auth account. Run: gcloud auth login" >&2
  exit 1
fi
echo "== Project: $PROJECT  account: $ACCOUNT =="

echo "→ Enabling Generative Language API on $PROJECT"
gcloud services enable generativelanguage.googleapis.com --project="$PROJECT"

# Re-use an existing key with our display name if one already exists;
# otherwise create a fresh one.
EXISTING_KEY="$(gcloud alpha services api-keys list \
  --filter='displayName=catdv-live-tokens' \
  --format='value(name)' --project="$PROJECT" 2>/dev/null | head -1 || true)"

if [ -z "${EXISTING_KEY}" ]; then
  echo "→ Creating API key 'catdv-live-tokens'"
  gcloud alpha services api-keys create \
    --display-name="catdv-live-tokens" \
    --api-target="service=generativelanguage.googleapis.com" \
    --project="$PROJECT"
  KEY_NAME="$(gcloud alpha services api-keys list \
    --filter='displayName=catdv-live-tokens' \
    --format='value(name)' --project="$PROJECT" | head -1)"
else
  echo "→ Re-using existing API key: $EXISTING_KEY"
  KEY_NAME="$EXISTING_KEY"
fi

echo
echo "→ GEMINI_API_KEY value (paste into .env):"
echo "─────────────────────────────────────────"
gcloud alpha services api-keys get-key-string "$KEY_NAME" --project="$PROJECT"
echo "─────────────────────────────────────────"
echo
echo "Next steps:"
echo "  1. echo 'GEMINI_API_KEY=<value-above>' >> .env"
echo "  2. ./run.sh   # restart — migration 0010 + seed run automatically"
echo "  3. Open a clip in the UI; the 🎤 Live button appears in the header."
