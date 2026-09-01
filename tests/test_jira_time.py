"""Tests for et.jira_time, mocking et.workspaces/et.tracker/et.jira."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from et.config import EtConfig, JiraConfig, WorkspaceConfigEntry
from et.jira import JiraError, JiraIssue
from et.jira_time import (
    JiraLogTimeError,
    log_manual_time_for_current_workspace,
    log_time_for_all_workspaces,
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


def _issue(key: str, *, summary: str = "Some summary") -> JiraIssue:
    return JiraIssue(key=key, summary=summary, priority="Medium", status="In Progress")


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


# --- log_time_for_all_workspaces -------------------------------------------


@patch("et.jira_time.fetch_issue")
@patch("et.jira_time.tracker.save_timers_with_reload")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.load_config")
def test_log_all_logs_and_resets_every_linked_workspace(
    mock_load_config, mock_load_timers, mock_create_worklog, mock_save_timers, mock_fetch_issue
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321"),
            WorkspaceConfigEntry(name="misc"),
            WorkspaceConfigEntry(name="ISD-654", ref="jira:ISD-654"),
        ]
    )
    mock_load_timers.return_value = [_timer(0, 3600), _timer(2, 1800, name="ET-3")]
    mock_fetch_issue.side_effect = lambda _jira_config, key: _issue(key, summary=f"{key} summary")

    result = log_time_for_all_workspaces(description="Weekly sync")

    assert [r.issue_key for r in result.logged] == ["ISD-321", "ISD-654"]
    assert [r.seconds_logged for r in result.logged] == [3600, 1800]
    assert [r.summary for r in result.logged] == ["ISD-321 summary", "ISD-654 summary"]
    assert all(r.tracker_reset for r in result.logged)
    assert result.skipped == []

    assert mock_create_worklog.call_count == 2
    mock_create_worklog.assert_any_call(
        mock_load_config.return_value.jira, "ISD-321", 3600, comment="Weekly sync"
    )
    mock_create_worklog.assert_any_call(
        mock_load_config.return_value.jira, "ISD-654", 1800, comment="Weekly sync"
    )
    # One save per successfully-logged workspace, not batched.
    assert mock_save_timers.call_count == 2


@patch("et.jira_time.fetch_issue", side_effect=JiraError("summary fetch failed"))
@patch("et.jira_time.tracker.save_timers_with_reload")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.load_config")
def test_log_all_falls_back_to_empty_summary_when_fetch_fails(
    mock_load_config, mock_load_timers, mock_create_worklog, mock_save_timers, mock_fetch_issue
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_load_timers.return_value = [_timer(0, 3600)]

    result = log_time_for_all_workspaces()

    assert result.logged[0].issue_key == "ISD-321"
    assert result.logged[0].summary == ""


@patch("et.jira_time.tracker.save_timers_with_reload")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.load_config")
def test_log_all_skips_workspace_below_minimum_elapsed(
    mock_load_config, mock_load_timers, mock_create_worklog, mock_save_timers
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_load_timers.return_value = [_timer(0, 10)]

    result = log_time_for_all_workspaces()

    assert result.logged == []
    assert len(result.skipped) == 1
    assert result.skipped[0].workspace_index == 0
    assert result.skipped[0].issue_key == "ISD-321"
    assert "only 10s elapsed" in result.skipped[0].reason
    mock_create_worklog.assert_not_called()
    mock_save_timers.assert_not_called()


@patch("et.jira_time.tracker.save_timers_with_reload")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers", return_value=[])
@patch("et.jira_time.load_config")
def test_log_all_skips_workspace_with_no_timer(
    mock_load_config, mock_load_timers, mock_create_worklog, mock_save_timers
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )

    result = log_time_for_all_workspaces()

    assert result.logged == []
    assert result.skipped[0].reason == "no Tracker timer for this workspace"
    mock_create_worklog.assert_not_called()
    mock_save_timers.assert_not_called()


@patch("et.jira_time.fetch_issue")
@patch("et.jira_time.tracker.save_timers_with_reload")
@patch("et.jira_time.create_worklog", side_effect=JiraError("boom"))
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.load_config")
def test_log_all_skips_workspace_whose_jira_call_fails_but_logs_the_rest(
    mock_load_config, mock_load_timers, mock_create_worklog, mock_save_timers, mock_fetch_issue
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321"),
            WorkspaceConfigEntry(name="ISD-654", ref="jira:ISD-654"),
        ]
    )
    timers = [_timer(0, 3600), _timer(1, 1800, name="ET-2")]
    mock_load_timers.return_value = timers
    mock_fetch_issue.return_value = _issue("ISD-654")

    def create_worklog_side_effect(_jira_config, key, *_args, **_kwargs):
        if key == "ISD-321":
            raise JiraError("boom")

    mock_create_worklog.side_effect = create_worklog_side_effect

    result = log_time_for_all_workspaces()

    assert [s.issue_key for s in result.skipped] == ["ISD-321"]
    assert result.skipped[0].reason == "boom"
    assert [r.issue_key for r in result.logged] == ["ISD-654"]
    # The failed workspace's timer must be left untouched.
    assert timers[0]["timeElapsed"] == 3600
    mock_save_timers.assert_called_once()


@patch("et.jira_time.load_config")
def test_log_all_raises_when_no_jira_config(mock_load_config):
    mock_load_config.return_value = _config(with_jira=False)

    with pytest.raises(JiraLogTimeError, match="no 'jira' block"):
        log_time_for_all_workspaces()


@patch("et.jira_time.tracker.save_timers_with_reload")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers", return_value=[])
@patch("et.jira_time.load_config")
def test_log_all_ignores_workspaces_with_no_linked_issue(
    mock_load_config, mock_load_timers, mock_create_worklog, mock_save_timers
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="misc"), WorkspaceConfigEntry(name="static", type="static")]
    )

    result = log_time_for_all_workspaces()

    assert result.logged == []
    assert result.skipped == []
    mock_create_worklog.assert_not_called()


@patch("et.jira_time.fetch_issue")
@patch("et.jira_time.tracker.save_timers_with_reload")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.tracker.load_timers")
@patch("et.jira_time.load_config")
def test_log_all_does_not_reset_when_disabled(
    mock_load_config, mock_load_timers, mock_create_worklog, mock_save_timers, mock_fetch_issue
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_load_timers.return_value = [_timer(0, 3600)]
    mock_fetch_issue.return_value = _issue("ISD-321")

    result = log_time_for_all_workspaces(reset=False)

    assert result.logged[0].tracker_reset is False
    mock_save_timers.assert_not_called()
