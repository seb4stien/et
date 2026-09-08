"""Typed D-Bus client for the in-house `et` GNOME Shell extension.

Replaces the third-party Tracker extension's GSettings-JSON-based timer
storage: the `et` extension (`et@seb4stien.github.com`) now owns
per-workspace counters itself, exposed over its own small D-Bus service on
GNOME Shell's session-bus connection (destination `org.gnome.Shell`, object
path `/org/gnome/Shell/Extensions/Et`, interface
`org.gnome.Shell.Extensions.Et`). Every call in this module shells out to
`gdbus call` and centralizes invocation/error handling: a single `_call`
helper builds the command line, decides whether a failure means "the
extension isn't installed/enabled" (gdbus reports an Unknown{Method,Object,
Interface} error) or some other problem, and raises `EtExtensionError`
accordingly. Has no Typer/CLI dependency so callers can unit test by mocking
`subprocess.run`.

The extension is deliberately ticket-system agnostic: `label` and
`estimate_seconds` are just generic display values (for Jira workspaces,
the CLI passes the issue summary and original estimate), never Jira
terminology itself.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

ET_DBUS_DEST = "org.gnome.Shell"
ET_DBUS_OBJECT_PATH = "/org/gnome/Shell/Extensions/Et"
ET_DBUS_INTERFACE = "org.gnome.Shell.Extensions.Et"
ET_EXTENSION_UUID = "et@seb4stien.github.com"

# gdbus reports these as the error name/message when the destination,
# object, or interface it was asked to call doesn't exist yet -- the
# tell-tale sign that the et extension itself isn't installed/enabled,
# rather than some other D-Bus failure.
_NOT_INSTALLED_TOKENS = ("UnknownMethod", "UnknownObject", "UnknownInterface")
_COUNTER_NOT_FOUND_ERROR = f"{ET_DBUS_INTERFACE}.Error.NotFound"


class EtExtensionError(RuntimeError):
    """Raised when a D-Bus call to the et GNOME Shell extension fails."""


class WorkspaceCounterNotFoundError(EtExtensionError):
    """Raised when a workspace has not been prepared with a counter."""


@dataclass(frozen=True)
class WorkspaceCounter:
    """A workspace's tracked elapsed time and running state.

    Returned by `get_workspace_counter`, mirroring the extension's
    `GetWorkspaceCounter(u index) -> (t elapsedSeconds, b running)` D-Bus
    method.
    """

    elapsed_seconds: int
    running: bool


@dataclass(frozen=True)
class WorkspaceMetadata:
    """A workspace's current display label and reference estimate.

    Returned by `get_workspace_metadata`, mirroring the extension's
    `GetWorkspaceMetadata(u index) -> (s label, t estimateSeconds)` D-Bus
    method.
    """

    label: str
    estimate_seconds: int


def _quote_gvariant_string(value: str) -> str:
    """Return `value` as a single-quoted GVariant text-format string literal.

    Backslashes and single quotes are escaped so arbitrary labels (Jira
    summaries, in practice) round-trip through `gdbus call` unscathed.
    """
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _unescape_gvariant_string(value: str) -> str:
    """Reverse `_quote_gvariant_string`'s escaping on a parsed `gdbus` string."""
    return re.sub(r"\\(.)", r"\1", value)


def _require_gdbus() -> None:
    if shutil.which("gdbus") is None:
        raise EtExtensionError("required command not found: gdbus")


