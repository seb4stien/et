"""Tests for et.config_wizard, mocking et.jira.check_credentials."""

from __future__ import annotations

from unittest.mock import patch

from et.config import EtConfig, JiraConfig, WorkspaceConfigEntry
from et.config_wizard import ConfigWizardPrompts, run_config_wizard
from et.jira import JiraError


def _jira_config(**overrides: object) -> JiraConfig:
    defaults: dict[str, object] = dict(
        base_url="https://example.atlassian.net",
        email="me@example.com",
        pat="secret-token",
        jql="assignee = currentUser()",
        project_key="PROJ",
        board_id=None,
    )
    defaults.update(overrides)
    return JiraConfig(**defaults)  # type: ignore[arg-type]


def _prompts(**overrides: object) -> ConfigWizardPrompts:
    defaults: dict[str, object] = dict(
        confirm_configure_jira=lambda has_existing: True,
        prompt_base_url=lambda default: default or "https://example.atlassian.net",
        prompt_email=lambda default: default or "me@example.com",
        prompt_pat=lambda default: default or "secret-token",
        prompt_jql=lambda default: default or "assignee = currentUser()",
        prompt_project_key=lambda default: default,
        prompt_board_id=lambda default: default,
        resolve_credential_check_failure=lambda message: "keep",
        prompt_workspace_action=lambda entries: "done",
        select_workspace_entry=lambda entries, purpose: None,
        prompt_workspace_entry=lambda default: None,
        confirm_save=lambda config: True,
        warn=lambda message: None,
    )
    defaults.update(overrides)
    return ConfigWizardPrompts(**defaults)  # type: ignore[arg-type]


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_builds_fresh_jira_config(mock_check):
    result = run_config_wizard(_prompts(), existing=None)

    assert result is not None
    assert result.jira is not None
    assert result.jira.base_url == "https://example.atlassian.net"
    assert result.jira.email == "me@example.com"
    assert result.jira.pat == "secret-token"
    assert result.jira.jql == "assignee = currentUser()"
    assert result.workspaces == []
    mock_check.assert_called_once()


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_declines_jira_block(mock_check):
    result = run_config_wizard(
        _prompts(confirm_configure_jira=lambda has_existing: False), existing=None
    )

    assert result is not None
    assert result.jira is None
    mock_check.assert_not_called()


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_prefills_from_existing_jira_config(mock_check):
    existing = EtConfig(jira=_jira_config(project_key="OLD"), workspaces=[])
    seen_defaults: dict[str, str] = {}

    def prompt_project_key(default: str) -> str:
        seen_defaults["project_key"] = default
        return default

    result = run_config_wizard(_prompts(prompt_project_key=prompt_project_key), existing=existing)

    assert seen_defaults["project_key"] == "OLD"
    assert result is not None
    assert result.jira is not None
    assert result.jira.project_key == "OLD"


@patch("et.config_wizard.check_credentials", side_effect=JiraError("bad credentials"))
def test_run_config_wizard_keeps_jira_config_on_credential_failure_when_told_to_keep(mock_check):
    result = run_config_wizard(
        _prompts(resolve_credential_check_failure=lambda message: "keep"), existing=None
    )

    assert result is not None
    assert result.jira is not None
    assert result.jira.base_url == "https://example.atlassian.net"


@patch("et.config_wizard.check_credentials", side_effect=JiraError("bad credentials"))
def test_run_config_wizard_discards_jira_config_on_credential_failure(mock_check):
    result = run_config_wizard(
        _prompts(resolve_credential_check_failure=lambda message: "discard"), existing=None
    )

    assert result is not None
    assert result.jira is None


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_retries_credentials_until_success(mock_check):
    mock_check.side_effect = [JiraError("bad credentials"), None]
    attempts = {"count": 0}

    def resolve_credential_check_failure(message: str) -> str:
        attempts["count"] += 1
        return "retry"

    result = run_config_wizard(
        _prompts(resolve_credential_check_failure=resolve_credential_check_failure),
        existing=None,
    )

    assert attempts["count"] == 1
    assert mock_check.call_count == 2
    assert result is not None
    assert result.jira is not None


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_adds_a_workspace_entry(mock_check):
    actions = iter(["add", "done"])
    new_entry = WorkspaceConfigEntry(name="mails", type="static")

    result = run_config_wizard(
        _prompts(
            confirm_configure_jira=lambda has_existing: False,
            prompt_workspace_action=lambda entries: next(actions),
            prompt_workspace_entry=lambda default: new_entry if default is None else None,
        ),
        existing=None,
    )

    assert result is not None
    assert result.workspaces == [new_entry]
    mock_check.assert_not_called()


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_edits_a_workspace_entry(mock_check):
    existing_entry = WorkspaceConfigEntry(name="mails", type="dynamic")
    existing = EtConfig(jira=None, workspaces=[existing_entry])
    edited_entry = WorkspaceConfigEntry(name="mails", type="static")
    actions = iter(["edit", "done"])

    result = run_config_wizard(
        _prompts(
            confirm_configure_jira=lambda has_existing: False,
            prompt_workspace_action=lambda entries: next(actions),
            select_workspace_entry=lambda entries, purpose: 0,
            prompt_workspace_entry=lambda default: edited_entry,
        ),
        existing=existing,
    )

    assert result is not None
    assert result.workspaces == [edited_entry]
    mock_check.assert_not_called()


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_removes_a_workspace_entry(mock_check):
    existing_entry = WorkspaceConfigEntry(name="mails", type="dynamic")
    existing = EtConfig(jira=None, workspaces=[existing_entry])
    actions = iter(["remove", "done"])

    result = run_config_wizard(
        _prompts(
            confirm_configure_jira=lambda has_existing: False,
            prompt_workspace_action=lambda entries: next(actions),
            select_workspace_entry=lambda entries, purpose: 0,
        ),
        existing=existing,
    )

    assert result is not None
    assert result.workspaces == []


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_ignores_declined_add(mock_check):
    actions = iter(["add", "done"])

    result = run_config_wizard(
        _prompts(
            confirm_configure_jira=lambda has_existing: False,
            prompt_workspace_action=lambda entries: next(actions),
            prompt_workspace_entry=lambda default: None,
        ),
        existing=None,
    )

    assert result is not None
    assert result.workspaces == []


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_ignores_cancelled_edit_selection(mock_check):
    existing_entry = WorkspaceConfigEntry(name="mails", type="dynamic")
    existing = EtConfig(jira=None, workspaces=[existing_entry])
    actions = iter(["edit", "done"])

    result = run_config_wizard(
        _prompts(
            confirm_configure_jira=lambda has_existing: False,
            prompt_workspace_action=lambda entries: next(actions),
            select_workspace_entry=lambda entries, purpose: None,
        ),
        existing=existing,
    )

    assert result is not None
    assert result.workspaces == [existing_entry]


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_treats_unrecognized_action_as_done(mock_check):
    result = run_config_wizard(
        _prompts(
            confirm_configure_jira=lambda has_existing: False,
            prompt_workspace_action=lambda entries: "bogus",
        ),
        existing=None,
    )

    assert result is not None
    assert result.workspaces == []


@patch("et.config_wizard.check_credentials")
def test_run_config_wizard_returns_none_when_save_declined(mock_check):
    result = run_config_wizard(
        _prompts(
            confirm_configure_jira=lambda has_existing: False,
            confirm_save=lambda config: False,
        ),
        existing=None,
    )

    assert result is None
