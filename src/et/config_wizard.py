"""Orchestrates `et config`: interactively build/update `~/.config/et/config.yaml`.

Collects the "jira" block (base_url/email/pat/jql/project_key/board_id) and
the "workspaces" list via injected prompt/confirm/select/warn callbacks (so
this module stays Typer-free and unit testable, mirroring `et.jira_create`
and `et.jira_time`). Pre-fills defaults from the existing config when one
is already present. This module never touches the filesystem itself:
`run_config_wizard` returns the assembled `EtConfig` (or `None` if the user
backs out of the final save confirmation), leaving the actual `save_config`
call to the CLI layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from et.config import DEFAULT_PRIORITY_ORDER, EtConfig, JiraConfig, WorkspaceConfigEntry
from et.jira import JiraError, check_credentials


def _no_op_warn(message: str) -> None:
    """Default no-op `warn` callback."""


@dataclass(frozen=True)
class ConfigWizardPrompts:
    """Callbacks `run_config_wizard` uses to collect each field/decision.

    Kept as plain callables (rather than e.g. a Typer-specific type) so the
    CLI layer can wire these to `typer.prompt`/`typer.confirm` while tests
    can supply canned answers.
    """

    # Jira block.
    confirm_configure_jira: Callable[[bool], bool]
    prompt_base_url: Callable[[str], str]
    prompt_email: Callable[[str], str]
    prompt_pat: Callable[[str], str]
    prompt_jql: Callable[[str], str]
    prompt_project_key: Callable[[str], str]
    prompt_board_id: Callable[[str], str]
    resolve_credential_check_failure: Callable[[str], str]

    # Workspaces block. `prompt_workspace_action` returns one of "add",
    # "edit", "remove", "done".
    prompt_workspace_action: Callable[[list[WorkspaceConfigEntry]], str]
    select_workspace_entry: Callable[[list[WorkspaceConfigEntry], str], int | None]
    prompt_workspace_entry: Callable[[WorkspaceConfigEntry | None], WorkspaceConfigEntry | None]

    # Final review.
    confirm_save: Callable[[EtConfig], bool]
    warn: Callable[[str], None] = _no_op_warn


def _collect_jira(prompts: ConfigWizardPrompts, existing: JiraConfig | None) -> JiraConfig | None:
    if not prompts.confirm_configure_jira(existing is not None):
        return None

    base_url = existing.base_url if existing else ""
    email = existing.email if existing else ""
    pat = existing.pat if existing else ""
    jql = existing.jql if existing else ""
    project_key = existing.project_key or "" if existing else ""
    board_id = existing.board_id or "" if existing else ""
    priority_order = list(existing.priority_order) if existing else list(DEFAULT_PRIORITY_ORDER)

    while True:
        base_url = prompts.prompt_base_url(base_url)
        email = prompts.prompt_email(email)
        pat = prompts.prompt_pat(pat)
        jql = prompts.prompt_jql(jql)
        project_key = prompts.prompt_project_key(project_key)
        board_id = prompts.prompt_board_id(board_id)

        candidate = JiraConfig(
            base_url=base_url,
            email=email,
            pat=pat,
            jql=jql,
            priority_order=priority_order,
            project_key=project_key or None,
            board_id=board_id or None,
        )

        try:
            check_credentials(candidate)
        except JiraError as exc:
            action = prompts.resolve_credential_check_failure(str(exc))
            if action == "retry":
                continue
            if action == "discard":
                return None
            # "keep": fall through and use the candidate as typed anyway.

        return candidate


def _collect_workspaces(
    prompts: ConfigWizardPrompts, existing: list[WorkspaceConfigEntry]
) -> list[WorkspaceConfigEntry]:
    entries = list(existing)

    while True:
        action = prompts.prompt_workspace_action(entries)

        if action == "done":
            return entries

        if action == "add":
            entry = prompts.prompt_workspace_entry(None)
            if entry is not None:
                entries.append(entry)
            continue

        if action == "edit":
            index = prompts.select_workspace_entry(entries, "edit")
            if index is None:
                continue
            edited = prompts.prompt_workspace_entry(entries[index])
            if edited is not None:
                entries[index] = edited
            continue

        if action == "remove":
            index = prompts.select_workspace_entry(entries, "remove")
            if index is not None:
                del entries[index]
            continue

        # Unrecognized action: treat as "done" rather than looping forever.
        return entries


def run_config_wizard(prompts: ConfigWizardPrompts, existing: EtConfig | None) -> EtConfig | None:
    """Interactively collect the full config and return it, or `None` if declined.

    Pre-fills every field from `existing` when given (i.e. a config file
    was already present). Returns `None` if the user declines the final
    `confirm_save` summary prompt, in which case the caller must not write
    anything to disk.
    """
    existing_jira = existing.jira if existing else None
    existing_workspaces = existing.workspaces if existing else []

    jira = _collect_jira(prompts, existing_jira)
    workspaces = _collect_workspaces(prompts, existing_workspaces)

    config = EtConfig(jira=jira, workspaces=workspaces)

    if not prompts.confirm_save(config):
        return None

    return config
