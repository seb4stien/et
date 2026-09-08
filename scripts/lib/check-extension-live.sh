#!/usr/bin/env bash
# Helper for scripts/check-gnome-shell-extension-live.sh, run *inside* the
# private dbus-run-session + XDG_CONFIG_HOME environment it sets up.
#
# Expects ET_EXTENSION_UUID and ET_ACTIVATION_TIMEOUT_SECONDS in the
# environment. Exits non-zero if the extension fails to activate or any
# D-Bus method call doesn't behave as expected.

set -uo pipefail

: "${ET_EXTENSION_UUID:?ET_EXTENSION_UUID must be set}"
: "${ET_ACTIVATION_TIMEOUT_SECONDS:?ET_ACTIVATION_TIMEOUT_SECONDS must be set}"
: "${ET_TEST_ROOT:?ET_TEST_ROOT must be set}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/lib/enable-extension-setting.sh
source "${REPO_ROOT}/scripts/lib/enable-extension-setting.sh"

DEST="org.gnome.Shell"
OBJECT_PATH="/org/gnome/Shell/Extensions/Et"
IFACE="org.gnome.Shell.Extensions.Et"
SHELL_LOG="${ET_TEST_ROOT}/gnome-shell.log"

failures=0

save_diagnostics() {
    if [ -n "${ET_DIAGNOSTICS_DIR:-}" ]; then
        mkdir -p "${ET_DIAGNOSTICS_DIR}"
        cp "${SHELL_LOG}" "${ET_DIAGNOSTICS_DIR}/gnome-shell.log"
        gnome-extensions info "${ET_EXTENSION_UUID}" \
            >"${ET_DIAGNOSTICS_DIR}/extension-info.txt" 2>&1 || true
    fi
}

# Pre-seed enabled-extensions before the Shell starts scanning extensions
# (see scripts/lib/run-nested-test-shell.sh for the full rationale/quirks:
# the `@as []` GVariant-annotated empty array form, and why nothing should
# be printed for an empty list).
enable_gnome_extension_in_settings "${ET_EXTENSION_UUID}"

gnome-shell --headless --wayland >"${SHELL_LOG}" 2>&1 &
shell_pid=$!
trap 'kill "${shell_pid}" 2>/dev/null || true; wait "${shell_pid}" 2>/dev/null || true' EXIT

wait_for_state() {
    local expected_state="$1"
    local attempts="$2"
    local info state
    for _ in $(seq 1 "${attempts}"); do
        info="$(gnome-extensions info "${ET_EXTENSION_UUID}" 2>/dev/null)" || true
        state="$(sed -n 's/^\s*State:\s*//p' <<<"${info}")"
        if [ "${state}" = "${expected_state}" ]; then
            return 0
        fi
        if [ "${expected_state}" = "ACTIVE" ] && echo "${info}" | grep -q "Enabled:\s*No"; then
            gnome-extensions enable "${ET_EXTENSION_UUID}" 2>/dev/null || true
        fi
        sleep 1
    done
    return 1
}

echo "Waiting for ${ET_EXTENSION_UUID} to activate (up to ${ET_ACTIVATION_TIMEOUT_SECONDS}s)..."
if ! wait_for_state ACTIVE "${ET_ACTIVATION_TIMEOUT_SECONDS}"; then
    echo "FAIL: ${ET_EXTENSION_UUID} did not reach ACTIVE within ${ET_ACTIVATION_TIMEOUT_SECONDS}s" >&2
    save_diagnostics
    cat "${SHELL_LOG}" >&2
    exit 1
fi
echo "PASS: ${ET_EXTENSION_UUID} reached ACTIVE"

call() {
    gdbus call --session --dest "${DEST}" --object-path "${OBJECT_PATH}" \
        --method "${IFACE}.$1" "${@:2}"
}

