"""Tests for et.config, using a temporary directory via ET_CONFIG_DIR."""

from __future__ import annotations

import pytest

from et.config import (
    DEFAULT_PRIORITY_ORDER,
    ConfigError,
    EtConfig,
    JiraConfig,
    WorkspaceConfigEntry,
    get_config_path,
    load_config,
    load_workspace_names,
    save_config,
)


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ET_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _write_config(config_dir, text: str) -> None:
    (config_dir / "config.yaml").write_text(text)


def test_get_config_path_uses_env_var_override(config_dir):
    assert get_config_path() == config_dir / "config.yaml"


def test_load_workspace_names_parses_list_of_name_mappings(config_dir):
    _write_config(
        config_dir,
        "workspaces:\n"
        "  - name: mails\n"
        "  - name: handson\n"
        "  - name: isd-321\n",
    )
    assert load_workspace_names() == ["mails", "handson", "isd-321"]


def test_load_workspace_names_raises_when_file_missing(config_dir):
    with pytest.raises(ConfigError, match="config file not found"):
        load_workspace_names()


def test_load_workspace_names_raises_on_invalid_yaml(config_dir):
    _write_config(config_dir, "workspaces: [unclosed")
    with pytest.raises(ConfigError, match="could not parse"):
        load_workspace_names()


def test_load_workspace_names_raises_when_top_level_is_not_a_mapping(config_dir):
    _write_config(config_dir, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="must contain a mapping"):
        load_workspace_names()


def test_load_workspace_names_raises_when_workspaces_is_not_a_list(config_dir):
    _write_config(config_dir, "workspaces: mails\n")
    with pytest.raises(ConfigError, match="'workspaces' must be a list"):
        load_workspace_names()


def test_load_workspace_names_raises_when_entry_missing_name_key(config_dir):
    _write_config(config_dir, "workspaces:\n  - not_name: mails\n")
    with pytest.raises(ConfigError, match="must be a mapping with a 'name' key"):
        load_workspace_names()


def test_load_workspace_names_raises_when_name_is_not_a_string(config_dir):
    _write_config(config_dir, "workspaces:\n  - name: 42\n")
    with pytest.raises(ConfigError, match="name must be a string"):
        load_workspace_names()


def test_load_workspace_names_returns_empty_list_for_empty_workspaces_key(config_dir):
    _write_config(config_dir, "workspaces: []\n")
    assert load_workspace_names() == []


def test_load_workspace_names_treats_empty_file_as_no_workspaces(config_dir):
    _write_config(config_dir, "")
    assert load_workspace_names() == []


def test_load_config_defaults_jira_and_workspaces_when_absent(config_dir):
    (config_dir / "config.yaml").write_text("workspaces:\n  - name: mails\n")
    config = load_config()
    assert config.jira is None
    assert config.workspaces == [WorkspaceConfigEntry(name="mails")]


def test_load_config_parses_full_schema(config_dir):
    (config_dir / "config.yaml").write_text(
        """
        jira:
          base_url: https://example.atlassian.net/
          email: me@example.com
          pat: secret-token
          jql: "assignee = currentUser()"
          priority_order: [High, Low]
        workspaces:
          - name: mails
            type: static
          - name: "Fix login timeo"
            type: dynamic
            ref: "jira:PROJ-123"
            description: "Fix login timeout on mobile clients"
        """
    )
    config = load_config()

    assert config.jira == JiraConfig(
        base_url="https://example.atlassian.net/",
        email="me@example.com",
        pat="secret-token",
        jql="assignee = currentUser()",
        priority_order=["High", "Low"],
    )
    assert config.workspaces == [
        WorkspaceConfigEntry(name="mails", type="static"),
        WorkspaceConfigEntry(
            name="Fix login timeo",
            type="dynamic",
            ref="jira:PROJ-123",
            description="Fix login timeout on mobile clients",
        ),
    ]


def test_load_config_parses_project_key_and_board_id(config_dir):
    (config_dir / "config.yaml").write_text(
        """
        jira:
          base_url: https://example.atlassian.net/
          email: me@example.com
          pat: secret-token
          jql: "assignee = currentUser()"
          project_key: ISD
          board_id: "42"
        workspaces: []
        """
    )
    config = load_config()
    assert config.jira is not None
    assert config.jira.project_key == "ISD"
    assert config.jira.board_id == "42"


def test_load_config_defaults_project_key_and_board_id_to_none(config_dir):
    (config_dir / "config.yaml").write_text(
        """
        jira:
          base_url: https://example.atlassian.net/
          email: me@example.com
          pat: secret-token
          jql: "assignee = currentUser()"
        workspaces: []
        """
    )
    config = load_config()
    assert config.jira is not None
    assert config.jira.project_key is None
    assert config.jira.board_id is None


def test_load_config_rejects_non_string_project_key(config_dir):
    (config_dir / "config.yaml").write_text(
        """
        jira:
          base_url: https://example.atlassian.net/
          email: me@example.com
          pat: secret-token
          jql: "assignee = currentUser()"
          project_key: 123
        workspaces: []
        """
    )
    with pytest.raises(ConfigError, match="project_key"):
        load_config()


def test_load_config_jira_priority_order_defaults_when_absent(config_dir):
    (config_dir / "config.yaml").write_text(
        """
        jira:
          base_url: https://example.atlassian.net/
          email: me@example.com
          pat: secret-token
          jql: "assignee = currentUser()"
        workspaces: []
        """
    )
    config = load_config()
    assert config.jira is not None
    assert config.jira.priority_order == DEFAULT_PRIORITY_ORDER


def test_load_config_rejects_invalid_workspace_type(config_dir):
    (config_dir / "config.yaml").write_text(
        "workspaces:\n  - name: mails\n    type: bogus\n"
    )
    with pytest.raises(ConfigError, match="type"):
        load_config()


def test_load_config_rejects_jira_block_missing_required_key(config_dir):
    (config_dir / "config.yaml").write_text(
        """
        jira:
          base_url: https://example.atlassian.net/
          email: me@example.com
        workspaces: []
        """
    )
    with pytest.raises(ConfigError, match="jira.pat"):
        load_config()


def test_save_config_round_trips_through_load_config(config_dir):
    config = EtConfig(
        jira=JiraConfig(
            base_url="https://example.atlassian.net/",
            email="me@example.com",
            pat="secret-token",
            jql="assignee = currentUser()",
            priority_order=["High", "Low"],
            project_key="ISD",
            board_id="42",
        ),
        workspaces=[
            WorkspaceConfigEntry(name="mails", type="static"),
            WorkspaceConfigEntry(
                name="Fix login timeo",
                ref="jira:PROJ-123",
                description="Fix login timeout on mobile clients",
            ),
        ],
    )

    save_config(config)

    assert load_config() == config
    mode = (config_dir / "config.yaml").stat().st_mode & 0o777
    assert mode == 0o600


def test_load_workspace_names_still_returns_plain_name_list(config_dir):
    (config_dir / "config.yaml").write_text(
        "workspaces:\n  - name: mails\n  - name: handson\n"
    )
    assert load_workspace_names() == ["mails", "handson"]
