#!/usr/bin/env bash
# build.sh — Build the owner/staff admin dashboard frontend for VPS deployment.
#
# Run this from the monorepo root (or via `npm run build` from dashboard/):
#   bash dashboard/build.sh
#
# Requirements: Node.js 20+
# Output: dashboard/public/ (ready to be served by server.mjs)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADMIN_SRC="${SCRIPT_DIR}/admin-ui"
DIST_DST="${SCRIPT_DIR}/public"

echo "[BUILD] Building Owner/Staff Admin Dashboard frontend..."
echo "[BUILD] Source: ${ADMIN_SRC}"
echo "[BUILD] Output: ${DIST_DST}"

if [[ ! -f "${ADMIN_SRC}/index.html" || ! -f "${ADMIN_SRC}/app.js" || ! -f "${ADMIN_SRC}/styles.css" ]]; then
  echo "[BUILD] Missing admin dashboard source files in ${ADMIN_SRC}" >&2
  exit 1
fi

if command -v node >/dev/null 2>&1; then
  node --check "${ADMIN_SRC}/app.js"
fi

rm -rf "${DIST_DST}"
mkdir -p "${DIST_DST}"
cp "${ADMIN_SRC}/index.html" "${DIST_DST}/index.html"
cp "${ADMIN_SRC}/app.js" "${DIST_DST}/app.js"
cp "${ADMIN_SRC}/styles.css" "${DIST_DST}/styles.css"

echo "[BUILD] Done."
echo "[BUILD] To start the dashboard:"
echo "        cd dashboard && cp .env.example .env && nano .env"
echo "        npm install && npm start"
