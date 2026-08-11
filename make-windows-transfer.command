#!/bin/bash

set -euo pipefail

project_root="$(cd "$(dirname "$0")" && pwd)"
project_parent="$(dirname "$project_root")"
timestamp="$(date +%Y%m%d-%H%M%S)"
output_zip="${1:-${project_parent}/fund-flow-windows-transfer-${timestamp}.zip}"
staging_root="$(mktemp -d "${TMPDIR:-/tmp}/fund-flow-transfer.XXXXXX")"
staging_app="${staging_root}/fund-flow"

cleanup() {
  if [[ -n "${staging_root:-}" && -d "$staging_root" ]]; then
    /bin/rm -rf -- "$staging_root"
  fi
}
trap cleanup EXIT

mkdir -p "$staging_app"

/usr/bin/rsync -a \
  --exclude='.git/' \
  --exclude='node_modules/' \
  --exclude='.next/' \
  --exclude='.vinext/' \
  --exclude='.wrangler/' \
  --exclude='dist/' \
  --exclude='work/' \
  --exclude='outputs/' \
  --exclude='__pycache__/' \
  --exclude='.DS_Store' \
  --exclude='startup-windows.log' \
  --exclude='data/cache/*.json' \
  --exclude='data/*.sqlite3*' \
  --exclude='output/*.mp4' \
  --exclude='fund-flow-windows-transfer-*.zip' \
  "${project_root}/" "${staging_app}/"

/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$staging_app" "$output_zip"

echo
echo "Windows transfer ZIP created:"
echo "$output_zip"
echo
echo "Copy this ZIP to the SD card. On Windows, extract it fully before running start-windows.bat."

if [[ "${FUND_FLOW_PACKAGE_TEST:-0}" != "1" ]]; then
  /usr/bin/open -R "$output_zip"
fi
