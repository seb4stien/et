#!/usr/bin/env bash
# Real, end-to-end smoke test of the et GNOME Shell extension's D-Bus
# surface against an actual (headless, isolated) GNOME Shell process --
# not the mocked `subprocess.run` used by the pytest suite.
#
# This exists because the pytest suite (unit + FakeSystem-based end-to-end
# tests) fully mocks `gsettings`/`gdbus`, so it can never catch bugs that
# only show up when talking to a *real* GNOME Shell process: e.g. the
# extension failing to actually reach D-Bus-callable state, taking longer
# than expected to activate, or the isolation technique used by
# scripts/test-gnome-shell-extension.sh leaking into (or being leaked into
# by) the real session's dconf. Two such bugs were only found by manual
# testing before this script existed.
#
# Uses `gnome-shell --headless` (no parent display needed, unlike `--nested`)
# with a private HOME and complete XDG directory set, including a staged copy
# of the extension. It never installs into or changes the real user session.
#
# By default it skips when GNOME is unavailable. Pass --strict in CI to make
# missing tools or an unexpected Shell major version fail the check.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="et@seb4stien.github.com"
ACTIVATION_TIMEOUT_SECONDS=90
STRICT=0
EXPECTED_SHELL_MAJOR=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        --strict)
            STRICT=1
            ;;
        --expected-shell-major)
            shift
            EXPECTED_SHELL_MAJOR="${1:?--expected-shell-major requires a value}"
            ;;
        *)
            echo "error: unknown argument '$1'" >&2
            exit 2
            ;;
    esac
    shift
done

if ! command -v gnome-shell >/dev/null 2>&1 || ! command -v dbus-run-session >/dev/null 2>&1; then
    if [ "${STRICT}" -eq 1 ]; then
        echo "error: gnome-shell and dbus-run-session are required in strict mode." >&2
        exit 1
    fi
    echo "skip: gnome-shell and/or dbus-run-session not found on PATH."
    exit 0
fi
if ! command -v gdbus >/dev/null 2>&1; then
    if [ "${STRICT}" -eq 1 ]; then
        echo "error: gdbus is required in strict mode." >&2
        exit 1
    fi
    echo "skip: gdbus not found on PATH."
    exit 0
fi

shell_version="$(gnome-shell --version)"
shell_major="$(sed -n 's/^GNOME Shell \([0-9][0-9]*\).*/\1/p' <<<"${shell_version}")"
if [ -n "${EXPECTED_SHELL_MAJOR}" ] && [ "${shell_major}" != "${EXPECTED_SHELL_MAJOR}" ]; then
    echo "error: expected GNOME Shell ${EXPECTED_SHELL_MAJOR}, found ${shell_version}" >&2
    exit 1
fi

echo "Starting an isolated ${shell_version} headless smoke test for ${UUID}..."

test_root="$(mktemp -d "${TMPDIR:-/tmp}/et-gnome-shell-test-XXXXXX")"
# shellcheck disable=SC2317 # Invoked by the EXIT trap.
cleanup() {
    "${REPO_ROOT}/scripts/lib/remove-isolated-gnome-shell-env.sh" "${test_root}"
}
trap cleanup EXIT

run_command=(
    env
    ET_TEST_ROOT="${test_root}"
    "${REPO_ROOT}/scripts/lib/with-isolated-gnome-shell-env.sh"
    dbus-run-session --
    env
    ET_ACTIVATION_TIMEOUT_SECONDS="${ACTIVATION_TIMEOUT_SECONDS}"
    "${REPO_ROOT}/scripts/lib/check-extension-live.sh"
)

# See scripts/test-gnome-shell-extension.sh for why this is wrapped in a
# systemd-run --scope: the headless Shell D-Bus-activates a pile of helper
# daemons (gvfs, tracker, evolution-*, goa-daemon, xdg-desktop-portal*,
# ibus...) that daemonize/reparent away from this process tree and are
# never cleaned up if only the main gnome-shell PID is killed. Wrapping
# the whole run in its own cgroup guarantees they're all reaped together
# when it exits, instead of permanently leaking on every run (which was
# observed to noticeably slow down repeated runs on the same machine).
set +e
if command -v systemd-run >/dev/null 2>&1 && command -v systemctl >/dev/null 2>&1 \
        && systemctl --user status >/dev/null 2>&1; then
    scope_name="et-live-check-$$.scope"
    systemd-run --user --scope --collect \
        --unit="et-live-check-$$" -- \
        "${run_command[@]}"
    status=$?
    systemctl --user stop "${scope_name}" >/dev/null 2>&1 || true
elif [ "${CI:-}" = "true" ]; then
    "${run_command[@]}"
    status=$?
else
    echo "error: a working user systemd session is required outside disposable CI" \
         "so GNOME helper processes cannot leak into the host session." >&2
    status=1
fi
set -e

exit "${status}"
