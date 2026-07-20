#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 GUILD_ID CHANNEL_ID" >&2
  exit 2
fi

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
LOCATION="${GOOGLE_CLOUD_LOCATION:-northamerica-northeast1}"
GUILD_ID="$1"
CHANNEL_ID="$2"

gcloud scheduler jobs pause viteoh-reconcile \
  --project="${PROJECT_ID}" --location="${LOCATION}"
gcloud tasks queues pause viteoh-deadlines \
  --project="${PROJECT_ID}" --location="${LOCATION}"

uv run python -m viteoh.cleanup_legacy \
  --guild-id="${GUILD_ID}" \
  --channel-id="${CHANNEL_ID}" \
  --confirm="DELETE-LEGACY-${GUILD_ID}"

echo "Cleanup succeeded. Deploy and configure guilds before resuming:"
echo "gcloud tasks queues resume viteoh-deadlines --project=${PROJECT_ID} --location=${LOCATION}"
echo "gcloud scheduler jobs resume viteoh-reconcile --project=${PROJECT_ID} --location=${LOCATION}"
