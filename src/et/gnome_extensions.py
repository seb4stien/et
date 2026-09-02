"""Thin wrapper around the `gnome-extensions` CLI.

Used to temporarily disable/re-enable a GNOME Shell extension around a
GSettings write. A *running* extension often keeps its own in-memory copy
of its settings and silently overwrites (clobbers) externally-written
changes it doesn't already know about the next time it resaves. Disabling
the extension before the write and re-enabling it afterwards forces a full
re-initialization that re-reads GSettings from scratch, so the external
change is picked up instead of being erased.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager

MAX_ATTEMPTS = 3
RETRY_INTERVAL_SECONDS = 5


class GnomeExtensionsError(RuntimeError):
    """Raised when a `gnome-extensions` operation cannot be completed."""


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run `gnome-extensions <args>`, retrying transient failures.

    A failure to reach the running GNOME Shell (e.g. right after a session
    change, such as deleting a workspace) is often transient, so a failed
    invocation is retried up to `MAX_ATTEMPTS` times, waiting
    `RETRY_INTERVAL_SECONDS` between attempts, before giving up.
    """
    if shutil.which("gnome-extensions") is None:
        raise GnomeExtensionsError("required command not found: gnome-extensions")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        result = subprocess.run(
            ["gnome-extensions", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 or attempt == MAX_ATTEMPTS:
            return result
        time.sleep(RETRY_INTERVAL_SECONDS)
    raise AssertionError("unreachable: loop always returns or raises")


def is_extension_enabled(uuid: str) -> bool:
    """Return whether the extension identified by `uuid` is currently enabled."""
    result = _run("list", "--enabled")
    if result.returncode != 0:
        raise GnomeExtensionsError(f"gnome-extensions list failed: {result.stderr.strip()}")
    return uuid in result.stdout.splitlines()


def disable_extension(uuid: str) -> None:
    """Disable the extension identified by `uuid`."""
    result = _run("disable", uuid)
    if result.returncode != 0:
        raise GnomeExtensionsError(f"gnome-extensions disable failed: {result.stderr.strip()}")


def enable_extension(uuid: str) -> None:
    """Enable the extension identified by `uuid`."""
    result = _run("enable", uuid)
    if result.returncode != 0:
        raise GnomeExtensionsError(f"gnome-extensions enable failed: {result.stderr.strip()}")


@contextmanager
def reload_around(uuid: str) -> Iterator[None]:
    """Disable `uuid` (if enabled) for the duration of the block, then re-enable it.

    If the extension isn't currently enabled, this is a no-op: there's no
    running instance to clobber the change, so nothing needs disabling.
    Re-enabling always happens (even if the block raises) so the extension
    is never left disabled because of a write failure.
    """
    was_enabled = is_extension_enabled(uuid)
    if was_enabled:
        disable_extension(uuid)
    try:
        yield
    finally:
        if was_enabled:
            enable_extension(uuid)
