#!/usr/bin/env bash
# Installs the Ubuntu system packages used to validate and exercise the GNOME
# Shell extension. Does nothing when all required commands are already present.

set -euo pipefail

required_commands=(
    dbus-run-session
    gjs
    glib-compile-schemas
    gnome-shell
    shellcheck
)

missing=0
for command_name in "${required_commands[@]}"; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        missing=1
        break
    fi
done

if [ "${missing}" -eq 0 ]; then
    exit 0
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "error: install these commands before testing: ${required_commands[*]}" >&2
    exit 1
fi

sudo apt-get update
sudo apt-get install --yes \
    dbus-daemon \
    gjs \
    gnome-shell \
    libglib2.0-bin \
    shellcheck
