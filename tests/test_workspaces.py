"""Tests for et.workspaces, mocking wmctrl/gsettings subprocess calls."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from et.et_extension import EtExtensionError
from et.gsettings import GSettingsError
from et.workspaces import (
    WorkspaceError,
    get_active_workspace_index,
    get_workspace_count,
    get_workspace_names,
    is_dynamic_workspaces_enabled,
    move_active_window_to_workspace,
    rename_active_workspace,
    rename_all_workspaces,
    set_workspace_count,
    set_workspace_names,
    switch_to_workspace,
)


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


WMCTRL_OUTPUT = (
    "0  - DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 1\n"
    "1  * DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 2\n"
    "2  - DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 3\n"
)


@patch("et.workspaces._is_wayland_session", return_value=False)
@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_get_active_workspace_index_returns_marked_index(mock_run, _mock_which, _mock_wayland):
    mock_run.return_value = _completed(stdout=WMCTRL_OUTPUT)
    assert get_active_workspace_index() == 1


@patch("et.workspaces._is_wayland_session", return_value=False)
@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_get_active_workspace_index_raises_when_no_marker(mock_run, _mock_which, _mock_wayland):
    mock_run.return_value = _completed(
        stdout="0  - DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1055  Workspace 1\n"
    )
    with pytest.raises(WorkspaceError, match="no active workspace"):
        get_active_workspace_index()


@patch("et.workspaces._is_wayland_session", return_value=False)
@patch("et.workspaces.shutil.which", return_value=None)
def test_get_active_workspace_index_raises_when_wmctrl_missing(_mock_which, _mock_wayland):
    with pytest.raises(WorkspaceError, match="wmctrl"):
        get_active_workspace_index()


@pytest.mark.parametrize(
    ("xdg_session_type", "wayland_display", "expected"),
    [
        ("wayland", "", True),
        ("x11", "", False),
        ("", "wayland-0", True),
        ("", "", False),
    ],
)
def test_is_wayland_session_detects_from_env(xdg_session_type, wayland_display, expected):
    from et.workspaces import _is_wayland_session

    env = {"XDG_SESSION_TYPE": xdg_session_type, "WAYLAND_DISPLAY": wayland_display}
    with patch("et.workspaces.os.environ", env):
        assert _is_wayland_session() is expected


@patch("et.workspaces._is_wayland_session", return_value=True)
@patch("et.workspaces.et_extension.get_active_workspace_index", return_value=2)
def test_get_active_workspace_index_delegates_to_et_extension_on_wayland(
    mock_get_index, _mock_wayland
):
    assert get_active_workspace_index() == 2
    mock_get_index.assert_called_once_with()


@patch("et.workspaces._is_wayland_session", return_value=True)
@patch(
    "et.workspaces.et_extension.get_active_workspace_index",
    side_effect=EtExtensionError("the et GNOME Shell extension doesn't seem to be installed"),
)
def test_get_active_workspace_index_wraps_et_extension_error(mock_get_index, _mock_wayland):
    with pytest.raises(WorkspaceError, match="et GNOME Shell extension"):
        get_active_workspace_index()


@patch(
    "et.workspaces.gsettings.read_string_array",
    return_value=["", "Focus", ""],
)
def test_get_workspace_names_parses_list(mock_read):
    assert get_workspace_names() == ["", "Focus", ""]
    mock_read.assert_called_once_with(
        "org.gnome.desktop.wm.preferences", "workspace-names"
    )


@patch("et.workspaces.gsettings.read_string_array", side_effect=GSettingsError("boom"))
def test_get_workspace_names_wraps_gsettings_error(mock_read):
    with pytest.raises(WorkspaceError, match="boom"):
        get_workspace_names()


@patch("et.workspaces.gsettings.write_string_array")
def test_set_workspace_names_calls_gsettings_helper(mock_write):
    set_workspace_names(["", "Focus"])
    mock_write.assert_called_once_with(
        "org.gnome.desktop.wm.preferences", "workspace-names", ["", "Focus"]
    )


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["", ""])
@patch("et.workspaces.get_active_workspace_index", return_value=2)
def test_rename_active_workspace_pads_list_when_needed(
    mock_get_index, mock_get_names, mock_set_names
):
    index = rename_active_workspace("focus")
    assert index == 2
    mock_set_names.assert_called_once_with(["", "", "focus"])


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["a", "b", "c"])
@patch("et.workspaces.get_active_workspace_index", return_value=1)
def test_rename_active_workspace_replaces_existing_name(
    mock_get_index, mock_get_names, mock_set_names
):
    index = rename_active_workspace("focus")
    assert index == 1
    mock_set_names.assert_called_once_with(["a", "focus", "c"])


@patch("et.workspaces.gsettings.read_boolean", return_value=True)
def test_is_dynamic_workspaces_enabled_reads_mutter_setting(mock_read_boolean):
    assert is_dynamic_workspaces_enabled() is True
    mock_read_boolean.assert_called_once_with("org.gnome.mutter", "dynamic-workspaces")


@patch("et.workspaces.gsettings.read_boolean", side_effect=GSettingsError("boom"))
def test_is_dynamic_workspaces_enabled_wraps_gsettings_error(mock_read_boolean):
    with pytest.raises(WorkspaceError, match="boom"):
        is_dynamic_workspaces_enabled()


@patch("et.workspaces.gsettings.read_int", return_value=4)
def test_get_workspace_count_reads_num_workspaces(mock_read_int):
    assert get_workspace_count() == 4
    mock_read_int.assert_called_once_with(
        "org.gnome.desktop.wm.preferences", "num-workspaces"
    )


@patch("et.workspaces.gsettings.read_int", side_effect=GSettingsError("boom"))
def test_get_workspace_count_wraps_gsettings_error(mock_read_int):
    with pytest.raises(WorkspaceError, match="boom"):
        get_workspace_count()


@patch("et.workspaces.gsettings.set_int")
def test_set_workspace_count_sets_num_workspaces_only(mock_set_int):
    set_workspace_count(7)
    mock_set_int.assert_called_once_with(
        "org.gnome.desktop.wm.preferences", "num-workspaces", 7
    )


@patch("et.workspaces.gsettings.set_int", side_effect=GSettingsError("boom"))
def test_set_workspace_count_wraps_gsettings_error(mock_set_int):
    with pytest.raises(WorkspaceError, match="boom"):
        set_workspace_count(7)


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["old-a", "old-b", "old-c"])
def test_rename_all_workspaces_replaces_leading_names(mock_get_names, mock_set_names):
    indices = rename_all_workspaces(["mails", "handson"])
    assert indices == [0, 1]
    mock_set_names.assert_called_once_with(["mails", "handson", "old-c"])


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=["old-a"])
def test_rename_all_workspaces_pads_when_config_has_more_entries(mock_get_names, mock_set_names):
    indices = rename_all_workspaces(["mails", "handson", "isd-321"])
    assert indices == [0, 1, 2]
    mock_set_names.assert_called_once_with(["mails", "handson", "isd-321"])


@patch("et.workspaces.set_workspace_names")
@patch("et.workspaces.get_workspace_names", return_value=[])
def test_rename_all_workspaces_with_empty_list_is_noop_write(mock_get_names, mock_set_names):
    indices = rename_all_workspaces([])
    assert indices == []
    mock_set_names.assert_called_once_with([])


@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_switch_to_workspace_calls_wmctrl_switch(mock_run, _mock_which):
    mock_run.return_value = _completed()
    switch_to_workspace(3)
    mock_run.assert_called_once_with(
        ["wmctrl", "-s", "3"], capture_output=True, text=True, check=False
    )


@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_switch_to_workspace_raises_on_failure(mock_run, _mock_which):
    mock_run.return_value = _completed(stderr="no such workspace", returncode=1)
    with pytest.raises(WorkspaceError, match="no such workspace"):
        switch_to_workspace(3)


@patch("et.workspaces.shutil.which", return_value=None)
def test_switch_to_workspace_raises_when_wmctrl_missing(_mock_which):
    with pytest.raises(WorkspaceError, match="wmctrl"):
        switch_to_workspace(0)


@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_move_active_window_to_workspace_calls_wmctrl_move(mock_run, _mock_which):
    mock_run.return_value = _completed()
    move_active_window_to_workspace(3)
    mock_run.assert_called_once_with(
        ["wmctrl", "-r", ":ACTIVE:", "-t", "3"], capture_output=True, text=True, check=False
    )


@patch("et.workspaces.shutil.which", return_value="/usr/bin/wmctrl")
@patch("et.workspaces.subprocess.run")
def test_move_active_window_to_workspace_raises_on_failure(mock_run, _mock_which):
    mock_run.return_value = _completed(stderr="no such window", returncode=1)
    with pytest.raises(WorkspaceError, match="no such window"):
        move_active_window_to_workspace(3)


@patch("et.workspaces.shutil.which", return_value=None)
def test_move_active_window_to_workspace_raises_when_wmctrl_missing(_mock_which):
    with pytest.raises(WorkspaceError, match="wmctrl"):
        move_active_window_to_workspace(0)
