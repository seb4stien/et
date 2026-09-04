#!/usr/bin/env bash
# Launches a throwaway, nested GNOME Shell session with the et extension
# enabled, for visually iterating on it without touching the real session.
#
# Stages the extension under a disposable HOME/XDG tree, then starts
# `gnome-shell --nested --wayland` inside a private D-Bus session. The nested
# Shell never reads or writes the real extension directory, dconf database,
# cache, state, or enabled-extension list.
#
# Unlike `GSETTINGS_BACKEND=memory` (an earlier attempt at this isolation),
# a private `XDG_CONFIG_HOME` gives the nested session a real, file-backed
# dconf database: settings persist across separate command invocations
# within the nested shell (e.g. `gsettings set ... dynamic-workspaces
# false` followed by a later `et` run), which a per-process, in-memory-only
# backend cannot do. `enabled-extensions` is pre-seeded into that private
# database *before* the nested Shell starts (see scripts/lib/
# run-nested-test-shell.sh), so it comes up with the extension already
# enabled -- no post-startup D-Bus retry dance needed.
#
# The nested Shell's startup D-Bus-activates a pile of helper daemons
# (gvfs, tracker, evolution-*, goa-daemon, xdg-desktop-portal*, ibus...).
# These daemonize/double-fork and end up reparented away from this
# script's own process tree, so merely killing the nested `gnome-shell`
# PID leaves all of them running forever -- they don't share its lifetime
# and never get cleaned up on their own. Left unchecked, every run of this
# script permanently leaks ~15-25 such processes, which was observed to
# noticeably (and increasingly, run after run) slow down the whole
# machine. `systemd-run --user --scope` puts the entire run in its own
# cgroup; when the wrapped command exits, systemd kills every remaining
# process in that cgroup regardless of what it got reparented to,
# guaranteeing a clean teardown. The script fails closed if a user systemd
# session is unavailable rather than risking leaked host processes.
#
# Exiting the dropped-into shell tears the nested Shell (and every helper
# process it started) down and removes the private config directory.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v gnome-shell >/dev/null 2>&1; then
    echo "error: gnome-shell not found on PATH; is GNOME Shell installed?" >&2
    exit 1
fi
if ! command -v dbus-run-session >/dev/null 2>&1; then
    echo "error: dbus-run-session not found on PATH; install dbus-user-session" >&2
    exit 1
fi
if ! command -v systemd-run >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1 \
        || ! systemctl --user status >/dev/null 2>&1; then
    echo "error: a working user systemd session is required so every helper" \
         "process started by the nested Shell can be cleaned up safely." >&2
    exit 1
fi

echo
echo "Launching a fully isolated nested GNOME Shell test session..."
echo "  - GNOME Shell version: $(gnome-shell --version)"
echo "  - Extension files, HOME, dconf, caches, and runtime state are staged" \
     "under a temporary directory printed after startup."
echo "  - This shell's D-Bus session is the nested Shell's own: 'et', 'gdbus'," \
     "and 'gnome-extensions' commands run here target it, not your real session."
echo "  - Alt+F2, then 'lg', opens Looking Glass inside the nested Shell."
echo "  - Exit this shell (Ctrl+D / 'exit') to stop the nested Shell and" \
     "discard every staged file and setting."
echo

test_root="$(mktemp -d "${TMPDIR:-/tmp}/et-gnome-shell-test-XXXXXX")"
# shellcheck disable=SC2317 # Invoked by the EXIT trap.
cleanup() {
    "${REPO_ROOT}/scripts/lib/remove-isolated-gnome-shell-env.sh" "${test_root}"
}
trap cleanup EXIT

scope_name="et-test-extension-$$.scope"
set +e
systemd-run --user --scope --collect \
    --unit="et-test-extension-$$" -- \
    env ET_TEST_ROOT="${test_root}" \
    "${REPO_ROOT}/scripts/lib/with-isolated-gnome-shell-env.sh" \
    dbus-run-session -- \
    "${REPO_ROOT}/scripts/lib/run-nested-test-shell.sh"
status=$?
systemctl --user stop "${scope_name}" >/dev/null 2>&1 || true
set -e
exit "${status}"