def _call(method: str, *args: str) -> str:
    """Invoke `method` on the et extension's D-Bus object and return raw stdout.

    Centralizes every gdbus invocation used by this module: builds the
    `gdbus call --session --dest ... --method Interface.Method ARGS...`
    command line, and turns a non-zero exit into a helpful
    `EtExtensionError` -- one message when gdbus reports an
    Unknown{Method,Object,Interface} error (the extension isn't
    installed/enabled), another for any other failure.
    """
    _require_gdbus()
    result = subprocess.run(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            ET_DBUS_DEST,
            "--object-path",
            ET_DBUS_OBJECT_PATH,
            "--method",
            f"{ET_DBUS_INTERFACE}.{method}",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if _COUNTER_NOT_FOUND_ERROR in stderr:
            raise WorkspaceCounterNotFoundError(
                f"workspace counter is not prepared: {stderr}"
            )
        if any(token in stderr for token in _NOT_INSTALLED_TOKENS):
            raise EtExtensionError(
                "the et GNOME Shell extension doesn't seem to be installed/enabled "
                f"(`gdbus call` failed: {stderr}). Run "
                "`scripts/install-gnome-shell-extension.sh` (or "
                f"`gnome-extensions enable {ET_EXTENSION_UUID}`) and log back in."
            )
        raise EtExtensionError(f"gdbus call to {method} failed: {stderr}")
    return result.stdout


def get_active_workspace_index() -> int:
    """Return the 0-based index of the currently active workspace.

    Used on Wayland, where `wmctrl` can't see the compositor's state (see
    `et.workspaces.get_active_workspace_index`, which falls back to this on
    a detected Wayland session).
    """
    stdout = _call("GetActiveWorkspaceIndex")
    match = re.search(r"uint32\s+(\d+)", stdout)
    if match is None:
        raise EtExtensionError(
            f"could not parse active workspace index from gdbus output: {stdout!r}"
        )
    return int(match.group(1))


def prepare_workspace(index: int, label: str, estimate_seconds: int) -> None:
    """Ensure workspace `index`'s counter is reset to zero, with `label`/`estimate_seconds` set.

    Called whenever et allocates a workspace slot for a new task (whether
    the slot is brand new or a reused one), so a reused slot never inherits
    a previous task's accumulated time.
    """
    _call(
        "PrepareWorkspace",
        f"uint32 {index}",
        _quote_gvariant_string(label),
        f"uint64 {estimate_seconds}",
    )


def get_workspace_counter(index: int) -> WorkspaceCounter:
    """Return workspace `index`'s current elapsed time and running state."""
    stdout = _call("GetWorkspaceCounter", f"uint32 {index}")
    match = re.search(r"uint64\s+(\d+)\s*,\s*(true|false)", stdout)
    if match is None:
        raise EtExtensionError(f"could not parse workspace counter from gdbus output: {stdout!r}")
    return WorkspaceCounter(elapsed_seconds=int(match.group(1)), running=match.group(2) == "true")


def get_workspace_metadata(index: int) -> WorkspaceMetadata:
    """Return workspace `index`'s current display label and reference estimate.

    Raises `WorkspaceCounterNotFoundError` if the workspace was never
    prepared (the extension reports the same not-found error as
    `get_workspace_counter` for that case).
    """
    stdout = _call("GetWorkspaceMetadata", f"uint32 {index}")
    match = re.search(r"'((?:[^'\\]|\\.)*)'\s*,\s*uint64\s+(\d+)", stdout)
    if match is None:
        raise EtExtensionError(
            f"could not parse workspace metadata from gdbus output: {stdout!r}"
        )
    return WorkspaceMetadata(
        label=_unescape_gvariant_string(match.group(1)),
        estimate_seconds=int(match.group(2)),
    )


def reset_workspace_counter(index: int) -> None:
    """Reset workspace `index`'s counter to zero (elapsed time and running state)."""
    _call("ResetWorkspaceCounter", f"uint32 {index}")


def remove_workspace(index: int) -> None:
    """Discard workspace `index`'s counter and display metadata entirely."""
    _call("RemoveWorkspace", f"uint32 {index}")


def remap_workspaces(moves: Sequence[tuple[int, int]]) -> None:
    """Atomically relocate each `(old_index, new_index)` pair in `moves`.

    Used when workspaces shift (`et ws delete`, `et jira complete`) or are
    freely permuted (`et ws organize`), so every counter follows its
    workspace to its new slot in a single D-Bus call rather than one call
    per moved slot. A no-op (no D-Bus call at all) when `moves` is empty.
    """
    if not moves:
        return
    pairs = ", ".join(f"(uint32 {old}, uint32 {new})" for old, new in moves)
    _call("RemapWorkspaces", f"[{pairs}]")


def set_workspace_metadata(index: int, label: str, estimate_seconds: int) -> None:
    """Update workspace `index`'s display label/estimate without touching its counter."""
    _call(
        "SetWorkspaceMetadata",
        f"uint32 {index}",
        _quote_gvariant_string(label),
        f"uint64 {estimate_seconds}",
    )


def sync_workspace_label(index: int, label: str) -> bool:
    """Best-effort: update workspace `index`'s stored label to `label`, if it's tracked.

    Renaming a GNOME workspace (`et ws rename`) is a lower-level primitive
    than "preparing" one for tracking (`prepare_workspace`), and must never
    gain a hard dependency on the et extension, nor ever start/reset
    tracking for a workspace that isn't already tracked (including
    `static` ones, which are never prepared). So this silently does
    nothing — returning `False` — both when `index` was never prepared
    (`WorkspaceCounterNotFoundError`) and when the extension itself can't
    be reached at all (any other `EtExtensionError`, e.g. not installed/
    enabled, or `gdbus` missing), mirroring the existing best-effort
    `move_active_window_to_workspace` step in `task.create_task_workspace`.
    Preserves the workspace's existing estimate and never touches its
    counter (only `SetWorkspaceMetadata` is called, never
    `PrepareWorkspace`). Returns whether the label was actually updated.
    """
    try:
        metadata = get_workspace_metadata(index)
    except EtExtensionError:
        return False

    try:
        set_workspace_metadata(index, label, metadata.estimate_seconds)
    except EtExtensionError:
        return False
    return True


__all__ = [
    "ET_DBUS_DEST",
    "ET_DBUS_OBJECT_PATH",
    "ET_DBUS_INTERFACE",
    "ET_EXTENSION_UUID",
    "EtExtensionError",
    "WorkspaceCounterNotFoundError",
    "WorkspaceCounter",
    "WorkspaceMetadata",
    "get_active_workspace_index",
    "prepare_workspace",
    "get_workspace_counter",
    "get_workspace_metadata",
    "reset_workspace_counter",
    "remove_workspace",
    "remap_workspaces",
    "set_workspace_metadata",
    "sync_workspace_label",
]
