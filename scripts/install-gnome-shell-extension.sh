#!/usr/bin/env bash
# Installs (symlinks) the et GNOME Shell extension into the current user's
# extensions directory and enables it. Safe to re-run.
#
# The extension exposes a small D-Bus service that `et` uses on Wayland
# sessions to find the active workspace, since `wmctrl` only works on X11.

set -euo pipefail

UUID="et@seb4stien.github.com"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${REPO_ROOT}/gnome-extension/${UUID}"
EXTENSIONS_DIR="${HOME}/.local/share/gnome-shell/extensions"
TARGET_DIR="${EXTENSIONS_DIR}/${UUID}"

if ! command -v gnome-extensions >/dev/null 2>&1; then
    echo "error: gnome-extensions not found on PATH; is GNOME Shell installed?" >&2
    exit 1
fi

mkdir -p "${EXTENSIONS_DIR}"

if [ -L "${TARGET_DIR}" ] && [ "$(readlink -f "${TARGET_DIR}")" = "$(readlink -f "${SOURCE_DIR}")" ]; then
    echo "et extension already symlinked at ${TARGET_DIR}"
elif [ -e "${TARGET_DIR}" ]; then
    echo "error: ${TARGET_DIR} already exists and is not a symlink to ${SOURCE_DIR}" >&2
    exit 1
else
    ln -s "${SOURCE_DIR}" "${TARGET_DIR}"
    echo "symlinked ${TARGET_DIR} -> ${SOURCE_DIR}"
fi

gnome-extensions enable "${UUID}"
echo "enabled ${UUID}"
echo "note: if GNOME Shell was already running, log out/in (or Alt+F2, r on X11) for it to load."
