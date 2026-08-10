"""Orchestrates `et jira create`: interactively build and create a new Jira issue.

Collects the fields a new issue needs (type, summary, assignee, priority,
component, sprint, estimate, description) via injected prompt/confirm
callbacks (so this module stays Typer-free and unit testable, mirroring
`et.jira_time` and `et.task`), optionally pre-filling the summary/description
(and defaulting the type to "Bug") from a GitHub issue/PR URL via
`et.github_ref`. When a GitHub URL is given, it's also written to the
issue's "Bug link" custom field (looked up by name, like "Sprint"), if that
field exists on the Jira instance. Enrichment steps that aren't strictly
required to create an issue (components, active sprint lookup, sprint field
discovery, bug link field discovery) degrade gracefully: any `JiraError`
there is reported via the `warn` callback and that field is skipped, rather
than aborting the whole command. Adding to a sprint without any Agile board
configured/discoverable is treated as a hard error instead, since there's
nothing sensible to fall back to. Before the issue is actually created, the
assembled fields are shown to the user via `confirm_create` for a final
go/no-go.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from et.config import EtConfig, JiraConfig, load_config, save_config
from et.github_ref import GithubRefError, fetch_github_ref, parse_github_url
from et.jira import (
    JiraBoardWithoutSprintsError,
    JiraComponent,
    JiraError,
    JiraSprint,
    create_issue,
    discover_board_id,
    fetch_active_sprint,
    fetch_bug_link_field_id,
    fetch_components,
    fetch_sprint_field_id,
    search_user_account_id,
    text_to_adf,
)

SCRUM_BOARD_TYPE = "scrum"
DEFAULT_ISSUE_TYPE = "Story"
DEFAULT_PRIORITY = "Medium"
ISSUE_TYPES = ("Bug", "Story", "Task")
PRIORITIES = ("Highest", "High", "Medium", "Low", "Lowest")
DESCRIPTION_SUMMARY_LENGTH = 60


class JiraCreateError(RuntimeError):
    """Raised when a new Jira issue cannot be created."""


@dataclass(frozen=True)
class JiraCreateResult:
    """Summary of what `create_issue_interactive` did."""

    key: str
    url: str


def _no_op_warn(message: str) -> None:
    """Default no-op `warn` callback."""


@dataclass(frozen=True)
class IssueDraftPrompts:
    """Callbacks `create_issue_interactive` uses to collect each field.

    Kept as plain callables (rather than e.g. a Typer-specific type) so the
    CLI layer can wire these to `typer.prompt`/`typer.confirm` while tests
    can supply canned answers.
    """

    prompt_type: Callable[[str], str]
    prompt_summary: Callable[[str], str]
    confirm_assign_self: Callable[[], bool]
    prompt_priority: Callable[[str], str]
    select_component: Callable[[list[JiraComponent]], JiraComponent | None]
    confirm_sprint: Callable[[], bool]
    prompt_estimate_hours: Callable[[], str]
    prompt_description: Callable[[str], str]
    confirm_create: Callable[[list[tuple[str, str]]], bool]
    warn: Callable[[str], None] = _no_op_warn


def _persist_board_id(config: EtConfig, jira: JiraConfig, board_id: str) -> None:
    """Save `board_id` as `jira.board_id`, so later runs skip board discovery."""
    updated_jira = JiraConfig(
        base_url=jira.base_url,
        email=jira.email,
        pat=jira.pat,
        jql=jira.jql,
        priority_order=jira.priority_order,
        project_key=jira.project_key,
        board_id=board_id,
    )
    save_config(EtConfig(jira=updated_jira, workspaces=config.workspaces))


def _resolve_board_id(config: EtConfig, jira: JiraConfig, project_key: str) -> str | None:
    """Return `jira.board_id`, discovering and persisting it if not yet set.

    Discovery prefers a Scrum board (`board_type="scrum"`), since only
    Scrum boards support sprints — a Kanban board would otherwise get
    cached here and later fail every `et jira create --sprint` with a
    confusing "board does not support sprints" error.
    """
    if jira.board_id:
        return jira.board_id

    board_id = discover_board_id(jira, project_key, board_type=SCRUM_BOARD_TYPE)
    if board_id is None:
        return None

    _persist_board_id(config, jira, board_id)
    return board_id


def _fetch_active_sprint_or_warn(
    config: EtConfig,
    jira: JiraConfig,
    project_key: str,
    board_id: str,
    warn: Callable[[str], None],
) -> JiraSprint | None:
    """Fetch the active sprint on `board_id`, falling back to a fresh Scrum board lookup.

    If `board_id` (whether cached or just discovered) turns out not to
    support sprints at all (Jira's board API rejects Kanban boards with a
    "does not support sprints" 400), tries discovering a genuine Scrum
    board for `project_key` and persisting it in its place. Raises
    `JiraCreateError` if that fallback also can't find a Scrum board,
    since there's no sensible way to add the issue to a sprint at that
    point. Any other `JiraError` (network issue, no active sprint, etc.)
    is reported via `warn` and treated as "skip the sprint" — this
    function owns all such warnings, so callers should not warn again on
    a `None` return.
    """
    try:
        sprint = fetch_active_sprint(jira, board_id)
    except JiraBoardWithoutSprintsError:
        fallback_board_id = discover_board_id(jira, project_key, board_type=SCRUM_BOARD_TYPE)
        if fallback_board_id is None or fallback_board_id == board_id:
            raise JiraCreateError(
                f"the configured Jira board (id {board_id}) does not support sprints, and no "
                f"Scrum board could be found for project '{project_key}' (set jira.board_id "
                "to a Scrum board's id manually, or decline the sprint prompt to skip it)"
            ) from None
        try:
            sprint = fetch_active_sprint(jira, fallback_board_id)
        except JiraError as exc:
            warn(f"could not resolve the current sprint: {exc}")
            return None
        _persist_board_id(config, jira, fallback_board_id)
    except JiraError as exc:
        warn(f"could not resolve the current sprint: {exc}")
        return None

    if sprint is None:
        warn("no active sprint found on the project's board; skipping sprint")
    return sprint


def _summarize_description(description: str) -> str:
    if not description:
        return "(none)"
    first_line = description.splitlines()[0]
    if len(description) > len(first_line) or len(first_line) > DESCRIPTION_SUMMARY_LENGTH:
        return first_line[:DESCRIPTION_SUMMARY_LENGTH].rstrip() + "..."
    return first_line


def create_issue_interactive(
    prompts: IssueDraftPrompts, github_url: str | None = None
) -> JiraCreateResult | None:
    """Interactively collect a new issue's fields and create it in Jira.

    Returns `None` if the user declines the final `confirm_create`
    summary prompt, leaving nothing created.

    Raises `ConfigError` if the config file is missing/malformed, and
    `JiraCreateError` if there's no 'jira' config block, no `project_key`
    is configured, the user opts to add the issue to the current sprint
    but no Agile board is configured or discoverable, or the final `POST
    /rest/api/3/issue` call fails. A `github_url` that fails to parse or
    fetch is reported via `prompts.warn` rather than raised, falling back
    to blank summary/description defaults.
    """
    config: EtConfig = load_config()
    if config.jira is None:
        raise JiraCreateError(
            "no 'jira' block found in the config file "
            "(add base_url/email/pat/jql under a top-level 'jira:' key)"
        )
    jira = config.jira
    project_key = jira.project_key
    if not project_key:
        raise JiraCreateError(
            "no 'jira.project_key' set in the config file "
            "(add it under the top-level 'jira:' key, e.g. project_key: ISD)"
        )

    github_title = ""
    github_body = ""
    is_bug = False
    if github_url:
        try:
            ref = parse_github_url(github_url)
            details = fetch_github_ref(ref)
        except GithubRefError as exc:
            prompts.warn(f"could not fetch GitHub details: {exc}")
        else:
            github_title = details.title
            github_body = details.body
            is_bug = details.is_bug

    issue_type = prompts.prompt_type("Bug" if is_bug else DEFAULT_ISSUE_TYPE)
    summary = prompts.prompt_summary(github_title)

    assignee_account_id: str | None = None
    if prompts.confirm_assign_self():
        try:
            assignee_account_id = search_user_account_id(jira, jira.email)
        except JiraError as exc:
            prompts.warn(f"could not resolve your Jira account: {exc}")

    priority = prompts.prompt_priority(DEFAULT_PRIORITY)

    components: list[JiraComponent] = []
    try:
        components = fetch_components(jira, project_key)
    except JiraError as exc:
        prompts.warn(f"could not fetch project components: {exc}")
    component = prompts.select_component(components) if components else None

    sprint_field_id: str | None = None
    sprint: JiraSprint | None = None
    if prompts.confirm_sprint():
        board_id = _resolve_board_id(config, jira, project_key)
        if board_id is None:
            raise JiraCreateError(
                f"no Jira Agile board configured or discoverable for project "
                f"'{project_key}' (set jira.board_id in the config file, or "
                "decline the sprint prompt to skip it)"
            )

        sprint = _fetch_active_sprint_or_warn(config, jira, project_key, board_id, prompts.warn)
        if sprint is not None:
            try:
                sprint_field_id = fetch_sprint_field_id(jira)
            except JiraError as exc:
                prompts.warn(f"could not find Jira's 'Sprint' field: {exc}")
            if sprint_field_id is None:
                prompts.warn("could not find Jira's 'Sprint' field; skipping sprint")

    estimate_hours_raw = prompts.prompt_estimate_hours().strip()
    original_estimate: str | None = None
    if estimate_hours_raw:
        original_estimate = f"{estimate_hours_raw}h"

    description = prompts.prompt_description(github_body)
    if github_url:
        reference = f"GitHub: {github_url}"
        description = f"{description}\n\n{reference}" if description else reference

    bug_link_field_id: str | None = None
    if github_url:
        try:
            bug_link_field_id = fetch_bug_link_field_id(jira)
        except JiraError as exc:
            prompts.warn(f"could not find Jira's 'Bug link' field: {exc}")
        if bug_link_field_id is None:
            prompts.warn("no 'Bug link' field found on this Jira instance; skipping it")

    summary_fields: list[tuple[str, str]] = [
        ("Project", project_key),
        ("Type", issue_type),
        ("Summary", summary),
        ("Assignee", jira.email if assignee_account_id else "(unassigned)"),
        ("Priority", priority),
        ("Component", component.name if component is not None else "(none)"),
        ("Sprint", sprint.name if sprint_field_id and sprint else "(none)"),
        ("Estimate", original_estimate or "(none)"),
        ("Bug link", github_url if bug_link_field_id and github_url else "(none)"),
        ("Description", _summarize_description(description)),
    ]
    if not prompts.confirm_create(summary_fields):
        return None

    fields: dict[str, object] = {
        "project": {"key": project_key},
        "issuetype": {"name": issue_type},
        "summary": summary,
        "priority": {"name": priority},
    }
    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}
    if component is not None:
        fields["components"] = [{"id": component.id}]
    if sprint_field_id and sprint is not None:
        fields[sprint_field_id] = int(sprint.id) if sprint.id.isdigit() else sprint.id
    if original_estimate:
        fields["timetracking"] = {"originalEstimate": original_estimate}
    if description:
        fields["description"] = text_to_adf(description)
    if bug_link_field_id and github_url:
        fields[bug_link_field_id] = github_url

    try:
        key = create_issue(jira, fields)
    except JiraError as exc:
        raise JiraCreateError(str(exc)) from exc

    base_url = jira.base_url.rstrip("/")
    return JiraCreateResult(key=key, url=f"{base_url}/browse/{key}")

