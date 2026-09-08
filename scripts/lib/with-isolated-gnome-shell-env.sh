#!/usr/bin/env bash
# Runs a command with a disposable HOME/XDG environment containing a staged
# copy of the extension. Nothing under the real user's home directory is read
# or modified by GNOME Shell, dconf, or the extension.

set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "error: expected a command to run" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UUID="et@seb4stien.github.com"
SOURCE_DIR="${REPO_ROOT}/gnome-extension/${UUID}"

owns_test_root=0
if [ -n "${ET_TEST_ROOT:-}" ]; then
    test_root="${ET_TEST_ROOT}"
    mkdir -p "${test_root}"
else
    test_root="$(mktemp -d "${TMPDIR:-/tmp}/et-gnome-shell-test-XXXXXX")"
    owns_test_root=1
fi
cleanup() {
    if [ "${owns_test_root}" -eq 1 ]; then
        "${REPO_ROOT}/scripts/lib/remove-isolated-gnome-shell-env.sh" "${test_root}"
    fi
}
trap cleanup EXIT

export HOME="${test_root}/home"
export XDG_CONFIG_HOME="${test_root}/config"
export XDG_DATA_HOME="${test_root}/data"
export XDG_CACHE_HOME="${test_root}/cache"
export XDG_STATE_HOME="${test_root}/state"
export XDG_RUNTIME_DIR="${test_root}/runtime"
export ET_TEST_ROOT="${test_root}"
export ET_EXTENSION_UUID="${UUID}"
export ET_EXTENSION_DIR="${XDG_DATA_HOME}/gnome-shell/extensions/${UUID}"

mkdir -p \
    "${HOME}" \
    "${XDG_CONFIG_HOME}" \
    "${XDG_DATA_HOME}/gnome-shell/extensions" \
    "${XDG_CACHE_HOME}" \
    "${XDG_STATE_HOME}" \
    "${XDG_RUNTIME_DIR}"
chmod 700 "${XDG_RUNTIME_DIR}"

cp -a "${SOURCE_DIR}" "${ET_EXTENSION_DIR}"
glib-compile-schemas "${ET_EXTENSION_DIR}/schemas"

"$@"
