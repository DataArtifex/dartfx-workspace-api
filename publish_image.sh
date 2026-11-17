#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-dartfx/dartfx-workspace-api}"
VERSION_FILE="src/dartfx/workspace_api/__about__.py"
VERSION=$(python3 -c 'import re, sys; m = re.search(r"__version__\s*=\s*[\"'\'']([^\"'\'']+)[\"'\'']", open(sys.argv[1]).read()); print(m.group(1) if m else "latest")' "$VERSION_FILE")
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

echo "Building and publishing ${IMAGE_NAME}:${VERSION} and ${IMAGE_NAME}:latest for ${PLATFORMS}..."

docker buildx build \
  --platform "${PLATFORMS}" \
  -t "${IMAGE_NAME}:${VERSION}" \
  -t "${IMAGE_NAME}:latest" \
  --push \
  .