# Asserts `call "$@"` (a method name plus its positional args) succeeds and
# its output contains $expect_substring; on mismatch, records a failure and
# prints the actual output for debugging, but keeps running the rest of the
# checks so a single early failure doesn't hide later ones.
assert_call_contains() {
    local expect_substring="$1"
    shift
    local out
    out="$(call "$@" 2>&1)"
    if [[ "${out}" == *"${expect_substring}"* ]]; then
        echo "PASS: $1 -> ${out}"
    else
        echo "FAIL: $1 expected to contain '${expect_substring}', got: ${out}" >&2
        failures=$((failures + 1))
    fi
}

# Asserts a call succeeds and its output matches an extended regular
# expression. This is used for elapsed time, where the exact value is expected
# to vary slightly with scheduling.
assert_call_matches() {
    local expected_regex="$1"
    shift
    local out
    out="$(call "$@" 2>&1)"
    if [[ "${out}" =~ ${expected_regex} ]]; then
        echo "PASS: $1 -> ${out}"
    else
        echo "FAIL: $1 expected to match '${expected_regex}', got: ${out}" >&2
        failures=$((failures + 1))
    fi
}

# 1. Invalid and never-prepared workspaces expose stable D-Bus errors.
assert_call_contains "Error.NotFound" GetWorkspaceMetadata 3
assert_call_contains "Error.InvalidArgument" PrepareWorkspace 999 "invalid" 0

# 2. Prepare active workspace 0, then verify metadata and the running counter.
assert_call_contains "()" PrepareWorkspace 0 "smoke-test" 120
assert_call_contains "'smoke-test', uint64 120" GetWorkspaceMetadata 0
assert_call_contains "true" GetWorkspaceCounter 0
sleep 2
assert_call_matches "uint64 [1-9][0-9]*, true" GetWorkspaceCounter 0

# 3. Update its label without resetting the counter, then reset it.
# sync_workspace_label's usage from `et ws rename`).
assert_call_contains "()" SetWorkspaceMetadata 0 "smoke-test-renamed" 120
assert_call_contains "'smoke-test-renamed', uint64 120" GetWorkspaceMetadata 0
assert_call_contains "()" ResetWorkspaceCounter 0
assert_call_contains "true" GetWorkspaceCounter 0

# 4. Remapping is simultaneous and rejects duplicate endpoints.
assert_call_contains "()" RemapWorkspaces "[(uint32 0, uint32 1)]"
assert_call_contains "'smoke-test-renamed', uint64 120" GetWorkspaceMetadata 1
assert_call_contains "Error.InvalidArgument" RemapWorkspaces \
    "[(uint32 1, uint32 0), (uint32 1, uint32 2)]"
assert_call_contains "()" RemapWorkspaces "[(uint32 1, uint32 0)]"

# 5. GetActiveWorkspaceIndex responds (headless Shell always reports 0).
assert_call_contains "uint32 0" GetActiveWorkspaceIndex

# 6. Disable/re-enable restores persisted data and re-exports D-Bus cleanly.
if gnome-extensions disable "${ET_EXTENSION_UUID}" \
        && wait_for_state INACTIVE "${ET_ACTIVATION_TIMEOUT_SECONDS}"; then
    echo "PASS: extension disabled cleanly"
else
    echo "FAIL: extension did not disable cleanly" >&2
    failures=$((failures + 1))
fi
if gnome-extensions enable "${ET_EXTENSION_UUID}" \
        && wait_for_state ACTIVE "${ET_ACTIVATION_TIMEOUT_SECONDS}"; then
    echo "PASS: extension re-enabled cleanly"
else
    echo "FAIL: extension did not re-enable cleanly" >&2
    failures=$((failures + 1))
fi
assert_call_contains "'smoke-test-renamed', uint64 120" GetWorkspaceMetadata 0

# 7. Removing it makes it unprepared again.
assert_call_contains "()" RemoveWorkspace 0
assert_call_contains "Error.NotFound" GetWorkspaceMetadata 0

if [ "${failures}" -eq 0 ]; then
    echo "All live D-Bus checks passed."
    exit 0
else
    echo "${failures} live D-Bus check(s) failed." >&2
    save_diagnostics
    cat "${SHELL_LOG}" >&2
    exit 1
fi
