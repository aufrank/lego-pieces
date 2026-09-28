#!/usr/bin/env bash
# Download instruction files from the Student Scissors "Instructions" collection.
# Auth comes from your own browser's Patreon login cookies (gallery-dl reads them;
# macOS may show a Keychain prompt). Re-runs skip anything already downloaded.
#
#   scripts/download.sh            # Chrome "Default" profile (the one logged into Patreon)
#   scripts/download.sh "chrome:Profile 1"   # any browser[:profile] gallery-dl accepts
set -euo pipefail
cd "$(dirname "$0")/.."
# Name the profile explicitly: without one, gallery-dl picks whichever profile
# touched its cookie DB most recently, which may be the wrong account.
BROWSER="${1:-chrome:Default}"
COLLECTION_URL="https://www.patreon.com/collection/34825"

uv run gallery-dl \
    --config gallery-dl.conf \
    --cookies-from-browser "$BROWSER" \
    --filter "extension not in ('jpg', 'jpeg', 'png', 'gif', 'webp')" \
    "$COLLECTION_URL"

echo
echo "Posts downloaded: $(find data/raw -name post.json | wc -l | tr -d ' ') / 133"
echo "Files by type:"
find data/raw -type f ! -name post.json | sed 's/.*\.//' | sort | uniq -c | sort -rn
