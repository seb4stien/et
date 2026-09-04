#!/usr/bin/env bash
# Helper for scripts/test-gnome-shell-extension.sh, run *inside* the private
# dbus-run-session + XDG_CONFIG_HOME environment it sets up. Kept as its own
# file (rather than an inline `bash -c '...'` string) so its quoting doesn't
# have to fight with the outer script's own quoting.
#
# Expects ET_EXTENSION_UUID in the environment.

set -euo pipefail

: "${ET_EXTENSION_UUID:?ET_EXTENSION_UUID must be set}"
: "${ET_TEST_ROOT:?ET_TEST_ROOT must be set}"

echo "  - Disposable test root: ${ET_TEST_ROOT}"

# Pre-seed enabled-extensions in the (empty, private) dconf database
# *before* the Shell starts scanning extensions, so it comes up already
# enabled instead of needing a post-startup retry loop. Preserves whatever
# distro-default extensions are already enabled (e.g. Ubuntu
# dock/appindicators), mirroring install-gnome-shell-extension.sh.
#
# `gsettings get` on an empty array prints the GVariant-annotated form
# `@as []` (needed since an empty array's element type can't otherwise be
# inferred from its contents) instead of plain `[]`; strip that leading
# `@<type> ` annotation before parsing so an empty enabled-extensions list
# doesn't crash this. Also print nothing (not an empty line) when the list
# is empty, so `mapfile` doesn't capture a stray blank element.
mapfile -t current < <(gsettings get org.gnome.shell enabled-extensions \
    | python3 -c '
import ast, re, sys
items = ast.literal_eval(re.sub(r"^@\S+\s+", "", sys.stdin.read()))
print(chr(10).join(items)) if items else None
')
if [[ ! " ${current[*]} " == *" ${ET_EXTENSION_UUID} "* ]]; then
    current+=("${ET_EXTENSION_UUID}")
fi
printf -v joined "'%s', " "${current[@]}"
gsettings set org.gnome.shell enabled-extensions "[${joined%, }]"

gnome-shell --nested --wayland &
nested_shell_pid=$!
trap 'kill "${nested_shell_pid}" 2>/dev/null' EXIT

echo "Waiting for the nested Shell to come up (this can take a couple of minutes)..."
active=0
for _ in $(seq 1 150); do
    # `gnome-extensions info` fails (non-zero) until the Shell's D-Bus
    # service is up, which is exactly the condition being waited out here
    # -- `|| true` keeps that expected, repeated failure from tripping
    # `set -e` and aborting the whole script on the very first attempt.
    info="$(gnome-extensions info "${ET_EXTENSION_UUID}" 2>/dev/null)" || true
    state="$(sed -n 's/^\s*State:\s*//p' <<<"${info}")"
    if [ "${state}" = "ACTIVE" ]; then
        echo "${ET_EXTENSION_UUID} is ACTIVE in the nested Shell"
        active=1
        break
    fi
    # Pre-seeding enabled-extensions above (before the Shell starts) is
    # racy: `gsettings set` can return before the Shell's own read of
    # enabled-extensions at startup, so it can come up with the extension
    # merely INITIALIZED, not ENABLED, and never pick it up on its own.
    # Actively re-assert enablement via the Shell's own D-Bus API once
    # reachable -- idempotent/cheap if already enabled -- instead of only
    # waiting passively.
    if echo "${info}" | grep -q "Enabled:\s*No"; then
        gnome-extensions enable "${ET_EXTENSION_UUID}" 2>/dev/null || true
    fi
    sleep 1
done
if [ "${active}" -ne 1 ]; then
    echo "warning: ${ET_EXTENSION_UUID} did not reach ACTIVE within 150s;" \
         "run 'gnome-extensions info ${ET_EXTENSION_UUID}' in this shell" \
         "to check its current state." >&2
fi

exec bash --noprofile --norc
