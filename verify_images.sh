#!/usr/bin/env bash
set -euo pipefail

URL="${1:-http://127.0.0.1:${HOST_PORT:-8000}/status}"
echo "Verifying container health at ${URL}..."
curl -fsS "${URL}" | python3 -m json.tool
