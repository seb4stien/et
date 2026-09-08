"""Logic for inspecting and renaming GNOME/Ubuntu workspaces.

This module shells out to `wmctrl` (to find the active workspace on X11) or,
on Wayland, delegates to `et.et_extension` (the companion `et` GNOME Shell
extension's D-Bus client), since `wmctrl` doesn't work under Wayland. It
also uses, via `et.gsettings`, `gsettings` (to read/write GNOME's
workspace-names/workspace-count settings). It has no Typer/CLI dependency
so it can be unit tested by mocking `subprocess.run`.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from et import et_extension, gsettings
from et.et_extension import EtExtensionError
from et.gsettings import GSettingsError

WORKSPACE_NAMES_SCHEMA = "org.gnome.desktop.wm.preferences"
WORKSPACE_NAMES_KEY = "workspace-names"
NUM_WORKSPACES_KEY = "num-workspaces"
MUTTER_SCHEMA = "org.gnome.mutter"
DYNAMIC_WORKSPACES_KEY = "dynamic-workspaces"


class WorkspaceError(RuntimeError):
    """Raised when a workspace operation cannot be completed."""


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise WorkspaceError(f"required command not found: {name}")


def _is_wayland_session() -> bool:
    """Return whether the current session is running under Wayland.

    `wmctrl` only works on X11, so callers use this to decide whether to
    fall back to the `et` GNOME Shell extension's D-Bus service instead.
    """
    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def get_active_workspace_index() -> int:
    """Return the 0-based index of the currently active workspace."""
    if _is_wayland_session():
        try:
            return et_extension.get_active_workspace_index()
        except EtExtensionError as exc:
            raise WorkspaceError(str(exc)) from exc

    _require_binary("wmctrl")
    result = subprocess.run(
        ["wmctrl", "-d"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"wmctrl -d failed: {result.stderr.strip()}")

    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        index_str, status = fields[0], fields[1]
        if status == "*":
            return int(index_str)

    raise WorkspaceError("no active workspace found in `wmctrl -d` output")


def switch_to_workspace(index: int) -> None:
    """Switch GNOME to the workspace at 0-based `index`."""
    _require_binary("wmctrl")
    result = subprocess.run(
        ["wmctrl", "-s", str(index)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"wmctrl -s {index} failed: {result.stderr.strip()}")


def move_active_window_to_workspace(index: int) -> None:
    """Move the currently focused window to the workspace at 0-based `index`.

    Lets a terminal running e.g. `et jira start` follow along to the
    workspace it just switched to, instead of being left behind on the
    workspace it was invoked from.
    """
    _require_binary("wmctrl")
    result = subprocess.run(
        ["wmctrl", "-r", ":ACTIVE:", "-t", str(index)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise WorkspaceError(f"wmctrl -r :ACTIVE: -t {index} failed: {result.stderr.strip()}")


def get_workspace_names() -> list[str]:
    """Return the current list of GNOME workspace names."""
    try:
        return gsettings.read_string_array(WORKSPACE_NAMES_SCHEMA, WORKSPACE_NAMES_KEY)
    except GSettingsError as exc:
        raise WorkspaceError(str(exc)) from exc


def set_workspace_names(names: list[str]) -> None:
    """Write the given list of workspace names to GNOME's settings."""
    try:
        gsettings.write_string_array(WORKSPACE_NAMES_SCHEMA, WORKSPACE_NAMES_KEY, names)
    except GSettingsError as exc:
        raise WorkspaceError(str(exc)) from exc


def is_dynamic_workspaces_enabled() -> bool:
    """Return whether GNOME is set to grow/shrink workspaces dynamically.

    et requires a fixed set of workspaces, so this reads `org.gnome.mutter
    dynamic-workspaces` to let callers verify the user has disabled it.
    """
    try:
        return gsettings.read_boolean(MUTTER_SCHEMA, DYNAMIC_WORKSPACES_KEY)
    except GSettingsError as exc:
        raise WorkspaceError(str(exc)) from exc


def get_workspace_count() -> int:
    """Return the number of fixed GNOME workspaces (`num-workspaces`)."""
    try:
        return gsettings.read_int(WORKSPACE_NAMES_SCHEMA, NUM_WORKSPACES_KEY)
    except GSettingsError as exc:
        raise WorkspaceError(str(exc)) from exc


def set_workspace_count(count: int) -> None:
    """Set the number of fixed GNOME workspaces (`num-workspaces`) to `count`.

    Only touches the workspace count, not `dynamic-workspaces` — disabling
    dynamic workspaces is the user's responsibility (verified separately via
    `is_dynamic_workspaces_enabled`).
    """
    try:
        gsettings.set_int(WORKSPACE_NAMES_SCHEMA, NUM_WORKSPACES_KEY, count)
    except GSettingsError as exc:
        raise WorkspaceError(str(exc)) from exc


def rename_active_workspace(new_name: str) -> int:
    """Rename the active workspace to `new_name`.

    Returns the 0-based index of the workspace that was renamed.
    """
    index = get_active_workspace_index()
    names = get_workspace_names()

    if len(names) <= index:
        names = names + [""] * (index + 1 - len(names))

    names[index] = new_name
    set_workspace_names(names)
    return index


def rename_all_workspaces(new_names: list[str]) -> list[int]:
    """Rename workspaces 0..len(new_names)-1 to `new_names`, in order.

    Any existing workspace at an index >= len(new_names) is left untouched.
    Returns the list of 0-based workspace indices that were renamed.
    """
    names = get_workspace_names()

    if len(names) < len(new_names):
        names = names + [""] * (len(new_names) - len(names))

    for index, name in enumerate(new_names):
        names[index] = name

    set_workspace_names(names)
    return list(range(len(new_names)))
