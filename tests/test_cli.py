"""Tests for the `et` CLI, focused on presentation behaviour not covered by unit tests
of the underlying config/tracker/workspace/jira modules."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from et.cli import _hyperlink, app
from et.config import EtConfig, JiraConfig, WorkspaceConfigEntry
from et.jira import JiraIssue
from et.jira_time import JiraLogTimeError, LogTimeResult
from et.task import TaskCompleteResult, TaskCreateResult, TaskError
from et.tracker import TrackerError
from et.workspaces import WorkspaceError

runner = CliRunner()


@pytest.fixture(autouse=True)
def _static_workspaces():
    """Every command runs the root callback's static-workspace check first.

    Default it to "static" (dynamic disabled) so ordinary command tests
    aren't blocked by it; the dedicated static-check tests override this.
    """
    with patch("et.cli.is_dynamic_workspaces_enabled", return_value=False):
        yield


def test_hyperlink_wraps_text_in_osc8_escape_codes_on_a_tty():
    with patch("sys.stdout.isatty", return_value=True):
        assert _hyperlink("PROJ-1", "https://example.atlassian.net/browse/PROJ-1") == (
            "\x1b]8;;https://example.atlassian.net/browse/PROJ-1\x1b\\PROJ-1\x1b]8;;\x1b\\"
        )


def test_hyperlink_returns_plain_text_when_not_a_tty():
    with patch("sys.stdout.isatty", return_value=False):
        assert _hyperlink("PROJ-1", "https://example.atlassian.net/browse/PROJ-1") == "PROJ-1"


def _config(workspaces: list[WorkspaceConfigEntry] | None = None) -> EtConfig:
    return EtConfig(
        jira=JiraConfig(
            base_url="https://example.atlassian.net/",
            email="me@example.com",
            pat="token",
            jql="assignee = currentUser()",
        ),
        workspaces=workspaces or [],
    )


@patch("et.cli.load_timers")
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_shows_jira_info_and_time_spent_for_non_static_workspace(
    mock_load_config, mock_index, mock_load_timers
):
    mock_index.return_value = 1
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="misc"),
            WorkspaceConfigEntry(
                name="Fix login timeout",
                ref="jira:PROJ-1",
                description="Fix login timeout on mobile clients",
            ),
        ]
    )
    mock_load_timers.return_value = []

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Workspace 2: Fix login timeout" in result.stdout
    assert "Fix login timeout on mobile clients" in result.stdout
    assert "jira:PROJ-1" in result.stdout
    assert "https://example.atlassian.net/browse/PROJ-1" not in result.stdout  # only in tty link
    assert "No tracker for this workspace." in result.stdout


@patch("et.cli._hyperlink", side_effect=lambda text, url: f"<{url}|{text}>")
@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_uses_hyperlink_helper_with_jira_browse_url(
    mock_load_config, mock_index, _mock_load_timers, mock_hyperlink
):
    mock_index.return_value = 0
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(
                name="Fix login timeout",
                ref="jira:PROJ-1",
                description="Fix login timeout on mobile clients",
            ),
        ]
    )

    result = runner.invoke(app, [])

    mock_hyperlink.assert_called_once_with(
        "jira:PROJ-1", "https://example.atlassian.net/browse/PROJ-1"
    )
    assert "<https://example.atlassian.net/browse/PROJ-1|jira:PROJ-1>" in result.stdout


@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_reports_no_jira_issue_when_workspace_has_no_ref(
    mock_load_config, mock_index, _mock_load_timers
):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "No Jira issue linked to this workspace." in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_shows_help_when_workspace_is_static(mock_load_config, mock_index):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="mails", type="static")])

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Interact with GNOME/Ubuntu workspaces." in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_shows_help_when_workspace_index_beyond_config_list(
    mock_load_config, mock_index
):
    mock_index.return_value = 5
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Interact with GNOME/Ubuntu workspaces." in result.stdout


@patch("et.cli.get_active_workspace_index")
def test_bare_invocation_shows_help_when_active_workspace_lookup_fails(mock_index):
    from et.workspaces import WorkspaceError

    mock_index.side_effect = WorkspaceError("wmctrl not found")

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Interact with GNOME/Ubuntu workspaces." in result.stdout


@patch("et.cli.load_timers", side_effect=TrackerError("no schema"))
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_bare_invocation_reports_tracker_error(mock_load_config, mock_index, _mock_load_timers):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="misc")])

    result = runner.invoke(app, [])

    assert result.exit_code == 1
    assert "Error: no schema" in result.output


@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_info_command_shows_jira_info_and_time_spent_for_non_static_workspace(
    mock_load_config, mock_index, _mock_load_timers
):
    mock_index.return_value = 0
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(
                name="Fix login timeout",
                ref="jira:PROJ-1",
                description="Fix login timeout on mobile clients",
            ),
        ]
    )

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert "Workspace 1: Fix login timeout" in result.stdout
    assert "jira:PROJ-1" in result.stdout


@patch("et.cli.get_active_workspace_index")
@patch("et.cli.load_config")
def test_info_command_shows_full_app_help_when_workspace_is_static(mock_load_config, mock_index):
    mock_index.return_value = 0
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="mails", type="static")])

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    # Falls back to the top-level app help, not just the `info` subcommand's own usage.
    assert "Interact with GNOME/Ubuntu workspaces." in result.stdout


@patch("et.cli.delete_active_workspace")
def test_ws_delete_reports_summary(mock_delete):
    from et.ws import WsDeleteResult

    mock_delete.return_value = WsDeleteResult(workspace_index=1, remaining_workspaces=4)

    result = runner.invoke(app, ["ws", "delete"])

    assert result.exit_code == 0
    assert "Deleted workspace 2 (now managing 4 workspaces)" in result.stdout


@patch("et.cli.delete_active_workspace")
def test_ws_delete_reports_error(mock_delete):
    from et.ws import WsDeleteError

    mock_delete.side_effect = WsDeleteError("workspace 1 is linked to ISD-1; complete it first")

    result = runner.invoke(app, ["ws", "delete"])

    assert result.exit_code == 1
    assert "Error: workspace 1 is linked to ISD-1; complete it first" in result.output


@patch("et.cli.delete_active_workspace")
def test_ws_delete_force_passes_flag_through(mock_delete):
    from et.ws import WsDeleteResult

    mock_delete.return_value = WsDeleteResult(workspace_index=0, remaining_workspaces=1)

    result = runner.invoke(app, ["ws", "delete", "--force"])

    assert result.exit_code == 0
    mock_delete.assert_called_once_with(force=True)
    assert "Deleted workspace 1 (now managing 1 workspace)" in result.stdout


# --- ws organize --------------------------------------------------------------


def _organize_config():
    return _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )


@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_workspace_count", return_value=1)
@patch("et.cli.load_config")
def test_ws_organize_reports_nothing_to_organize(mock_load_config, _mock_count, _mock_timers):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ISD-A")])

    result = runner.invoke(app, ["ws", "organize"])

    assert result.exit_code == 0
    assert "Nothing to organize" in result.stdout


@patch("et.cli.open_in_editor")
@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_workspace_count", return_value=2)
@patch("et.cli.load_config")
def test_ws_organize_reports_no_changes(mock_load_config, _mock_count, _mock_timers, mock_editor):
    mock_load_config.return_value = _organize_config()
    mock_editor.return_value = "1\n2\n"

    result = runner.invoke(app, ["ws", "organize"])

    assert result.exit_code == 0
    assert "No changes." in result.stdout


@patch("et.cli.apply_organize_plan")
@patch("et.cli.open_in_editor")
@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_workspace_count", return_value=2)
@patch("et.cli.load_config")
def test_ws_organize_shows_summary_and_aborts_on_decline(
    mock_load_config, _mock_count, _mock_timers, mock_editor, mock_apply
):
    mock_load_config.return_value = _organize_config()
    mock_editor.return_value = "2\n1\n"

    result = runner.invoke(app, ["ws", "organize"], input="n\n")

    assert result.exit_code == 0
    assert "Proposed workspace order:" in result.stdout
    assert "ISD-A" in result.stdout
    assert "ISD-B" in result.stdout
    assert "Aborted." in result.stdout
    mock_apply.assert_not_called()


@patch("et.cli.apply_organize_plan")
@patch("et.cli.open_in_editor")
@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_workspace_count", return_value=2)
@patch("et.cli.load_config")
def test_ws_organize_applies_on_confirm(
    mock_load_config, _mock_count, _mock_timers, mock_editor, mock_apply
):
    mock_load_config.return_value = _organize_config()
    mock_editor.return_value = "2\n1\n"

    result = runner.invoke(app, ["ws", "organize"], input="y\n")

    assert result.exit_code == 0
    assert "Reorganized 2 workspaces." in result.stdout
    mock_apply.assert_called_once()
    plan = mock_apply.call_args[0][3]
    by_new_slot = {row.new_slot: row for row in plan}
    assert by_new_slot[0].entry.name == "ISD-B"
    assert by_new_slot[1].entry.name == "ISD-A"


@patch("et.cli.open_in_editor")
@patch("et.cli.load_timers", return_value=[])
@patch("et.cli.get_workspace_count", return_value=2)
@patch("et.cli.load_config")
def test_ws_organize_reports_invalid_editor_result(
    mock_load_config, _mock_count, _mock_timers, mock_editor
):
    mock_load_config.return_value = _organize_config()
    mock_editor.return_value = "1\n1\n"

    result = runner.invoke(app, ["ws", "organize"])

    assert result.exit_code == 1
    assert "Error:" in result.output


# --- static-workspace startup check ------------------------------------------


@patch("et.cli.is_dynamic_workspaces_enabled", return_value=True)
def test_root_exits_when_dynamic_workspaces_enabled(_mock_dynamic):
    result = runner.invoke(app, ["ws", "delete"])

    assert result.exit_code == 1
    assert "dynamic workspaces are enabled" in result.output
    assert "gsettings set org.gnome.mutter dynamic-workspaces false" in result.output


@patch("et.cli.is_dynamic_workspaces_enabled", return_value=False)
def test_root_debug_flag_enables_debug_logging(_mock_dynamic):
    logging.getLogger().setLevel(logging.WARNING)

    runner.invoke(app, ["--debug", "ws", "delete"])

    assert logging.getLogger().isEnabledFor(logging.DEBUG)


@patch("et.cli.is_dynamic_workspaces_enabled", side_effect=WorkspaceError("gsettings missing"))
def test_root_exits_when_static_check_fails(_mock_dynamic):
    result = runner.invoke(app, ["ws", "delete"])

    assert result.exit_code == 1
    assert "Error: gsettings missing" in result.output


# --- et jira -----------------------------------------------------------------


@patch("et.cli.create_task_from_jira")
def test_jira_start_lists_issues_and_creates_from_selection(mock_create_from_jira):
    mock_create_from_jira.return_value = TaskCreateResult(
        workspace_index=2, name="ISD-2", ref="jira:ISD-2", timer_created=True, window_moved=True
    )

    def fake_create_from_jira(select_issue, confirm_transition, confirm_grow):
        del confirm_grow
        del confirm_transition
        issues = [
            JiraIssue(key="ISD-2", summary="Second issue", priority="High", status="In Progress")
        ]
        with patch("sys.stdout.isatty", return_value=False):
            select_issue(issues)
        return mock_create_from_jira.return_value

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="1\n")

    assert result.exit_code == 0
    assert "Active issues not yet linked to a workspace:" in result.stdout
    assert "ISD-2" in result.stdout
    assert "Created workspace 3: 'ISD-2' (linked to ISD-2)" in result.stdout
    assert "Note: could not move this terminal window" not in result.stdout


@patch("et.cli.create_task_from_jira")
def test_jira_start_notes_when_window_could_not_be_moved(mock_create_from_jira):
    mock_create_from_jira.return_value = TaskCreateResult(
        workspace_index=2, name="ISD-2", ref="jira:ISD-2", timer_created=True, window_moved=False
    )

    def fake_create_from_jira(select_issue, confirm_transition, confirm_grow):
        del confirm_grow
        del confirm_transition
        issues = [
            JiraIssue(key="ISD-2", summary="Second issue", priority="High", status="In Progress")
        ]
        with patch("sys.stdout.isatty", return_value=False):
            select_issue(issues)
        return mock_create_from_jira.return_value

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="1\n")

    assert result.exit_code == 0
    assert "Note: could not move this terminal window to the new workspace" in result.stdout


@patch("et.cli.create_task_from_jira")
def test_jira_start_cancelled_returns_none(mock_create_from_jira):
    mock_create_from_jira.return_value = None

    result = runner.invoke(app, ["jira", "start"])

    assert result.exit_code == 0
    assert "Cancelled." in result.stdout


@patch("et.cli.create_task_from_jira")
def test_jira_start_reports_error(mock_create_from_jira):
    mock_create_from_jira.side_effect = TaskError("no 'jira' block found in the config file")

    result = runner.invoke(app, ["jira", "start"])

    assert result.exit_code == 1
    assert "Error: no 'jira' block found" in result.output


@patch("et.cli.create_task_from_jira")
def test_jira_start_prompts_to_move_issue_to_in_progress_when_confirmed(mock_create_from_jira):
    captured = {}

    def fake_create_from_jira(select_issue, confirm_transition, confirm_grow):
        del confirm_grow
        del select_issue
        issue = JiraIssue(key="ISD-2", summary="Second issue", priority="High", status="To Do")
        captured["confirmed"] = confirm_transition(issue)
        return None

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="y\n")

    assert result.exit_code == 0
    assert "ISD-2 is currently 'To Do'. Move it to 'In Progress'?" in result.stdout
    assert captured["confirmed"] is True


@patch("et.cli.create_task_from_jira")
def test_jira_start_does_not_transition_when_declined(mock_create_from_jira):
    captured = {}

    def fake_create_from_jira(select_issue, confirm_transition, confirm_grow):
        del confirm_grow
        del select_issue
        issue = JiraIssue(key="ISD-2", summary="Second issue", priority="High", status="To Do")
        captured["confirmed"] = confirm_transition(issue)
        return None

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="n\n")

    assert result.exit_code == 0
    assert captured["confirmed"] is False


@patch("et.cli.create_task_from_jira")
def test_jira_start_defaults_transition_prompt_to_yes(mock_create_from_jira):
    captured = {}

    def fake_create_from_jira(select_issue, confirm_transition, confirm_grow):
        del confirm_grow
        del select_issue
        issue = JiraIssue(key="ISD-2", summary="Second issue", priority="High", status="To Do")
        captured["confirmed"] = confirm_transition(issue)
        return None

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="\n")

    assert result.exit_code == 0
    assert "[Y/n]" in result.stdout
    assert captured["confirmed"] is True


@patch("et.cli.create_task_from_jira")
def test_jira_start_defaults_grow_prompt_to_yes(mock_create_from_jira):
    captured = {}

    def fake_create_from_jira(select_issue, confirm_transition, confirm_grow):
        del confirm_transition
        del select_issue
        captured["confirmed"] = confirm_grow(4)
        return None

    mock_create_from_jira.side_effect = fake_create_from_jira

    result = runner.invoke(app, ["jira", "start"], input="\n")

    assert result.exit_code == 0
    assert "All 4 workspaces are in use. Add another workspace? [Y/n]" in result.stdout
    assert captured["confirmed"] is True


@patch("et.cli._hyperlink", side_effect=lambda text, url: f"<{url}|{text}>")
@patch("et.cli.load_config")
@patch("et.cli.log_time_for_current_workspace")
def test_jira_log_time_logs_and_reports_duration(mock_log_time, mock_load_config, mock_hyperlink):
    mock_log_time.return_value = LogTimeResult(
        workspace_index=1, issue_key="ISD-321", seconds_logged=4320, tracker_reset=True
    )
    mock_load_config.return_value = _config([])

    result = runner.invoke(app, ["jira", "log-time", "--comment", "note"])

    assert result.exit_code == 0
    mock_hyperlink.assert_called_once_with(
        "jira:ISD-321", "https://example.atlassian.net/browse/ISD-321"
    )
    assert "Logged 1h 12m 0s to <https://example.atlassian.net/browse/ISD-321|jira:ISD-321>" in (
        result.stdout
    )
    mock_log_time.assert_called_once_with(description="note", reset=True, issue_key=None)


@patch("et.cli.load_config")
@patch("et.cli.log_manual_time_for_current_workspace")
def test_jira_log_time_with_hours_argument_logs_manual_duration(
    mock_log_manual, mock_load_config
):
    mock_log_manual.return_value = LogTimeResult(
        workspace_index=1, issue_key="ISD-321", seconds_logged=7200, tracker_reset=False
    )
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "log-time", "2h", "--comment", "manual"])

    assert result.exit_code == 0
    assert "Logged 2h 0m 0s to jira:ISD-321 (workspace 2)" in result.stdout
    assert "Reset tracker to 0" not in result.stdout
    mock_log_manual.assert_called_once_with(7200, description="manual", issue_key=None)


def test_jira_log_time_with_invalid_hours_argument_reports_error():
    result = runner.invoke(app, ["jira", "log-time", "2m"])

    assert result.exit_code == 1
    assert "Error: invalid duration" in result.output


@patch("et.cli.load_config")
@patch("et.cli.log_manual_time_for_current_workspace")
def test_jira_log_time_forwards_jira_option(mock_log_manual, mock_load_config):
    mock_log_manual.return_value = LogTimeResult(
        workspace_index=1, issue_key="ISD-999", seconds_logged=7200, tracker_reset=False
    )
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "log-time", "2h", "--jira", "ISD-999"])

    assert result.exit_code == 0
    mock_log_manual.assert_called_once_with(7200, description=None, issue_key="ISD-999")


def test_jira_log_time_rejects_no_reset_combined_with_manual_hours():
    result = runner.invoke(app, ["jira", "log-time", "2h", "--no-reset"])

    assert result.exit_code == 1
    assert "--no-reset only applies" in result.output


@patch("et.cli.complete_task_for_current_workspace")
def test_jira_complete_logs_time_and_frees_workspace(mock_complete):
    def fake_complete(comment, issue_key, on_logged, confirm_delete, confirm_done):
        log_result = LogTimeResult(
            workspace_index=1, issue_key="ISD-321", seconds_logged=780, tracker_reset=True
        )
        on_logged(log_result)
        freed = confirm_delete(log_result)
        done = confirm_done(log_result)
        return TaskCompleteResult(
            log_result=log_result, workspace_freed=freed, moved_to_done=done
        )

    mock_complete.side_effect = fake_complete

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(
            app, ["jira", "complete", "--comment", "wrapping up"], input="y\ny\n"
        )

    assert result.exit_code == 0
    assert mock_complete.call_args.kwargs["comment"] == "wrapping up"
    assert "Logged 0h 13m 0s to jira:ISD-321 (workspace 2)" in result.stdout
    assert "Deleted workspace 2" in result.stdout
    assert "Moved ISD-321 to 'Done'" in result.stdout


@patch("et.cli._hyperlink", side_effect=lambda text, url: f"<{url}|{text}>")
@patch("et.cli.load_config")
@patch("et.cli.complete_task_for_current_workspace")
def test_jira_complete_uses_hyperlink_helper_for_issue_key(
    mock_complete, mock_load_config, mock_hyperlink
):
    mock_load_config.return_value = _config([])

    def fake_complete(comment, issue_key, on_logged, confirm_delete, confirm_done):
        log_result = LogTimeResult(
            workspace_index=1, issue_key="ISD-321", seconds_logged=780, tracker_reset=True
        )
        on_logged(log_result)
        freed = confirm_delete(log_result)
        done = confirm_done(log_result)
        return TaskCompleteResult(
            log_result=log_result, workspace_freed=freed, moved_to_done=done
        )

    mock_complete.side_effect = fake_complete

    result = runner.invoke(app, ["jira", "complete"], input="y\ny\n")

    assert result.exit_code == 0
    assert "<https://example.atlassian.net/browse/ISD-321|jira:ISD-321>" in result.stdout
    assert "Move <https://example.atlassian.net/browse/ISD-321|ISD-321> to 'Done'?" in (
        result.stdout
    )
    assert "Moved <https://example.atlassian.net/browse/ISD-321|ISD-321> to 'Done'" in (
        result.stdout
    )


@patch("et.cli.complete_task_for_current_workspace")
def test_jira_complete_skips_cleanup_when_declined(mock_complete):
    def fake_complete(comment, issue_key, on_logged, confirm_delete, confirm_done):
        log_result = LogTimeResult(
            workspace_index=1, issue_key="ISD-321", seconds_logged=780, tracker_reset=True
        )
        on_logged(log_result)
        freed = confirm_delete(log_result)
        done = confirm_done(log_result)
        return TaskCompleteResult(
            log_result=log_result, workspace_freed=freed, moved_to_done=done
        )

    mock_complete.side_effect = fake_complete

    result = runner.invoke(app, ["jira", "complete"], input="n\nn\n")

    assert result.exit_code == 0
    assert "Logged 0h 13m 0s to jira:ISD-321 (workspace 2)" in result.stdout
    assert "Deleted workspace 2" not in result.stdout
    assert "Moved ISD-321 to 'Done'" not in result.stdout


@patch("et.cli.complete_task_for_current_workspace")
def test_jira_complete_reports_error(mock_complete):
    mock_complete.side_effect = JiraLogTimeError("no Jira issue linked to workspace 1")

    result = runner.invoke(app, ["jira", "complete"])

    assert result.exit_code == 1
    assert "Error: no Jira issue linked to workspace 1" in result.output


@patch("et.cli.load_config")
@patch("et.cli.add_comment_to_current_workspace")
def test_jira_comment_adds_comment_from_argument(mock_add_comment, mock_load_config):
    mock_add_comment.return_value = "ISD-321"
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "comment", "Looks good"])

    assert result.exit_code == 0
    assert "Added comment to ISD-321" in result.stdout
    mock_add_comment.assert_called_once_with("Looks good", issue_key=None)


@patch("et.cli.load_config")
@patch("et.cli.add_comment_to_current_workspace")
def test_jira_comment_prompts_when_message_not_given(mock_add_comment, mock_load_config):
    mock_add_comment.return_value = "ISD-321"
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "comment"], input="Prompted comment\n")

    assert result.exit_code == 0
    mock_add_comment.assert_called_once_with("Prompted comment", issue_key=None)


@patch("et.cli.load_config")
@patch("et.cli.add_comment_to_current_workspace")
def test_jira_comment_forwards_explicit_key(mock_add_comment, mock_load_config):
    mock_add_comment.return_value = "ISD-99"
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "comment", "hi", "--jira", "ISD-99"])

    assert result.exit_code == 0
    mock_add_comment.assert_called_once_with("hi", issue_key="ISD-99")


@patch("et.cli.add_comment_to_current_workspace")
def test_jira_comment_reports_error(mock_add_comment):
    mock_add_comment.side_effect = JiraLogTimeError("no Jira issue linked to workspace 1")

    result = runner.invoke(app, ["jira", "comment", "hi"])

    assert result.exit_code == 1
    assert "Error: no Jira issue linked to workspace 1" in result.output


@patch("et.cli.load_config")
@patch("et.cli.set_status_for_current_workspace")
def test_jira_status_with_in_progress_argument_transitions_immediately(
    mock_set_status, mock_load_config
):
    mock_set_status.return_value = "ISD-321"
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "status", "in-progress"])

    assert result.exit_code == 0
    assert "Moved ISD-321 to 'In Progress'" in result.stdout
    mock_set_status.assert_called_once_with("in progress", issue_key=None)


@patch("et.cli.load_config")
@patch("et.cli.set_status_for_current_workspace")
def test_jira_status_with_blocked_argument_transitions_immediately(
    mock_set_status, mock_load_config
):
    mock_set_status.return_value = "ISD-321"
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "status", "blocked"])

    assert result.exit_code == 0
    assert "Moved ISD-321 to 'Blocked'" in result.stdout
    mock_set_status.assert_called_once_with("blocked", issue_key=None)


@patch("et.cli.load_config")
@patch("et.cli.set_status_for_current_workspace")
def test_jira_status_forwards_jira_option(mock_set_status, mock_load_config):
    mock_set_status.return_value = "ISD-999"
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "status", "blocked", "--jira", "ISD-999"])

    assert result.exit_code == 0
    assert "Moved ISD-999 to 'Blocked'" in result.stdout
    mock_set_status.assert_called_once_with("blocked", issue_key="ISD-999")


def test_jira_status_rejects_unknown_argument():
    result = runner.invoke(app, ["jira", "status", "done"])

    assert result.exit_code == 1
    assert "status must be one of" in result.output


@patch("et.cli.load_config")
@patch("et.cli.set_status_for_current_workspace")
@patch("et.cli.get_current_status_for_current_workspace")
def test_jira_status_with_no_argument_shows_numbered_list_and_transitions_on_choice(
    mock_get_status, mock_set_status, mock_load_config
):
    mock_get_status.return_value = ("ISD-321", "In Progress")
    mock_set_status.return_value = "ISD-321"
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "status"], input="4\n")

    assert result.exit_code == 0
    assert "ISD-321 is currently: In Progress" in result.stdout
    assert "1. Untriaged" in result.stdout
    assert "4. Blocked" in result.stdout
    assert "Moved ISD-321 to 'Blocked'" in result.stdout
    mock_set_status.assert_called_once_with("Blocked", issue_key=None)


@patch("et.cli.load_config")
@patch("et.cli.set_status_for_current_workspace")
@patch("et.cli.get_current_status_for_current_workspace")
def test_jira_status_with_no_argument_cancels_on_blank_input(
    mock_get_status, mock_set_status, mock_load_config
):
    mock_get_status.return_value = ("ISD-321", "In Progress")
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "status"], input="\n")

    assert result.exit_code == 0
    assert "Cancelled." in result.stdout
    mock_set_status.assert_not_called()


@patch("et.cli.load_config")
@patch("et.cli.get_current_status_for_current_workspace")
def test_jira_status_with_no_argument_rejects_invalid_choice(mock_get_status, mock_load_config):
    mock_get_status.return_value = ("ISD-321", "In Progress")
    mock_load_config.return_value = _config([])

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "status"], input="99\n")

    assert result.exit_code == 1
    assert "Error: invalid choice" in result.output


@patch("et.cli.get_current_status_for_current_workspace")
def test_jira_status_reports_error(mock_get_status):
    mock_get_status.side_effect = JiraLogTimeError("no Jira issue linked to workspace 1")

    result = runner.invoke(app, ["jira", "status"])

    assert result.exit_code == 1
    assert "Error: no Jira issue linked to workspace 1" in result.output



@patch("et.cli.create_issue_interactive")
def test_jira_create_reports_created_issue(mock_create):
    from et.jira_create import JiraCreateResult

    mock_create.return_value = JiraCreateResult(
        key="PROJ-42", url="https://example.atlassian.net/browse/PROJ-42"
    )

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "create"])

    assert result.exit_code == 0
    assert "Created PROJ-42" in result.stdout
    args, _kwargs = mock_create.call_args
    assert args[1] is None


@patch("et.cli.create_issue_interactive")
def test_jira_create_forwards_github_url(mock_create):
    from et.jira_create import JiraCreateResult

    mock_create.return_value = JiraCreateResult(
        key="PROJ-43", url="https://example.atlassian.net/browse/PROJ-43"
    )
    github_url = "https://github.com/canonical/wazuh-server-operator/issues/263"

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "create", github_url])

    assert result.exit_code == 0
    args, _kwargs = mock_create.call_args
    assert args[1] == github_url


@patch("et.cli.create_issue_interactive")
def test_jira_create_reports_error(mock_create):
    from et.jira_create import JiraCreateError

    mock_create.side_effect = JiraCreateError("no 'jira.project_key' set in the config file")

    result = runner.invoke(app, ["jira", "create"])

    assert result.exit_code == 1
    assert "Error: no 'jira.project_key' set" in result.output


@patch("et.cli.create_issue_interactive")
def test_jira_create_prompt_type_defaults_to_story(mock_create):
    from et.jira_create import JiraCreateResult

    def fake_create(prompts, github_url):
        del github_url
        assert prompts.prompt_type("Story") == "Story"
        return JiraCreateResult(key="PROJ-44", url="https://example.atlassian.net/browse/PROJ-44")

    mock_create.side_effect = fake_create

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["jira", "create"], input="\n")

    assert result.exit_code == 0


def test_prompt_component_lists_components_and_returns_none_for_zero():
    from et.cli import _prompt_component
    from et.jira import JiraComponent

    components = [JiraComponent(id="1", name="Backend"), JiraComponent(id="2", name="Frontend")]

    with patch("et.cli.typer.prompt", return_value="0"):
        assert _prompt_component(components) is None


def test_prompt_component_returns_selected_component():
    from et.cli import _prompt_component
    from et.jira import JiraComponent

    components = [JiraComponent(id="1", name="Backend"), JiraComponent(id="2", name="Frontend")]

    with patch("et.cli.typer.prompt", return_value="2"):
        assert _prompt_component(components) == components[1]


def test_prompt_from_list_shows_numbered_options_and_marks_default():
    from et.cli import _prompt_from_list

    with patch("et.cli.typer.prompt", return_value="1") as mock_prompt:
        result = _prompt_from_list("Priority", ("Highest", "High", "Medium"), "High")

    assert result == "Highest"
    mock_prompt.assert_called_once_with("Pick a number", default="2")


def test_prompt_from_list_falls_back_to_default_on_invalid_input():
    from et.cli import _prompt_from_list

    with patch("et.cli.typer.prompt", return_value="99"):
        result = _prompt_from_list("Priority", ("Highest", "High", "Medium"), "High")

    assert result == "High"


def test_prompt_component_shows_three_columns(capsys):
    from et.cli import _prompt_component
    from et.jira import JiraComponent

    components = [
        JiraComponent(id="1", name="Backend"),
        JiraComponent(id="2", name="Frontend"),
        JiraComponent(id="3", name="Docs"),
        JiraComponent(id="4", name="Infra"),
    ]

    with patch("et.cli.typer.prompt", return_value="0"):
        _prompt_component(components)

    lines = capsys.readouterr().out.splitlines()
    # "0. (none)", "1. Backend", "2. Frontend" all fit on the first row (3 columns).
    first_row = next(line for line in lines if "0. (none)" in line)
    assert "1. Backend" in first_row
    assert "2. Frontend" in first_row


@patch("et.cli.create_issue_interactive")
def test_jira_create_returns_cancelled_when_confirm_declined(mock_create):
    def fake_create(prompts, github_url):
        del github_url
        assert prompts.confirm_create([("Summary", "Fix the bug")]) is False
        return None

    with patch("et.cli.typer.confirm", return_value=False):
        mock_create.side_effect = fake_create
        result = runner.invoke(app, ["jira", "create"])

    assert result.exit_code == 0
    assert "Cancelled." in result.stdout


@patch("et.cli.create_issue_interactive")
def test_jira_create_confirm_prints_summary_fields(mock_create):
    from et.jira_create import JiraCreateResult

    def fake_create(prompts, github_url):
        del github_url
        prompts.confirm_create([("Summary", "Fix the bug"), ("Priority", "Medium")])
        return JiraCreateResult(key="PROJ-50", url="https://example.atlassian.net/browse/PROJ-50")

    mock_create.side_effect = fake_create

    with patch("et.cli.typer.confirm", return_value=True):
        result = runner.invoke(app, ["jira", "create"])

    assert result.exit_code == 0
    assert "About to create this issue:" in result.stdout
    assert "Summary: Fix the bug" in result.stdout
    assert "Priority: Medium" in result.stdout


@patch("et.cli.create_issue_interactive")
def test_jira_create_reports_no_board_configured_error(mock_create):
    from et.jira_create import JiraCreateError

    mock_create.side_effect = JiraCreateError(
        "no Jira Agile board configured or discoverable for project 'PROJ'"
    )

    result = runner.invoke(app, ["jira", "create"])

    assert result.exit_code == 1
    assert "Error: no Jira Agile board configured" in result.output


# --- git create-branch -------------------------------------------------------


@patch("et.cli.create_branch_interactive")
@patch("et.cli.resolve_issue_key")
@patch("et.cli.load_config")
def test_git_create_branch_creates_and_switches(mock_load_config, mock_resolve, mock_create):
    from et.git_branch import BranchCreateResult

    config = _config([])
    mock_load_config.return_value = config
    mock_resolve.return_value = (config.jira, "ISD-1234")
    mock_create.return_value = BranchCreateResult(
        name="feat/some-feature-isd-1234", issue_key="ISD-1234"
    )

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["git", "create-branch"])

    assert result.exit_code == 0
    assert "Created and switched to branch 'feat/some-feature-isd-1234'" in result.stdout
    mock_resolve.assert_called_once_with(config, issue_key=None)
    args, kwargs = mock_create.call_args
    assert args[0] == config.jira
    assert args[1] == "ISD-1234"
    assert callable(kwargs["select_type"])
    assert callable(kwargs["edit_description"])
    assert callable(kwargs["announce_issue"])


@patch("et.cli.create_branch_interactive")
@patch("et.cli.resolve_issue_key")
@patch("et.cli.load_config")
def test_git_cb_is_an_alias_for_create_branch(mock_load_config, mock_resolve, mock_create):
    from et.git_branch import BranchCreateResult

    config = _config([])
    mock_load_config.return_value = config
    mock_resolve.return_value = (config.jira, "ISD-1234")
    mock_create.return_value = BranchCreateResult(
        name="feat/some-feature-isd-1234", issue_key="ISD-1234"
    )

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["git", "cb"])

    assert result.exit_code == 0
    assert "Created and switched to branch 'feat/some-feature-isd-1234'" in result.stdout


@patch("et.cli.create_branch_interactive")
@patch("et.cli.resolve_issue_key")
@patch("et.cli.load_config")
def test_git_create_branch_announces_issue_link_and_summary_first(
    mock_load_config, mock_resolve, mock_create
):
    from et.git_branch import BranchCreateResult, JiraIssueBasis

    config = _config([])
    mock_load_config.return_value = config
    mock_resolve.return_value = (config.jira, "ISD-1234")

    def fake_create(jira_config, issue_key, *, select_type, edit_description, announce_issue):
        announce_issue(
            JiraIssueBasis(summary="Add wildcard SNI support", issue_type="Story", labels=())
        )
        return BranchCreateResult(name="feat/some-feature-isd-1234", issue_key="ISD-1234")

    mock_create.side_effect = fake_create

    with patch("sys.stdout.isatty", return_value=False):
        result = runner.invoke(app, ["git", "create-branch"])

    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert lines[0] == "ISD-1234: Add wildcard SNI support"
    assert "Created and switched to branch" in lines[-1]


@patch("et.cli.create_branch_interactive")
@patch("et.cli.resolve_issue_key")
@patch("et.cli.load_config")
def test_git_create_branch_forwards_explicit_jira_key(mock_load_config, mock_resolve, mock_create):
    from et.git_branch import BranchCreateResult

    config = _config([])
    mock_load_config.return_value = config
    mock_resolve.return_value = (config.jira, "ISD-99")
    mock_create.return_value = BranchCreateResult(name="fix/a-bug-isd-99", issue_key="ISD-99")

    result = runner.invoke(app, ["git", "create-branch", "--jira", "ISD-99"])

    assert result.exit_code == 0
    mock_resolve.assert_called_once_with(config, issue_key="ISD-99")


@patch("et.cli.create_branch_interactive", return_value=None)
@patch("et.cli.resolve_issue_key")
@patch("et.cli.load_config")
def test_git_create_branch_prints_cancelled_when_result_none(
    mock_load_config, mock_resolve, mock_create
):
    config = _config([])
    mock_load_config.return_value = config
    mock_resolve.return_value = (config.jira, "ISD-1")

    result = runner.invoke(app, ["git", "create-branch"])

    assert result.exit_code == 0
    assert "Cancelled." in result.stdout


@patch("et.cli.create_branch_interactive")
@patch("et.cli.resolve_issue_key")
@patch("et.cli.load_config")
def test_git_create_branch_reports_not_a_git_repo_error(
    mock_load_config, mock_resolve, mock_create
):
    from et.git_branch import GitBranchError

    config = _config([])
    mock_load_config.return_value = config
    mock_resolve.return_value = (config.jira, "ISD-1")
    mock_create.side_effect = GitBranchError("not inside a git repository")

    result = runner.invoke(app, ["git", "create-branch"])

    assert result.exit_code == 1
    assert "Error: not inside a git repository" in result.output


@patch("et.cli.create_branch_interactive")
@patch("et.cli.resolve_issue_key")
@patch("et.cli.load_config")
def test_git_create_branch_reports_branch_already_exists_error(
    mock_load_config, mock_resolve, mock_create
):
    from et.git_branch import GitBranchError

    config = _config([])
    mock_load_config.return_value = config
    mock_resolve.return_value = (config.jira, "ISD-1")
    mock_create.side_effect = GitBranchError("branch 'feat/x-isd-1' already exists")

    result = runner.invoke(app, ["git", "create-branch"])

    assert result.exit_code == 1
    assert "Error: branch 'feat/x-isd-1' already exists" in result.output


@patch("et.cli.resolve_issue_key")
@patch("et.cli.load_config")
def test_git_create_branch_reports_no_active_workspace_error(mock_load_config, mock_resolve):
    mock_load_config.return_value = _config([])
    mock_resolve.side_effect = JiraLogTimeError("no Jira issue linked to workspace 1")

    result = runner.invoke(app, ["git", "create-branch"])

    assert result.exit_code == 1
    assert "Error: no Jira issue linked to workspace 1" in result.output


def test_select_branch_type_accepts_default():
    from et.cli import _select_branch_type

    with patch("et.cli.typer.prompt", return_value="feat"):
        assert _select_branch_type("feat") == "feat"


def test_select_branch_type_accepts_override():
    from et.cli import _select_branch_type

    with patch("et.cli.typer.prompt", return_value="chore"):
        assert _select_branch_type("feat") == "chore"


def test_select_branch_type_rejects_invalid_choice():
    from et.cli import _select_branch_type

    with patch("et.cli.typer.prompt", return_value="bogus"):
        with pytest.raises(typer.Exit):
            _select_branch_type("feat")


def test_edit_branch_description_returns_stripped_input():
    from et.cli import _edit_branch_description

    with patch("et.cli.typer.prompt", return_value="  custom slug  "):
        assert _edit_branch_description("default-slug") == "custom slug"
