"""Loading and saving et's configuration file (`~/.config/et/config.yaml`).

Holds the "workspaces" list (name/type/ref/description per managed GNOME
workspace, used by `et ws rename --all` and `et jira start`) and the "jira"
block (Jira Cloud API credentials and query, also used by `et jira
log-time` to log worklogs). Has no Typer/CLI dependency so callers can unit
test without touching the real filesystem (via the `ET_CONFIG_DIR`
environment variable override).
"""


from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR_ENV_VAR = "ET_CONFIG_DIR"
CONFIG_FILE_NAME = "config.yaml"

DEFAULT_PRIORITY_ORDER = ["Highest", "High", "Medium", "Low", "Lowest"]
VALID_WORKSPACE_TYPES = ("dynamic", "static")


class ConfigError(RuntimeError):
    """Raised when the et config file is missing or malformed."""


@dataclass(frozen=True)
class JiraConfig:
    """Jira Cloud REST API credentials and query, from the "jira" config block."""

    base_url: str
    email: str
    pat: str
    jql: str
    priority_order: list[str] = field(default_factory=lambda: list(DEFAULT_PRIORITY_ORDER))
    project_key: str | None = None
    board_id: str | None = None


@dataclass(frozen=True)
class WorkspaceConfigEntry:
    """One entry in the "workspaces" config list."""

    name: str
    type: str = "dynamic"
    ref: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class EtConfig:
    """The full parsed contents of `~/.config/et/config.yaml`."""

    jira: JiraConfig | None
    workspaces: list[WorkspaceConfigEntry]


def get_config_dir() -> Path:
    """Return the directory et's config file lives in (~/.config/et by default)."""
    override = os.environ.get(CONFIG_DIR_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".config" / "et"


def get_config_path() -> Path:
    """Return the full path to et's config file."""
    return get_config_dir() / CONFIG_FILE_NAME


def _parse_jira_block(raw_jira: object, path: Path) -> JiraConfig | None:
    if raw_jira is None:
        return None
    if not isinstance(raw_jira, dict):
        raise ConfigError(f"config file {path}: 'jira' must be a mapping")

    required: dict[str, str] = {}
    for key in ("base_url", "email", "pat", "jql"):
        if key not in raw_jira:
            raise ConfigError(f"config file {path}: jira.{key} is required")
        value = raw_jira[key]
        if not isinstance(value, str):
            raise ConfigError(f"config file {path}: jira.{key} must be a string")
        required[key] = value

    priority_order = raw_jira.get("priority_order", DEFAULT_PRIORITY_ORDER)
    if not isinstance(priority_order, list) or not all(
        isinstance(item, str) for item in priority_order
    ):
        raise ConfigError(f"config file {path}: jira.priority_order must be a list of strings")

    project_key = raw_jira.get("project_key")
    if project_key is not None and not isinstance(project_key, str):
        raise ConfigError(f"config file {path}: jira.project_key must be a string")

    board_id = raw_jira.get("board_id")
    if board_id is not None and not isinstance(board_id, str):
        raise ConfigError(f"config file {path}: jira.board_id must be a string")

    return JiraConfig(
        base_url=required["base_url"],
        email=required["email"],
        pat=required["pat"],
        jql=required["jql"],
        priority_order=list(priority_order),
        project_key=project_key,
        board_id=board_id,
    )


def _parse_workspaces(raw_workspaces: object, path: Path) -> list[WorkspaceConfigEntry]:
    if not isinstance(raw_workspaces, list):
        raise ConfigError(f"config file {path}: 'workspaces' must be a list")

    entries: list[WorkspaceConfigEntry] = []
    for index, raw_entry in enumerate(raw_workspaces):
        if not isinstance(raw_entry, dict) or "name" not in raw_entry:
            raise ConfigError(
                f"config file {path}: workspaces[{index}] must be a mapping with a 'name' key"
            )
        name = raw_entry["name"]
        if not isinstance(name, str):
            raise ConfigError(f"config file {path}: workspaces[{index}].name must be a string")

        workspace_type = raw_entry.get("type", "dynamic")
        if workspace_type not in VALID_WORKSPACE_TYPES:
            raise ConfigError(
                f"config file {path}: workspaces[{index}].type must be one of "
                f"{VALID_WORKSPACE_TYPES}, got {workspace_type!r}"
            )

        ref = raw_entry.get("ref")
        if ref is not None and not isinstance(ref, str):
            raise ConfigError(f"config file {path}: workspaces[{index}].ref must be a string")

        description = raw_entry.get("description")
        if description is not None and not isinstance(description, str):
            raise ConfigError(
                f"config file {path}: workspaces[{index}].description must be a string"
            )

        entries.append(
            WorkspaceConfigEntry(name=name, type=workspace_type, ref=ref, description=description)
        )

    return entries


def load_config() -> EtConfig:
    """Load and validate the full et configuration file.

    Raises ConfigError if the file doesn't exist, can't be read, isn't
    valid YAML, or doesn't match the expected schema. Missing optional keys
    (`jira`, `workspaces`) default to `None` and `[]` respectively.
    """
    path = get_config_path()
    if not path.is_file():
        raise ConfigError(
            f"config file not found: {path} "
            f"(create it with a top-level 'workspaces' list of {{name: ...}} entries)"
        )

    try:
        raw = path.read_text()
    except OSError as exc:
        raise ConfigError(f"could not read config file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse config file {path}: {exc}") from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must contain a mapping at the top level")

    jira_config = _parse_jira_block(data.get("jira"), path)
    workspace_entries = _parse_workspaces(data.get("workspaces", []), path)

    return EtConfig(jira=jira_config, workspaces=workspace_entries)


def save_config(config: EtConfig) -> None:
    """Write `config` back to the config file, creating ~/.config/et if needed.

    The file is chmod'd 0o600 since it may contain a Jira API token.
    """
    data: dict[str, object] = {}

    if config.jira is not None:
        jira_data: dict[str, object] = {
            "base_url": config.jira.base_url,
            "email": config.jira.email,
            "pat": config.jira.pat,
            "jql": config.jira.jql,
            "priority_order": list(config.jira.priority_order),
        }
        if config.jira.project_key is not None:
            jira_data["project_key"] = config.jira.project_key
        if config.jira.board_id is not None:
            jira_data["board_id"] = config.jira.board_id
        data["jira"] = jira_data

    workspaces_data: list[dict[str, object]] = []
    for entry in config.workspaces:
        entry_data: dict[str, object] = {"name": entry.name}
        if entry.type != "dynamic":
            entry_data["type"] = entry.type
        if entry.ref is not None:
            entry_data["ref"] = entry.ref
        if entry.description is not None:
            entry_data["description"] = entry.description
        workspaces_data.append(entry_data)
    data["workspaces"] = workspaces_data

    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    path.chmod(0o600)



def load_workspace_names() -> list[str]:
    """Return the ordered list of workspace names configured for `ws rename --all`.

    Reads the "workspaces" key from the config file: a list of mappings,
    each with a "name" key, e.g.:

        workspaces:
          - name: mails
          - name: handson
          - name: isd-321

    Raises ConfigError if the file is missing, unreadable, or malformed.
    """
    return [entry.name for entry in load_config().workspaces]
