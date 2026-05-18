#!/usr/bin/env bash
# build.sh — Build the React frontend for standalone VPS deployment.
#
# Run this from the monorepo root (or via `npm run build` from dashboard/):
#   bash dashboard/build.sh
#
# Requirements: Node.js 20+, pnpm (installed in the monorepo)
# Output: dashboard/public/ (ready to be served by server.mjs)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_SRC="${MONO_ROOT}/artifacts/dj-status/dist/public"
DIST_DST="${SCRIPT_DIR}/public"

echo "[BUILD] Building DJ Dashboard frontend..."
echo "[BUILD] Monorepo root: ${MONO_ROOT}"

cd "${MONO_ROOT}"

# Build the React frontend with base path = / (VPS root, not /dj-status/)
PORT=3000 BASE_PATH=/ NODE_ENV=production \
  pnpm --filter @workspace/dj-status run build

echo "[BUILD] Copying built files to dashboard/public/ ..."
rm -rf "${DIST_DST}"
cp -r "${DIST_SRC}" "${DIST_DST}"

echo "[BUILD] Done."
echo "[BUILD] To start the dashboard:"
echo "        cd dashboard && cp .env.example .env && nano .env"
echo "        npm install && npm start"
