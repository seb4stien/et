#!/usr/bin/env bash
# Installs the et GNOME Shell extension into the current user's extensions
# directory and enables it. Safe to re-run.
#
# The extension exposes GNOME Shell state and owns the per-workspace counters
# used by the CLI. It also displays configurable workspace metadata and elapsed
# time in the panel and workspace switcher.
#
# By default this *copies* the extension files so the installed copy is a
# real, persistent directory: this repo's checkout can be re-provisioned
# (e.g. on session/workspace restart) on a timeline independent of the
# GNOME session, and GNOME Shell only scans the extensions directory once
# at startup. A symlink whose target doesn't exist yet at that exact moment
# is silently skipped by GNOME Shell and never picked up until a full
# restart happens *after* the target exists. A copy avoids that race on
# every boot after the first successful install.
#
# Pass --dev (or --symlink) to symlink instead, e.g. for iterating on
# extension.js without re-running this script for every change (a Shell
# restart is still required either way to pick up new code).

set -euo pipefail

UUID="et@seb4stien.github.com"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${REPO_ROOT}/gnome-extension/${UUID}"
EXTENSIONS_DIR="${HOME}/.local/share/gnome-shell/extensions"
TARGET_DIR="${EXTENSIONS_DIR}/${UUID}"

MODE="copy"
for arg in "$@"; do
    case "${arg}" in
        --dev|--symlink)
            MODE="symlink"
            ;;
        *)
            echo "error: unknown argument '${arg}' (expected --dev/--symlink)" >&2
            exit 1
            ;;
    esac
done

if ! command -v gnome-extensions >/dev/null 2>&1; then
    echo "error: gnome-extensions not found on PATH; is GNOME Shell installed?" >&2
    exit 1
fi
if ! command -v gsettings >/dev/null 2>&1; then
    echo "error: gsettings not found on PATH; is GNOME Shell installed?" >&2
    exit 1
fi
if ! command -v glib-compile-schemas >/dev/null 2>&1; then
    echo "error: glib-compile-schemas not found on PATH; install GLib development tools" >&2
    exit 1
fi

mkdir -p "${EXTENSIONS_DIR}"

if [ "${MODE}" = "symlink" ]; then
    if [ -L "${TARGET_DIR}" ] && [ "$(readlink -f "${TARGET_DIR}")" = "$(readlink -f "${SOURCE_DIR}")" ]; then
        echo "et extension already symlinked at ${TARGET_DIR}"
    elif [ -e "${TARGET_DIR}" ]; then
        rm -rf "${TARGET_DIR}"
        ln -s "${SOURCE_DIR}" "${TARGET_DIR}"
        echo "replaced ${TARGET_DIR} with a symlink -> ${SOURCE_DIR}"
    else
        ln -s "${SOURCE_DIR}" "${TARGET_DIR}"
        echo "symlinked ${TARGET_DIR} -> ${SOURCE_DIR}"
    fi
else
    if [ -L "${TARGET_DIR}" ]; then
        # Replace a symlink from a previous install (or --dev run) with a
        # real copy, so it survives independently of SOURCE_DIR's lifecycle.
        rm -f "${TARGET_DIR}"
    fi

    if [ -d "${TARGET_DIR}" ] \
        && diff -rq --exclude=gschemas.compiled "${SOURCE_DIR}" "${TARGET_DIR}" >/dev/null 2>&1; then
        echo "et extension already installed and up to date at ${TARGET_DIR}"
    else
        if [ -e "${TARGET_DIR}" ]; then
            echo "updating installed copy at ${TARGET_DIR}"
        else
            echo "installing copy at ${TARGET_DIR}"
        fi
        rm -rf "${TARGET_DIR}"
        cp -r "${SOURCE_DIR}" "${TARGET_DIR}"
        echo "note: extension content changed — log out and back in for GNOME Shell to load the update."
    fi
fi

# Copy/symlink installs bypass `gnome-extensions install`, so compile the
# bundled schema explicitly. For --dev the target is a symlink and the compiled
# file lands in the source tree; it is ignored by git.
glib-compile-schemas "${TARGET_DIR}/schemas"

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
    # `gsettings get` on an empty array prints the GVariant-annotated form
    # `@as []` (needed since an empty array's element type can't otherwise
    # be inferred from its contents) instead of plain `[]`; strip that
    # leading `@<type> ` annotation before parsing so an empty
    # enabled-extensions list doesn't crash this. Also print nothing (not
    # an empty line) when the list is empty, so `mapfile` doesn't capture a
    # stray blank element.
    mapfile -t current < <(gsettings get org.gnome.shell enabled-extensions \
        | python3 -c "import ast, re, sys
items = ast.literal_eval(re.sub(r'^@\S+\s+', '', sys.stdin.read()))
print('\n'.join(items)) if items else None")
    if [[ ! " ${current[*]} " == *" ${UUID} "* ]]; then
        current+=("${UUID}")
    fi
    printf -v joined "'%s', " "${current[@]}"
    gsettings set org.gnome.shell enabled-extensions "[${joined%, }]"
    echo "registered ${UUID} as enabled (GNOME Shell hasn't scanned it in yet: ${enable_err})"

    # Query the *running* Shell over D-Bus to tell apart "Shell has never
    # heard of this UUID" (needs a full restart to scan it in — commonly a
    # boot-time race between this workspace becoming available and GNOME
    # Shell's one-time startup scan) from "Shell knows about it but it's in
    # an error/out-of-date state" (a real bug worth fixing, not just a
    # restart away). Best-effort: skip silently if the D-Bus call fails.
    info="$(gdbus call --session --dest org.gnome.Shell.Extensions \
        --object-path /org/gnome/Shell/Extensions \
        --method org.gnome.Shell.Extensions.GetExtensionInfo "${UUID}" 2>/dev/null || true)"
    if [[ "${info}" == "({},)" || "${info}" == "(@a{sv} {},)" ]]; then
        echo "note: GNOME Shell has no record of ${UUID} at all — log out and" \
             "back in (Wayland) — or Alt+F2, r on X11 — for it to scan the" \
             "extensions directory again."
    elif [[ -n "${info}" ]] && err=$(grep -oP "'error': <'\K[^']*" <<< "${info}") && [[ -n "${err}" ]]; then
        echo "warning: GNOME Shell already scanned ${UUID} but reports an error:"
        echo "  ${err}"
    else
        echo "note: log out and back in (Wayland) — or Alt+F2, r on X11 — for GNOME Shell to load it."
    fi
fi
