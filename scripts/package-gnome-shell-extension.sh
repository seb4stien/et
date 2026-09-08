#!/usr/bin/env bash
# Build an extensions.gnome.org-ready ZIP for the bundled extension.

set -euo pipefail

UUID="et@seb4stien.github.com"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${REPO_ROOT}/gnome-extension/${UUID}"
DIST_DIR="${REPO_ROOT}/dist"
ARCHIVE="${DIST_DIR}/${UUID}.shell-extension.zip"
SCHEMA="schemas/org.gnome.shell.extensions.et.gschema.xml"

if ! command -v gnome-extensions >/dev/null 2>&1; then
    echo "error: gnome-extensions not found on PATH; is GNOME Shell installed?" >&2
    exit 1
fi

python3 "${REPO_ROOT}/scripts/check-gnome-shell-extension.py" "${SOURCE_DIR}"
mkdir -p "${DIST_DIR}"

(
    cd "${SOURCE_DIR}"
    gnome-extensions pack \
        --force \
        --out-dir="${DIST_DIR}" \
        --schema="${SCHEMA}" \
        --extra-source="LICENSE" \
        --extra-source="lib" \
        .
)

python3 "${REPO_ROOT}/scripts/check-gnome-shell-extension.py" \
    "${SOURCE_DIR}" \
    --archive "${ARCHIVE}"
echo "built ${ARCHIVE}"
