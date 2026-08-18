"""Tests for et.jira_time, mocking et.workspaces/et.tracker/et.jira."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from et.config import EtConfig, JiraConfig, WorkspaceConfigEntry
from et.jira import JiraError
from et.jira_time import (
    JiraLogTimeError,
    log_manual_time_for_current_workspace,
    log_time_for_current_workspace,
    resolve_issue_key,
)
from et.tracker import TrackerError
from et.workspaces import WorkspaceError


def _config(
    workspaces: list[WorkspaceConfigEntry] | None = None, *, with_jira: bool = True
) -> EtConfig:
    return EtConfig(
        jira=(
            JiraConfig(
                base_url="https://example.atlassian.net/",
                email="me@example.com",
                pat="secret-token",
                jql="assignee = currentUser()",
            )
            if with_jira
            else None
        ),
        workspaces=workspaces or [],
    )


def _timer(workspace_id: int, elapsed: int, name: str = "ET-1") -> dict:
    return {
        "id": "timer-1",
        "name": name,
        "timeElapsed": elapsed,
        "running": True,
        "selected": False,
        "workspaceId": workspace_id,
        "autoResume": True,
    }


@patch("et.jira_time.tracker.save_timers_with_reload")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_logs_elapsed_seconds_and_resets_tracker(
    mock_load_config,
    mock_index,
    mock_load_timers,
    mock_create_worklog,
    mock_save_timers,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_load_timers.return_value = [_timer(0, 4320)]

    result = log_time_for_current_workspace(description="Fixed it")

    assert result.workspace_index == 0
    assert result.issue_key == "ISD-321"
    assert result.seconds_logged == 4320
    assert result.tracker_reset is True

    mock_create_worklog.assert_called_once_with(
        mock_load_config.return_value.jira, "ISD-321", 4320, comment="Fixed it"
    )

    saved_entries, _reason = mock_save_timers.call_args[0]
    assert saved_entries[0]["timeElapsed"] == 0
    assert saved_entries[0]["running"] is False


@patch("et.jira_time.tracker.save_timers_with_reload")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_does_not_reset_tracker_when_disabled(
    mock_load_config,
    mock_index,
    mock_load_timers,
    mock_create_worklog,
    mock_save_timers,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_load_timers.return_value = [_timer(0, 4320)]

    result = log_time_for_current_workspace(reset=False)

    assert result.tracker_reset is False
    mock_save_timers.assert_not_called()


@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_raises_when_no_jira_config(mock_load_config, mock_index):
    mock_load_config.return_value = _config(with_jira=False)

    with pytest.raises(JiraLogTimeError, match="no 'jira' block"):
        log_time_for_current_workspace()


@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_raises_when_no_jira_issue_linked_to_workspace(
    mock_load_config, mock_index
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    with pytest.raises(JiraLogTimeError, match="no Jira issue linked to workspace 1"):
        log_time_for_current_workspace()


@patch("et.jira_time.tracker.load_timers", return_value=[])
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_raises_when_no_tracker_bound_to_workspace(
    mock_load_config, mock_index, mock_load_timers
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )

    with pytest.raises(JiraLogTimeError, match="minimum 60s"):
        log_time_for_current_workspace()


@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_raises_when_elapsed_time_below_minimum(
    mock_load_config, mock_index, mock_load_timers
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_load_timers.return_value = [_timer(0, 30)]

    with pytest.raises(JiraLogTimeError, match="only 30s elapsed"):
        log_time_for_current_workspace()


@patch("et.jira_time.create_worklog", side_effect=JiraError("boom"))
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_wraps_jira_errors(
    mock_load_config, mock_index, mock_load_timers, mock_create_worklog
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_load_timers.return_value = [_timer(0, 4320)]

    with pytest.raises(JiraLogTimeError, match="boom"):
        log_time_for_current_workspace()


@patch(
    "et.jira_time.workspaces.get_active_workspace_index",
    side_effect=WorkspaceError("no wmctrl"),
)
@patch("et.jira_time.load_config")
def test_log_time_propagates_workspace_errors(mock_load_config, mock_index):
    mock_load_config.return_value = _config()

    with pytest.raises(WorkspaceError, match="no wmctrl"):
        log_time_for_current_workspace()


@patch("et.jira_time.tracker.save_timers_with_reload", side_effect=TrackerError("gsettings boom"))
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_wraps_tracker_errors_on_reset(
    mock_load_config,
    mock_index,
    mock_load_timers,
    mock_create_worklog,
    mock_save_timers,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_load_timers.return_value = [_timer(0, 4320)]

    with pytest.raises(JiraLogTimeError, match="gsettings boom"):
        log_time_for_current_workspace()


# --- log_manual_time_for_current_workspace --------------------------------


@patch("et.jira_time.create_worklog")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_manual_time_logs_given_seconds_without_touching_tracker(
    mock_load_config, mock_index, mock_create_worklog
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )

    result = log_manual_time_for_current_workspace(7200, description="Manual entry")

    assert result.workspace_index == 0
    assert result.issue_key == "ISD-321"
    assert result.seconds_logged == 7200
    assert result.tracker_reset is False

    mock_create_worklog.assert_called_once_with(
        mock_load_config.return_value.jira, "ISD-321", 7200, comment="Manual entry"
    )


@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_manual_time_raises_when_no_jira_config(mock_load_config, mock_index):
    mock_load_config.return_value = _config(with_jira=False)

    with pytest.raises(JiraLogTimeError, match="no 'jira' block"):
        log_manual_time_for_current_workspace(7200)


@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_manual_time_raises_when_no_jira_issue_linked_to_workspace(
    mock_load_config, mock_index
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    with pytest.raises(JiraLogTimeError, match="no Jira issue linked to workspace 1"):
        log_manual_time_for_current_workspace(7200)


@patch("et.jira_time.create_worklog", side_effect=JiraError("boom"))
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_manual_time_wraps_jira_errors(mock_load_config, mock_index, mock_create_worklog):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )

    with pytest.raises(JiraLogTimeError, match="boom"):
        log_manual_time_for_current_workspace(7200)


@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_uses_explicit_issue_key_override(
    mock_load_config, mock_index, mock_load_timers, mock_create_worklog
):
    # Even with an override, the workspace index is still needed (Tracker
    # timer / workspace deletion are tied to the physical workspace, not
    # the ticket), so get_active_workspace_index is still called.
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_load_timers.return_value = [_timer(0, 4320)]

    with patch("et.jira_time.tracker.save_timers_with_reload"):
        result = log_time_for_current_workspace(issue_key="ISD-999")

    assert result.issue_key == "ISD-999"
    mock_create_worklog.assert_called_once_with(
        mock_load_config.return_value.jira, "ISD-999", 4320, comment=None
    )


@patch("et.jira_time.create_worklog")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_manual_time_uses_explicit_issue_key_override(
    mock_load_config, mock_index, mock_create_worklog
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )

    result = log_manual_time_for_current_workspace(7200, issue_key="ISD-999")

    assert result.issue_key == "ISD-999"
    mock_create_worklog.assert_called_once_with(
        mock_load_config.return_value.jira, "ISD-999", 7200, comment=None
    )


@patch("et.jira_time.workspaces.get_active_workspace_index")
@patch("et.jira_time.load_config")
def test_resolve_issue_key_skips_workspace_lookup_when_override_given(
    mock_load_config, mock_index
):
    mock_load_config.return_value = _config(with_jira=True)

    jira_config, resolved_key = resolve_issue_key(
        mock_load_config.return_value, issue_key="ISD-42"
    )

    assert resolved_key == "ISD-42"
    assert jira_config is mock_load_config.return_value.jira
    mock_index.assert_not_called()


def test_resolve_issue_key_raises_when_override_given_but_no_jira_config():
    config = _config(with_jira=False)

    with pytest.raises(JiraLogTimeError, match="no 'jira' block"):
        resolve_issue_key(config, issue_key="ISD-42")


@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
def test_resolve_issue_key_falls_back_to_active_workspace_without_override(mock_index):
    config = _config([WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")])

    jira_config, resolved_key = resolve_issue_key(config)

    assert resolved_key == "ISD-321"
    assert jira_config is config.jira
    mock_index.assert_called_once()
