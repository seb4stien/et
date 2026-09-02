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
if ! command -v gsettings >/dev/null 2>&1; then
    echo "error: gsettings not found on PATH; is GNOME Shell installed?" >&2
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

# A running GNOME Shell only scans ~/.local/share/gnome-shell/extensions at
# startup, so it doesn't know about a brand-new UUID yet: `gnome-extensions
# enable` talks to the *live* shell process and fails with "does not exist"
# for it, without touching anything, until the shell restarts. Fall back to
# flipping the same `enabled-extensions` gsettings key `enable` would set,
# so the extension starts enabled the next time GNOME Shell (re)loads.
if enable_err=$(gnome-extensions enable "${UUID}" 2>&1 >/dev/null); then
    echo "enabled ${UUID}"
    echo "note: if GNOME Shell was already running, log out/in (or Alt+F2, r on X11) for it to load."
else
    mapfile -t current < <(gsettings get org.gnome.shell enabled-extensions \
        | python3 -c "import ast, sys; print('\n'.join(ast.literal_eval(sys.stdin.read())))")
    if [[ ! " ${current[*]} " == *" ${UUID} "* ]]; then
        current+=("${UUID}")
    fi
    printf -v joined "'%s', " "${current[@]}"
    gsettings set org.gnome.shell enabled-extensions "[${joined%, }]"
    echo "registered ${UUID} as enabled (GNOME Shell hasn't scanned it in yet: ${enable_err})"
    echo "note: log out and back in (Wayland) — or Alt+F2, r on X11 — for GNOME Shell to load it."
fi

