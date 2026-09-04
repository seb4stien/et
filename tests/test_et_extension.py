"""Tests for et.et_extension, mocking the gdbus subprocess calls."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from et.et_extension import (
    ET_DBUS_DEST,
    ET_DBUS_INTERFACE,
    ET_DBUS_OBJECT_PATH,
    EtExtensionError,
    WorkspaceCounter,
    WorkspaceCounterNotFoundError,
    WorkspaceMetadata,
    get_active_workspace_index,
    get_workspace_counter,
    get_workspace_metadata,
    prepare_workspace,
    remap_workspaces,
    remove_workspace,
    reset_workspace_counter,
    set_workspace_metadata,
    sync_workspace_label,
)


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _gdbus_command(method: str, *args: str) -> list[str]:
    return [
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
    ]


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_active_workspace_index_parses_uint32(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="(uint32 2,)\n")
    assert get_active_workspace_index() == 2
    mock_run.assert_called_once_with(
        _gdbus_command("GetActiveWorkspaceIndex"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value=None)
def test_get_active_workspace_index_raises_when_gdbus_missing(_mock_which):
    with pytest.raises(EtExtensionError, match="gdbus"):
        get_active_workspace_index()


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_active_workspace_index_raises_helpful_message_when_extension_missing(
    mock_run, _mock_which
):
    mock_run.return_value = _completed(
        returncode=1,
        stderr=(
            "Error: GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: "
            "No such interface \u201corg.gnome.Shell.Extensions.Et\u201d"
        ),
    )
    with pytest.raises(EtExtensionError, match="et GNOME Shell extension"):
        get_active_workspace_index()


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_active_workspace_index_raises_on_other_dbus_failure(mock_run, _mock_which):
    mock_run.return_value = _completed(returncode=1, stderr="some other dbus error")
    with pytest.raises(EtExtensionError, match="gdbus call"):
        get_active_workspace_index()


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_active_workspace_index_raises_when_output_unparseable(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="()\n")
    with pytest.raises(EtExtensionError, match="could not parse"):
        get_active_workspace_index()


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_prepare_workspace_sends_typed_arguments(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="()\n")
    prepare_workspace(3, "My Task", 3600)
    mock_run.assert_called_once_with(
        _gdbus_command("PrepareWorkspace", "uint32 3", "'My Task'", "uint64 3600"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_prepare_workspace_escapes_quotes_and_backslashes_in_label(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="()\n")
    prepare_workspace(0, "it's a \\test\\", 0)
    mock_run.assert_called_once_with(
        _gdbus_command("PrepareWorkspace", "uint32 0", "'it\\'s a \\\\test\\\\'", "uint64 0"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_workspace_counter_parses_elapsed_and_running(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="(uint64 123, true)\n")
    assert get_workspace_counter(3) == WorkspaceCounter(elapsed_seconds=123, running=True)
    mock_run.assert_called_once_with(
        _gdbus_command("GetWorkspaceCounter", "uint32 3"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_workspace_counter_parses_not_running(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="(uint64 0, false)\n")
    assert get_workspace_counter(0) == WorkspaceCounter(elapsed_seconds=0, running=False)


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_workspace_counter_raises_when_output_unparseable(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="()\n")
    with pytest.raises(EtExtensionError, match="could not parse"):
        get_workspace_counter(0)


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_workspace_counter_distinguishes_unprepared_workspace(mock_run, _mock_which):
    mock_run.return_value = _completed(
        returncode=1,
        stderr=(
            "Error: GDBus.Error:org.gnome.Shell.Extensions.Et.Error.NotFound: "
            "workspace 2 has no prepared counter"
        ),
    )
    with pytest.raises(WorkspaceCounterNotFoundError, match="not prepared"):
        get_workspace_counter(2)


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_reset_workspace_counter_calls_expected_method(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="()\n")
    reset_workspace_counter(2)
    mock_run.assert_called_once_with(
        _gdbus_command("ResetWorkspaceCounter", "uint32 2"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_remove_workspace_calls_expected_method(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="()\n")
    remove_workspace(5)
    mock_run.assert_called_once_with(
        _gdbus_command("RemoveWorkspace", "uint32 5"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_remap_workspaces_sends_array_of_tuples(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="()\n")
    remap_workspaces([(0, 1), (2, 3)])
    mock_run.assert_called_once_with(
        _gdbus_command("RemapWorkspaces", "[(uint32 0, uint32 1), (uint32 2, uint32 3)]"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_remap_workspaces_with_empty_moves_does_not_call_gdbus(mock_run, _mock_which):
    remap_workspaces([])
    mock_run.assert_not_called()


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_set_workspace_metadata_sends_typed_arguments(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="()\n")
    set_workspace_metadata(1, "Another label", 7200)
    mock_run.assert_called_once_with(
        _gdbus_command("SetWorkspaceMetadata", "uint32 1", "'Another label'", "uint64 7200"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_call_raises_generic_error_with_method_name(mock_run, _mock_which):
    mock_run.return_value = _completed(returncode=1, stderr="custom extension error")
    with pytest.raises(EtExtensionError, match="ResetWorkspaceCounter"):
        reset_workspace_counter(0)


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_workspace_metadata_parses_label_and_estimate(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="('My Task', uint64 3600)\n")
    assert get_workspace_metadata(3) == WorkspaceMetadata(label="My Task", estimate_seconds=3600)
    mock_run.assert_called_once_with(
        _gdbus_command("GetWorkspaceMetadata", "uint32 3"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_workspace_metadata_unescapes_quotes_and_backslashes_in_label(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="('it\\'s a \\\\test\\\\', uint64 0)\n")
    assert get_workspace_metadata(0) == WorkspaceMetadata(
        label="it's a \\test\\", estimate_seconds=0
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_workspace_metadata_raises_when_output_unparseable(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="()\n")
    with pytest.raises(EtExtensionError, match="could not parse"):
        get_workspace_metadata(0)


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_get_workspace_metadata_distinguishes_unprepared_workspace(mock_run, _mock_which):
    mock_run.return_value = _completed(
        returncode=1,
        stderr=(
            "Error: GDBus.Error:org.gnome.Shell.Extensions.Et.Error.NotFound: "
            "workspace 2 has no prepared counter"
        ),
    )
    with pytest.raises(WorkspaceCounterNotFoundError, match="not prepared"):
        get_workspace_metadata(2)


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_sync_workspace_label_updates_prepared_workspace_preserving_estimate(
    mock_run, _mock_which
):
    mock_run.side_effect = [
        _completed(stdout="('Old label', uint64 3600)\n"),
        _completed(stdout="()\n"),
    ]
    assert sync_workspace_label(1, "New label") is True
    assert mock_run.call_count == 2
    mock_run.assert_called_with(
        _gdbus_command("SetWorkspaceMetadata", "uint32 1", "'New label'", "uint64 3600"),
        capture_output=True,
        text=True,
        check=False,
    )


@patch("et.et_extension.shutil.which", return_value="/usr/bin/gdbus")
@patch("et.et_extension.subprocess.run")
def test_sync_workspace_label_is_a_silent_no_op_when_not_prepared(mock_run, _mock_which):
    mock_run.return_value = _completed(
        returncode=1,
        stderr=(
            "Error: GDBus.Error:org.gnome.Shell.Extensions.Et.Error.NotFound: "
            "workspace 2 has no prepared counter"
        ),
    )
    assert sync_workspace_label(2, "New label") is False
    mock_run.assert_called_once()


@patch("et.et_extension.shutil.which", return_value=None)
def test_sync_workspace_label_is_a_silent_no_op_when_extension_unreachable(_mock_which):
    assert sync_workspace_label(0, "New label") is False
