#!/usr/bin/env bash
# Removes one disposable test root created for GNOME Shell testing.

set -euo pipefail

test_root="$(realpath -m "${1:?expected the disposable test root path}")"
temporary_parent="$(realpath -m "${TMPDIR:-/tmp}")"
if [ "$(dirname "${test_root}")" != "${temporary_parent}" ] \
        || [[ "$(basename "${test_root}")" != et-gnome-shell-test-* ]]; then
    echo "error: refusing to remove unexpected test root: ${test_root}" >&2
    exit 2
fi

# Portal FUSE mounts can disappear while rm is traversing runtime/doc after
# the private session exits. Retry cleanup briefly, checking mounts each time.
for attempt in 1 2 3; do
    for mount_path in "${test_root}/runtime/doc" "${test_root}/runtime/gvfs"; do
        if mountpoint -q "${mount_path}" 2>/dev/null; then
            if command -v fusermount3 >/dev/null 2>&1; then
                fusermount3 -uz "${mount_path}" 2>/dev/null || true
            elif command -v fusermount >/dev/null 2>&1; then
                fusermount -uz "${mount_path}" 2>/dev/null || true
            fi
            if mountpoint -q "${mount_path}" 2>/dev/null; then
                echo "error: refusing to remove test root while ${mount_path} is mounted" >&2
                exit 1
            fi
        fi
    done

    if rm -rf --one-file-system "${test_root}"; then
        exit 0
    fi
    if [ "${attempt}" -lt 3 ]; then
        sleep 1
    fi
done

echo "error: could not remove disposable test root: ${test_root}" >&2
exit 1
