"""Tests for et.jira_create, mocking et.jira/et.github_ref calls."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from et.config import EtConfig, JiraConfig, save_config
from et.github_ref import GithubRefDetails, GithubRefError
from et.jira import JiraBoardWithoutSprintsError, JiraComponent, JiraError, JiraSprint
from et.jira_create import IssueDraftPrompts, JiraCreateError, create_issue_interactive


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ET_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _jira_config(**overrides: object) -> JiraConfig:
    defaults: dict[str, object] = dict(
        base_url="https://example.atlassian.net",
        email="me@example.com",
        pat="secret-token",
        jql="assignee = currentUser()",
        project_key="PROJ",
    )
    defaults.update(overrides)
    return JiraConfig(**defaults)  # type: ignore[arg-type]


def _write_config(config_dir, jira: JiraConfig | None) -> None:
    save_config(EtConfig(jira=jira, workspaces=[]))


def _prompts(**overrides: object) -> IssueDraftPrompts:
    defaults: dict[str, object] = dict(
        prompt_type=lambda default: default,
        prompt_summary=lambda default: default or "A summary",
        confirm_assign_self=lambda: False,
        prompt_priority=lambda default: default,
        select_component=lambda components: None,
        confirm_sprint=lambda: False,
        prompt_estimate_hours=lambda: "",
        prompt_description=lambda default: default,
        confirm_create=lambda fields: True,
        warn=lambda message: None,
    )
    defaults.update(overrides)
    return IssueDraftPrompts(**defaults)  # type: ignore[arg-type]


def test_create_issue_interactive_raises_without_jira_block(config_dir):
    _write_config(config_dir, None)

    with pytest.raises(JiraCreateError, match="no 'jira' block"):
        create_issue_interactive(_prompts())


def test_create_issue_interactive_raises_without_project_key(config_dir):
    _write_config(config_dir, _jira_config(project_key=None))

    with pytest.raises(JiraCreateError, match="project_key"):
        create_issue_interactive(_prompts())


@patch("et.jira_create.create_issue")
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_builds_minimal_fields(mock_components, mock_create, config_dir):
    _write_config(config_dir, _jira_config())
    mock_create.return_value = "PROJ-1"

    result = create_issue_interactive(_prompts(prompt_summary=lambda default: "Fix the bug"))

    assert result.key == "PROJ-1"
    assert result.url == "https://example.atlassian.net/browse/PROJ-1"
    _config_arg, fields = mock_create.call_args.args
    assert fields["project"] == {"key": "PROJ"}
    assert fields["issuetype"] == {"name": "Story"}
    assert fields["summary"] == "Fix the bug"
    assert fields["priority"] == {"name": "Medium"}
    assert "assignee" not in fields
    assert "components" not in fields
    assert "timetracking" not in fields
    assert "description" not in fields


@patch("et.jira_create.create_issue", return_value="PROJ-2")
@patch("et.jira_create.search_user_account_id", return_value="acct-123")
@patch("et.jira_create.fetch_components")
def test_create_issue_interactive_assigns_self_and_component(
    mock_components, mock_search, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())
    mock_components.return_value = [JiraComponent(id="10000", name="Backend")]

    result = create_issue_interactive(
        _prompts(
            confirm_assign_self=lambda: True,
            select_component=lambda components: components[0],
        )
    )

    assert result.key == "PROJ-2"
    _config_arg, fields = mock_create.call_args.args
    assert fields["assignee"] == {"accountId": "acct-123"}
    assert fields["components"] == [{"id": "10000"}]


@patch("et.jira_create.create_issue", return_value="PROJ-3")
@patch("et.jira_create.discover_board_id", return_value="42")
@patch("et.jira_create.fetch_active_sprint", return_value=JiraSprint(id="7", name="Sprint 7"))
@patch("et.jira_create.fetch_sprint_field_id", return_value="customfield_10020")
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_adds_sprint_and_persists_board_id(
    mock_components, mock_field, mock_sprint, mock_board, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())

    result = create_issue_interactive(_prompts(confirm_sprint=lambda: True))

    assert result.key == "PROJ-3"
    _config_arg, fields = mock_create.call_args.args
    assert fields["customfield_10020"] == 7

    from et.config import load_config

    assert load_config().jira.board_id == "42"


@patch("et.jira_create.create_issue", return_value="PROJ-4")
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_uses_existing_board_id(mock_components, mock_create, config_dir):
    _write_config(config_dir, _jira_config(board_id="99"))

    with patch("et.jira_create.discover_board_id") as mock_discover, patch(
        "et.jira_create.fetch_active_sprint", return_value=None
    ) as mock_sprint:
        create_issue_interactive(_prompts(confirm_sprint=lambda: True))
        mock_discover.assert_not_called()
        mock_sprint.assert_called_once_with(mock_create.call_args.args[0], "99")


@patch("et.jira_create.create_issue", return_value="PROJ-5")
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_sets_estimate(mock_components, mock_create, config_dir):
    _write_config(config_dir, _jira_config())

    create_issue_interactive(_prompts(prompt_estimate_hours=lambda: "6"))

    _config_arg, fields = mock_create.call_args.args
    assert fields["timetracking"] == {"originalEstimate": "6h"}


@patch("et.jira_create.create_issue", return_value="PROJ-6")
@patch("et.jira_create.fetch_bug_link_field_id", return_value="customfield_10050")
@patch("et.jira_create.fetch_components", return_value=[])
@patch("et.jira_create.fetch_github_ref")
@patch("et.jira_create.parse_github_url")
def test_create_issue_interactive_prefills_from_github_issue(
    mock_parse, mock_fetch, mock_components, mock_bug_link_field, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())
    mock_fetch.return_value = GithubRefDetails(
        title="Server crashes", body="Steps to reproduce", is_bug=True
    )

    seen_type_default = {}
    seen_description_default = {}

    def prompt_type(default: str) -> str:
        seen_type_default["value"] = default
        return default

    def prompt_description(default: str) -> str:
        seen_description_default["value"] = default
        return default

    result = create_issue_interactive(
        _prompts(
            prompt_type=prompt_type,
            prompt_summary=lambda default: default,
            prompt_description=prompt_description,
        ),
        github_url="https://github.com/canonical/wazuh-server-operator/issues/263",
    )

    assert result.key == "PROJ-6"
    assert seen_type_default["value"] == "Bug"
    assert seen_description_default["value"] == "Steps to reproduce"
    _config_arg, fields = mock_create.call_args.args
    assert fields["summary"] == "Server crashes"
    assert fields["customfield_10050"] == "https://github.com/canonical/wazuh-server-operator/issues/263"
    assert "GitHub: https://github.com/canonical/wazuh-server-operator/issues/263" in (
        fields["description"]["content"][-1]["content"][0]["text"]
    )


@patch("et.jira_create.create_issue", return_value="PROJ-7")
@patch("et.jira_create.fetch_bug_link_field_id", return_value=None)
@patch("et.jira_create.fetch_components", return_value=[])
@patch("et.jira_create.fetch_github_ref", side_effect=GithubRefError("not found"))
def test_create_issue_interactive_warns_on_bad_github_url(
    mock_fetch, mock_components, mock_bug_link_field, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())
    warnings: list[str] = []

    create_issue_interactive(
        _prompts(warn=warnings.append),
        github_url="https://github.com/canonical/wazuh-server-operator/issues/263",
    )

    assert any("could not fetch GitHub details" in message for message in warnings)


@patch("et.jira_create.create_issue", return_value="PROJ-8")
@patch("et.jira_create.fetch_bug_link_field_id", return_value=None)
@patch("et.jira_create.fetch_components", return_value=[])
@patch("et.jira_create.fetch_github_ref")
@patch("et.jira_create.parse_github_url")
def test_create_issue_interactive_warns_when_no_bug_link_field(
    mock_parse, mock_fetch, mock_components, mock_bug_link_field, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())
    mock_fetch.return_value = GithubRefDetails(title="Server crashes", body="", is_bug=False)
    warnings: list[str] = []

    create_issue_interactive(
        _prompts(warn=warnings.append),
        github_url="https://github.com/canonical/wazuh-server-operator/pull/410",
    )

    _config_arg, fields = mock_create.call_args.args
    assert "customfield_10050" not in fields
    assert any("no 'Bug link' field found" in message for message in warnings)


@patch("et.jira_create.create_issue", return_value="PROJ-9")
@patch("et.jira_create.fetch_bug_link_field_id", side_effect=JiraError("boom"))
@patch("et.jira_create.fetch_components", return_value=[])
@patch("et.jira_create.fetch_github_ref")
@patch("et.jira_create.parse_github_url")
def test_create_issue_interactive_warns_when_bug_link_field_lookup_fails(
    mock_parse, mock_fetch, mock_components, mock_bug_link_field, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())
    mock_fetch.return_value = GithubRefDetails(title="Server crashes", body="", is_bug=False)
    warnings: list[str] = []

    create_issue_interactive(
        _prompts(warn=warnings.append),
        github_url="https://github.com/canonical/wazuh-server-operator/pull/410",
    )

    assert any("could not find Jira's 'Bug link' field" in message for message in warnings)


@patch("et.jira_create.create_issue", return_value="PROJ-10")
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_does_not_look_up_bug_link_field_without_github_url(
    mock_components, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())

    with patch("et.jira_create.fetch_bug_link_field_id") as mock_bug_link_field:
        create_issue_interactive(_prompts())

    mock_bug_link_field.assert_not_called()


@patch("et.jira_create.create_issue")
@patch("et.jira_create.fetch_components", side_effect=JiraError("boom"))
def test_create_issue_interactive_warns_and_continues_when_components_fail(
    mock_components, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())
    mock_create.return_value = "PROJ-8"
    warnings: list[str] = []

    result = create_issue_interactive(_prompts(warn=warnings.append))

    assert result.key == "PROJ-8"
    assert any("components" in message for message in warnings)


@patch("et.jira_create.fetch_components", return_value=[])
@patch("et.jira_create.create_issue", side_effect=JiraError("issue creation failed"))
def test_create_issue_interactive_raises_jira_create_error_on_api_failure(
    mock_create, mock_components, config_dir
):
    _write_config(config_dir, _jira_config())

    with pytest.raises(JiraCreateError, match="issue creation failed"):
        create_issue_interactive(_prompts())


@patch("et.jira_create.discover_board_id", return_value=None)
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_raises_when_no_board_configured(
    mock_components, mock_discover, config_dir
):
    _write_config(config_dir, _jira_config())

    with pytest.raises(JiraCreateError, match="no Jira Agile board configured"):
        create_issue_interactive(_prompts(confirm_sprint=lambda: True))


@patch("et.jira_create.create_issue")
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_returns_none_when_confirm_declined(
    mock_components, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())

    result = create_issue_interactive(_prompts(confirm_create=lambda fields: False))

    assert result is None
    mock_create.assert_not_called()


@patch("et.jira_create.create_issue", return_value="PROJ-9")
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_passes_summary_fields_to_confirm(
    mock_components, mock_create, config_dir
):
    _write_config(config_dir, _jira_config())
    captured: list[tuple[str, str]] = []

    def confirm_create(fields: list[tuple[str, str]]) -> bool:
        captured.extend(fields)
        return True

    create_issue_interactive(
        _prompts(
            prompt_summary=lambda default: "Fix the bug",
            prompt_estimate_hours=lambda: "4",
            confirm_create=confirm_create,
        )
    )

    fields_dict = dict(captured)
    assert fields_dict["Summary"] == "Fix the bug"
    assert fields_dict["Estimate"] == "4h"
    assert fields_dict["Assignee"] == "(unassigned)"
    assert fields_dict["Component"] == "(none)"
    assert fields_dict["Sprint"] == "(none)"


@patch("et.jira_create.create_issue", return_value="PROJ-10")
@patch("et.jira_create.fetch_sprint_field_id", return_value="customfield_10020")
@patch("et.jira_create.discover_board_id", return_value="99")
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_falls_back_to_scrum_board_when_cached_board_lacks_sprints(
    mock_components, mock_discover, mock_field, mock_create, config_dir
):
    _write_config(config_dir, _jira_config(board_id="1304"))

    def fake_fetch_active_sprint(jira_config, board_id):
        del jira_config
        if board_id == "1304":
            raise JiraBoardWithoutSprintsError("board 1304 does not support sprints")
        assert board_id == "99"
        return JiraSprint(id="7", name="Sprint 7")

    with patch("et.jira_create.fetch_active_sprint", side_effect=fake_fetch_active_sprint):
        result = create_issue_interactive(_prompts(confirm_sprint=lambda: True))

    assert result is not None
    _config_arg, fields = mock_create.call_args.args
    assert fields["customfield_10020"] == 7

    from et.config import load_config

    assert load_config().jira.board_id == "99"


@patch("et.jira_create.discover_board_id", return_value=None)
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_raises_when_cached_board_lacks_sprints_and_no_fallback(
    mock_components, mock_discover, config_dir
):
    _write_config(config_dir, _jira_config(board_id="1304"))

    with patch(
        "et.jira_create.fetch_active_sprint",
        side_effect=JiraBoardWithoutSprintsError("board 1304 does not support sprints"),
    ):
        with pytest.raises(JiraCreateError, match="does not support sprints"):
            create_issue_interactive(_prompts(confirm_sprint=lambda: True))


@patch("et.jira_create.discover_board_id", return_value="1304")
@patch("et.jira_create.fetch_components", return_value=[])
def test_create_issue_interactive_raises_when_fallback_board_is_same_as_cached(
    mock_components, mock_discover, config_dir
):
    _write_config(config_dir, _jira_config(board_id="1304"))

    with patch(
        "et.jira_create.fetch_active_sprint",
        side_effect=JiraBoardWithoutSprintsError("board 1304 does not support sprints"),
    ):
        with pytest.raises(JiraCreateError, match="does not support sprints"):
            create_issue_interactive(_prompts(confirm_sprint=lambda: True))
