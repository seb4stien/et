#!/usr/bin/env bash

enable_gnome_extension_in_settings() {
    local extension_uuid="$1"
    local enabled_extensions

    enabled_extensions="$(
        gsettings get org.gnome.shell enabled-extensions \
            | python3 -c '
import ast
import re
import sys

items = ast.literal_eval(re.sub(r"^@\S+\s+", "", sys.stdin.read()))
extension_uuid = sys.argv[1]
if extension_uuid not in items:
    items.append(extension_uuid)
print(repr(items))
' "${extension_uuid}"
    )"
    gsettings set org.gnome.shell enabled-extensions "${enabled_extensions}"
}
