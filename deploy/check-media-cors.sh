#!/usr/bin/env bash
#
# Check that the media bucket's live CORS policy lets each Cloud Run service
# read its videos. The player loads signed media URLs with
# crossorigin="anonymous", so a service URL missing from the bucket's CORS
# means videos don't play there and the clip chat can't read frames. Run it
# after changing deploy/gcs-cors.json or adding an app URL:
#     ./deploy/check-media-cors.sh catdv-annotator-staging catdv-annotator
set -euo pipefail

REGION="${REGION:-europe-west3}"
BUCKET="gs://$(sed -nE 's/^GCS_BUCKET_NAME:[[:space:]]*"?([^"]+)"?/\1/p' \
  "$(dirname "$0")/cloudrun.env.yaml")"

if ! cors="$(gcloud storage buckets describe "$BUCKET" --format='json(cors_config)' 2>&1)"; then
  echo "::warning::Couldn't read the CORS policy of $BUCKET, so it wasn't checked: $cors"
  exit 0
fi

status=0
for service in "$@"; do
  # A service answers on two URLs (project-number and hash forms); check both.
  urls="$(gcloud run services describe "$service" --region="$REGION" \
    --format='value(status.url,metadata.annotations."run.googleapis.com/urls")' \
    | tr ',;[]" \t' '\n' | grep '^https://' | sort -u)"
  for url in $urls; do
    if ! grep -qF "\"$url\"" <<<"$cors"; then
      echo "::error::$BUCKET has no CORS entry for $url, so videos won't play there. Add it to deploy/gcs-cors.json and run: gcloud storage buckets update $BUCKET --cors-file=deploy/gcs-cors.json"
      status=1
    fi
  done
done
[ "$status" = 0 ] && echo "CORS on $BUCKET covers: $*"
exit "$status"
