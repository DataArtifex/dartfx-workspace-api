#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <new-project-name> <new_package_name>"
  exit 1
fi

NEW_PROJECT="$1"
NEW_PACKAGE="$2"
OLD_PROJECT="dartfx-workspace-api"
OLD_PACKAGE="workspace_api"

echo "Renaming project '${OLD_PROJECT}' -> '${NEW_PROJECT}' and package '${OLD_PACKAGE}' -> '${NEW_PACKAGE}'..."

find . -maxdepth 4 \
  -not -path './.git*' \
  -not -path './.venv*' \
  -not -path './dist*' \
  -not -path './build*' \
  -not -path './docs/build*' \
  -type f -print0 | while IFS= read -r -d '' file; do
  if [ "$file" = "./rename.sh" ]; then
    continue
  fi
  python3 -c '
import pathlib, sys
p = pathlib.Path(sys.argv[1])
try:
    txt = p.read_text(encoding="utf-8")
except UnicodeDecodeError:
    sys.exit(0)
new_txt = txt.replace(sys.argv[2], sys.argv[3]).replace(sys.argv[4], sys.argv[5])
if new_txt != txt:
    p.write_text(new_txt, encoding="utf-8")
' "$file" "$OLD_PROJECT" "$NEW_PROJECT" "$OLD_PACKAGE" "$NEW_PACKAGE"
done

if [ -d "src/dartfx/${OLD_PACKAGE}" ] && [ "${OLD_PACKAGE}" != "${NEW_PACKAGE}" ]; then
  mv "src/dartfx/${OLD_PACKAGE}" "src/dartfx/${NEW_PACKAGE}"
fi

echo "Done. Run 'uv sync' to refresh the environment."
