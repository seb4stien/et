"""Tests for et.jira_time, mocking et.workspaces/et.et_extension/et.jira."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from et.config import EtConfig, JiraConfig, WorkspaceConfigEntry
from et.et_extension import EtExtensionError, WorkspaceCounter
from et.jira import JiraError, JiraIssue
from et.jira_time import (
    JiraLogTimeError,
    log_manual_time_for_current_workspace,
    log_time_for_all_workspaces,
    log_time_for_current_workspace,
    resolve_issue_key,
)
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


def _issue(key: str, *, summary: str = "Some summary") -> JiraIssue:
    return JiraIssue(key=key, summary=summary, priority="Medium", status="In Progress")


@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_logs_elapsed_seconds_and_resets_counter(
    mock_load_config,
    mock_index,
    mock_get_counter,
    mock_create_worklog,
    mock_reset_counter,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=4320, running=True)

    result = log_time_for_current_workspace(description="Fixed it")

    assert result.workspace_index == 0
    assert result.issue_key == "ISD-321"
    assert result.seconds_logged == 4320
    assert result.counter_reset is True

    mock_create_worklog.assert_called_once_with(
        mock_load_config.return_value.jira, "ISD-321", 4320, comment="Fixed it"
    )
    mock_reset_counter.assert_called_once_with(0)


@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_does_not_reset_counter_when_disabled(
    mock_load_config,
    mock_index,
    mock_get_counter,
    mock_create_worklog,
    mock_reset_counter,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=4320, running=True)

    result = log_time_for_current_workspace(reset=False)

    assert result.counter_reset is False
    mock_reset_counter.assert_not_called()


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


@patch(
    "et.jira_time.et_extension.get_workspace_counter",
    side_effect=EtExtensionError("extension not installed"),
)
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_raises_when_counter_cannot_be_read(
    mock_load_config, mock_index, mock_get_counter
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )

    with pytest.raises(JiraLogTimeError, match="extension not installed"):
        log_time_for_current_workspace()


@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_raises_when_elapsed_time_below_minimum(
    mock_load_config, mock_index, mock_get_counter
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=30, running=False)

    with pytest.raises(JiraLogTimeError, match="only 30s elapsed"):
        log_time_for_current_workspace()


@patch("et.jira_time.create_worklog", side_effect=JiraError("boom"))
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_wraps_jira_errors(
    mock_load_config, mock_index, mock_get_counter, mock_create_worklog
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=4320, running=True)

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


@patch(
    "et.jira_time.et_extension.reset_workspace_counter",
    side_effect=EtExtensionError("extension boom"),
)
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_wraps_extension_errors_on_reset(
    mock_load_config,
    mock_index,
    mock_get_counter,
    mock_create_worklog,
    mock_reset_counter,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=4320, running=True)

    with pytest.raises(JiraLogTimeError, match="extension boom"):
        log_time_for_current_workspace()


# --- log_manual_time_for_current_workspace --------------------------------


@patch("et.jira_time.create_worklog")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_manual_time_logs_given_seconds_without_touching_counter(
    mock_load_config, mock_index, mock_create_worklog
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )

    result = log_manual_time_for_current_workspace(7200, description="Manual entry")

    assert result.workspace_index == 0
    assert result.issue_key == "ISD-321"
    assert result.seconds_logged == 7200
    assert result.counter_reset is False

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


@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.workspaces.get_active_workspace_index", return_value=0)
@patch("et.jira_time.load_config")
def test_log_time_uses_explicit_issue_key_override(
    mock_load_config, mock_index, mock_get_counter, mock_create_worklog, mock_reset_counter
):
    # Even with an override, the workspace index is still needed (the
    # extension counter / workspace deletion are tied to the physical
    # workspace, not the ticket), so get_active_workspace_index is still
    # called.
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=4320, running=True)

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
@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.load_config")
def test_log_all_logs_and_resets_every_linked_workspace(
    mock_load_config,
    mock_get_counter,
    mock_create_worklog,
    mock_reset_counter,
    mock_fetch_issue,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321"),
            WorkspaceConfigEntry(name="misc"),
            WorkspaceConfigEntry(name="ISD-654", ref="jira:ISD-654"),
        ]
    )
    counters = {
        0: WorkspaceCounter(elapsed_seconds=3600, running=False),
        2: WorkspaceCounter(elapsed_seconds=1800, running=False),
    }
    mock_get_counter.side_effect = lambda index: counters[index]
    mock_fetch_issue.side_effect = lambda _jira_config, key: _issue(key, summary=f"{key} summary")

    result = log_time_for_all_workspaces(description="Weekly sync")

    assert [r.issue_key for r in result.logged] == ["ISD-321", "ISD-654"]
    assert [r.seconds_logged for r in result.logged] == [3600, 1800]
    assert [r.summary for r in result.logged] == ["ISD-321 summary", "ISD-654 summary"]
    assert all(r.counter_reset for r in result.logged)
    assert result.skipped == []

    assert mock_create_worklog.call_count == 2
    mock_create_worklog.assert_any_call(
        mock_load_config.return_value.jira, "ISD-321", 3600, comment="Weekly sync"
    )
    mock_create_worklog.assert_any_call(
        mock_load_config.return_value.jira, "ISD-654", 1800, comment="Weekly sync"
    )
    # One reset per successfully-logged workspace, not batched.
    assert mock_reset_counter.call_count == 2
    mock_reset_counter.assert_any_call(0)
    mock_reset_counter.assert_any_call(2)


@patch("et.jira_time.fetch_issue", side_effect=JiraError("summary fetch failed"))
@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.load_config")
def test_log_all_falls_back_to_empty_summary_when_fetch_fails(
    mock_load_config,
    mock_get_counter,
    mock_create_worklog,
    mock_reset_counter,
    mock_fetch_issue,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=3600, running=False)

    result = log_time_for_all_workspaces()

    assert result.logged[0].issue_key == "ISD-321"
    assert result.logged[0].summary == ""


@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.load_config")
def test_log_all_skips_workspace_below_minimum_elapsed(
    mock_load_config, mock_get_counter, mock_create_worklog, mock_reset_counter
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=10, running=False)

    result = log_time_for_all_workspaces()

    assert result.logged == []
    assert len(result.skipped) == 1
    assert result.skipped[0].workspace_index == 0
    assert result.skipped[0].issue_key == "ISD-321"
    assert "only 10s elapsed" in result.skipped[0].reason
    mock_create_worklog.assert_not_called()
    mock_reset_counter.assert_not_called()


@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog")
@patch(
    "et.jira_time.et_extension.get_workspace_counter",
    side_effect=EtExtensionError("no counter"),
)
@patch("et.jira_time.load_config")
def test_log_all_skips_workspace_with_no_counter(
    mock_load_config, mock_get_counter, mock_create_worklog, mock_reset_counter
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )

    result = log_time_for_all_workspaces()

    assert result.logged == []
    assert "no prepared counter for this workspace" in result.skipped[0].reason
    mock_create_worklog.assert_not_called()
    mock_reset_counter.assert_not_called()


@patch("et.jira_time.fetch_issue")
@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog", side_effect=JiraError("boom"))
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.load_config")
def test_log_all_skips_workspace_whose_jira_call_fails_but_logs_the_rest(
    mock_load_config,
    mock_get_counter,
    mock_create_worklog,
    mock_reset_counter,
    mock_fetch_issue,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321"),
            WorkspaceConfigEntry(name="ISD-654", ref="jira:ISD-654"),
        ]
    )
    counters = {
        0: WorkspaceCounter(elapsed_seconds=3600, running=False),
        1: WorkspaceCounter(elapsed_seconds=1800, running=False),
    }
    mock_get_counter.side_effect = lambda index: counters[index]
    mock_fetch_issue.return_value = _issue("ISD-654")

    def create_worklog_side_effect(_jira_config, key, *_args, **_kwargs):
        if key == "ISD-321":
            raise JiraError("boom")

    mock_create_worklog.side_effect = create_worklog_side_effect

    result = log_time_for_all_workspaces()

    assert [s.issue_key for s in result.skipped] == ["ISD-321"]
    assert result.skipped[0].reason == "boom"
    assert [r.issue_key for r in result.logged] == ["ISD-654"]
    # The failed workspace's counter must be left untouched.
    mock_reset_counter.assert_called_once_with(1)


@patch("et.jira_time.load_config")
def test_log_all_raises_when_no_jira_config(mock_load_config):
    mock_load_config.return_value = _config(with_jira=False)

    with pytest.raises(JiraLogTimeError, match="no 'jira' block"):
        log_time_for_all_workspaces()


@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.load_config")
def test_log_all_ignores_workspaces_with_no_linked_issue(
    mock_load_config, mock_get_counter, mock_create_worklog, mock_reset_counter
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="misc"), WorkspaceConfigEntry(name="static", type="static")]
    )

    result = log_time_for_all_workspaces()

    assert result.logged == []
    assert result.skipped == []
    mock_create_worklog.assert_not_called()
    mock_get_counter.assert_not_called()


@patch("et.jira_time.fetch_issue")
@patch("et.jira_time.et_extension.reset_workspace_counter")
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.load_config")
def test_log_all_does_not_reset_when_disabled(
    mock_load_config,
    mock_get_counter,
    mock_create_worklog,
    mock_reset_counter,
    mock_fetch_issue,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=3600, running=False)
    mock_fetch_issue.return_value = _issue("ISD-321")

    result = log_time_for_all_workspaces(reset=False)

    assert result.logged[0].counter_reset is False
    mock_reset_counter.assert_not_called()


@patch("et.jira_time.fetch_issue")
@patch(
    "et.jira_time.et_extension.reset_workspace_counter",
    side_effect=EtExtensionError("reset boom"),
)
@patch("et.jira_time.create_worklog")
@patch("et.jira_time.et_extension.get_workspace_counter")
@patch("et.jira_time.load_config")
def test_log_all_raises_when_reset_fails_after_successful_log(
    mock_load_config,
    mock_get_counter,
    mock_create_worklog,
    mock_reset_counter,
    mock_fetch_issue,
):
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-321", ref="jira:ISD-321")]
    )
    mock_get_counter.return_value = WorkspaceCounter(elapsed_seconds=3600, running=False)
    mock_fetch_issue.return_value = _issue("ISD-321")

    with pytest.raises(JiraLogTimeError, match="reset boom"):
        log_time_for_all_workspaces()
