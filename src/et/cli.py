"""Command-line interface for et (effort tracker)."""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, cast

import typer

from et.config import ConfigError, load_config, load_workspace_names
from et.jira_create import (
    ISSUE_TYPES,
    PRIORITIES,
    IssueDraftPrompts,
    JiraCreateError,
    create_issue_interactive,
)
from et.jira_ref import jira_key_from_ref
from et.jira_time import (
    JiraLogTimeError,
    LogTimeResult,
    log_manual_time_for_current_workspace,
    log_time_for_current_workspace,
)
from et.task import (
    BLOCKED_STATUS,
    IN_PROGRESS_STATUS,
    STATUS_WORKFLOW_ORDER,
    TaskError,
    add_comment_to_current_workspace,
    complete_task_for_current_workspace,
    create_task_from_jira,
    get_current_status_for_current_workspace,
    set_status_for_current_workspace,
)
from et.tracker import (
    TrackerError,
    find_timer_for_workspace,
    format_duration,
    load_timers,
    parse_hours_to_seconds,
)
from et.workspaces import (
    WorkspaceError,
    get_active_workspace_index,
    get_workspace_count,
    is_dynamic_workspaces_enabled,
    rename_active_workspace,
    rename_all_workspaces,
)
from et.ws import (
    OrganizePlanRow,
    WsDeleteError,
    WsOrganizeError,
    apply_organize_plan,
    build_organize_editor_content,
    build_organize_plan,
    delete_active_workspace,
    list_organize_candidates,
    open_in_editor,
    parse_organize_order,
    prepare_organize,
)

if TYPE_CHECKING:
    from et.config import EtConfig
    from et.jira import JiraComponent, JiraIssue
    from et.task import TaskCreateResult


def _hyperlink(text: str, url: str) -> str:
    """Wrap `text` in an OSC 8 terminal hyperlink pointing to `url`.

    Falls back to plain `text` when stdout isn't a terminal (e.g. piped or
    redirected output), since the escape codes would otherwise be visible.
    """
    if not sys.stdout.isatty():
        return text
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


app = typer.Typer(
    help="et: a small CLI for tracking effort and managing your workspace.",
    no_args_is_help=False,
)
ws_app = typer.Typer(help="Interact with GNOME/Ubuntu workspaces.", no_args_is_help=True)
app.add_typer(ws_app, name="ws")

jira_app = typer.Typer(
    help="Convenience layer around ws (plus the Tracker/Jira integrations) for a single "
    "task's lifecycle.",
    no_args_is_help=True,
)
app.add_typer(jira_app, name="jira")


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    debug: bool = typer.Option(
        False, "--debug", help="Log the Jira API requests et makes (URL, JQL, results)."
    ),
) -> None:
    """Send library warnings (e.g. from et.jira) to stderr.

    With no subcommand, shows the same info as `et info` (and `et jira
    log-time`) would act on (the Jira issue and tracked time for the
    active workspace) when that workspace is part of the managed
    (non-`static`) pool; otherwise falls back to the regular help text.
    """
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(levelname)s: %(message)s",
        force=True,
    )

    _ensure_static_workspaces()

    if ctx.invoked_subcommand is not None:
        return

    _show_active_workspace_info(ctx)


def _ensure_static_workspaces() -> None:
    """Exit with setup instructions unless GNOME uses a fixed set of workspaces.

    et manages a fixed pool of "ET-<n>" workspaces, which only works when
    GNOME's dynamic-workspaces mode is off. If it's on, print how to disable
    it (and pick a workspace count) and exit non-zero.
    """
    try:
        dynamic = is_dynamic_workspaces_enabled()
    except WorkspaceError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if dynamic:
        typer.echo(
            "Error: et needs a fixed set of GNOME workspaces, but dynamic "
            "workspaces are enabled.\n"
            "Disable them and choose how many workspaces you want:\n"
            "  gsettings set org.gnome.mutter dynamic-workspaces false\n"
            "  gsettings set org.gnome.desktop.wm.preferences num-workspaces <N>",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command("info")
def info(ctx: typer.Context) -> None:
    """Show the Jira issue and tracked time for the active workspace.

    Explicit equivalent of running `et` with no subcommand: shows the
    active workspace's linked Jira issue and tracked time when it's part
    of the managed (non-`static`) pool, otherwise falls back to the
    regular help text.
    """
    _show_active_workspace_info(ctx)


def _show_active_workspace_info(ctx: typer.Context) -> None:
    """Print the active workspace's Jira info + tracked time, or fall back to help."""
    root_ctx = ctx.find_root()
    try:
        index = get_active_workspace_index()
        config = load_config()
    except (ConfigError, WorkspaceError):
        typer.echo(root_ctx.get_help())
        raise typer.Exit() from None

    entry = config.workspaces[index] if index < len(config.workspaces) else None
    if entry is None or entry.type == "static":
        typer.echo(root_ctx.get_help())
        raise typer.Exit()

    _print_workspace_jira_info(index, config)
    try:
        _print_workspace_time_spent(index)
    except TrackerError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


@ws_app.command("rename")
def rename(
    new_name: str | None = typer.Argument(
        None, help="New name for the active workspace (omit when using --all)."
    ),
    all_workspaces: bool = typer.Option(
        False,
        "--all",
        help="Rename all workspaces using the 'workspaces' list from ~/.config/et/config.yaml.",
    ),
) -> None:
    """Rename the current (active) workspace to NEW_NAME, or all workspaces with --all."""
    if all_workspaces:
        if new_name is not None:
            typer.echo("Error: NEW_NAME must not be given together with --all", err=True)
            raise typer.Exit(code=1)

        try:
            names = load_workspace_names()
            indices = rename_all_workspaces(names)
        except (ConfigError, WorkspaceError) as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(code=1) from error

        for index, name in zip(indices, names, strict=True):
            typer.echo(f"Renamed workspace {index + 1} to '{name}'")
        return

    if new_name is None:
        typer.echo("Error: NEW_NAME is required unless --all is given", err=True)
        raise typer.Exit(code=1)

    try:
        index = rename_active_workspace(new_name)
    except WorkspaceError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Renamed workspace {index + 1} to '{new_name}'")


def _jira_base_url() -> str:
    """Return the configured Jira base URL (no trailing slash), or "" if unavailable."""
    try:
        config = load_config()
    except ConfigError:
        return ""
    return config.jira.base_url.rstrip("/") if config.jira else ""


def _jira_key_link(key: str) -> str:
    """Return `key` as a clickable hyperlink to its Jira issue.

    Falls back to plain `key` if no Jira base URL is configured.
    """
    base_url = _jira_base_url()
    return _hyperlink(key, f"{base_url}/browse/{key}") if base_url else key


def _jira_ref_link(key: str) -> str:
    """Return "jira:<key>" as a clickable hyperlink to its Jira issue.

    Falls back to plain "jira:<key>" if no Jira base URL is configured.
    """
    base_url = _jira_base_url()
    return _hyperlink(f"jira:{key}", f"{base_url}/browse/{key}") if base_url else f"jira:{key}"


def _print_workspace_jira_info(index: int, config: EtConfig) -> None:
    """Print the Jira issue (description + link) linked to workspace `index`, if any."""
    entry = config.workspaces[index] if index < len(config.workspaces) else None
    key = jira_key_from_ref(entry.ref) if entry else None

    if entry is None or key is None:
        typer.echo("No Jira issue linked to this workspace.")
        return

    typer.echo(f"Workspace {index + 1}: {entry.name}")
    typer.echo(entry.description or "(no description)")
    typer.echo(_jira_ref_link(key))


def _print_workspace_time_spent(index: int) -> None:
    """Print the elapsed time of the ET-<n> tracker bound to workspace `index`, if any."""
    entries = load_timers()
    timer = find_timer_for_workspace(entries, index)
    if timer is None:
        typer.echo("No tracker for this workspace.")
        return

    elapsed = timer.get("timeElapsed", 0)
    seconds = elapsed if isinstance(elapsed, (int, float)) else 0
    running = " (running)" if timer.get("running") else ""
    typer.echo(f"Time spent: {format_duration(seconds)}{running}")


@ws_app.command("delete")
def ws_delete(
    force: bool = typer.Option(
        False,
        "--force",
        help="Delete even if the workspace is still linked to a Jira issue; its tracker is lost.",
    ),
) -> None:
    """Delete the active workspace, shifting later workspaces left to fill the gap.

    Only works when the active workspace is free (not `static`, and not
    linked to a Jira issue) — use `et jira complete` (or `et jira
    log-time`) first if it's still tracking something, or pass `--force`
    to delete it anyway (its Tracker timer, if any, is discarded rather
    than logged). Every non-static workspace after it (and its Tracker
    timer) shifts one slot to the left, then the now-empty trailing slot is
    removed by decrementing GNOME's workspace count (unless the last
    workspace is `static`, in which case the count is left unchanged).
    """
    try:
        result = delete_active_workspace(force=force)
    except (ConfigError, WorkspaceError, WsDeleteError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    plural = "workspace" if result.remaining_workspaces == 1 else "workspaces"
    typer.echo(
        f"Deleted workspace {result.workspace_index + 1} "
        f"(now managing {result.remaining_workspaces} {plural})"
    )


def _format_organize_row(row: OrganizePlanRow) -> str:
    """Format one `OrganizePlanRow` for the `ws organize` confirmation summary."""
    key = jira_key_from_ref(row.entry.ref) or "-"
    if row.timer is not None:
        elapsed = row.timer.get("timeElapsed", 0)
        seconds = elapsed if isinstance(elapsed, (int, float)) else 0
        running = " (running)" if row.timer.get("running") else ""
        timer_desc = f"{format_duration(seconds)}{running}"
    else:
        timer_desc = "no timer"
    return (
        f"  {row.old_slot + 1:>3} -> {row.new_slot + 1:<3} "
        f"{row.entry.name:<20} {key:<12} {timer_desc}"
    )


@ws_app.command("organize")
def ws_organize() -> None:
    """Reorder dynamic workspaces by editing their order in `$EDITOR`.

    Only non-`static` ("dynamic") workspaces are listed and reorderable;
    `static` workspaces always keep their current slot. Opens `$EDITOR`
    (falling back to `vi`) on a text listing of the dynamic workspaces;
    reorder the lines (without adding, removing, or duplicating any) and
    save to express the desired new order. Shows a before/after summary
    (slot, name, linked Jira issue, tracker time) and asks for confirmation
    before applying anything. Each moved workspace's Tracker timer follows
    it to its new slot. The active GNOME workspace's index is left
    unchanged — whatever task ends up in that slot is simply what's shown.
    """
    try:
        config = load_config()
        count = get_workspace_count()
        entries = load_timers()
    except (ConfigError, WorkspaceError, TrackerError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    workspaces_list, slots = prepare_organize(config, count)

    if len(slots) < 2:
        typer.echo("Nothing to organize: fewer than 2 dynamic workspaces.")
        return

    candidates = list_organize_candidates(workspaces_list, slots, entries)
    editor_content = build_organize_editor_content(candidates)

    try:
        edited_content = open_in_editor(editor_content)
        new_order = parse_organize_order(edited_content.splitlines(), slots)
    except WsOrganizeError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if new_order == slots:
        typer.echo("No changes.")
        return

    try:
        plan = build_organize_plan(workspaces_list, entries, slots, new_order)
    except WsOrganizeError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo("Proposed workspace order:")
    typer.echo("  old -> new name                 jira         timer")
    for row in sorted(plan, key=lambda row: row.new_slot):
        typer.echo(_format_organize_row(row))

    if not typer.confirm("Apply this reordering?", default=False):
        typer.echo("Aborted.")
        return

    try:
        apply_organize_plan(config, workspaces_list, entries, plan)
    except (ConfigError, WorkspaceError, TrackerError, WsOrganizeError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Reorganized {len(plan)} workspaces.")


def _print_task_created(result: TaskCreateResult) -> None:
    key = jira_key_from_ref(result.ref)
    ref_suffix = f" (linked to {key})" if key else ""
    typer.echo(f"Created workspace {result.workspace_index + 1}: '{result.name}'{ref_suffix}")
    if not result.timer_created:
        typer.echo(f"Tracker already existed for workspace {result.workspace_index + 1}")
    typer.echo(f"Switched to workspace {result.workspace_index + 1}")
    if not result.window_moved:
        typer.echo(
            "Note: could not move this terminal window to the new workspace "
            "(unsupported here, e.g. under Wayland)."
        )


def _jira_key_option() -> str | None:
    """Shared `-j/--jira KEY` option for commands acting on "the current ticket".

    Lets the caller target a specific Jira issue instead of the one linked
    to the active workspace. When given, these commands skip workspace
    resolution entirely (so they work even outside a managed workspace).
    """
    return cast(
        "str | None",
        typer.Option(
            None,
            "--jira",
            "-j",
            help="Jira issue key to act on, instead of the active workspace's linked issue.",
        ),
    )


@jira_app.command("start")
def jira_start() -> None:
    """Start a new task: allocate a workspace slot, its Tracker timer, and switch to it.

    Lists your active Jira issues that aren't already linked to a
    workspace and lets you pick one (its summary becomes the workspace
    name/description and its key is linked). If the selected issue isn't
    already "In Progress", offers to move it there. When every workspace is
    already taken, `et jira start` asks whether to add one more (bumping
    GNOME's workspace count by one). The terminal window `et jira start`
    was run from is moved along to the new workspace, so it doesn't get
    left behind.
    """
    try:
        config = load_config()
    except ConfigError:
        config = None
    base_url = config.jira.base_url.rstrip("/") if config and config.jira else ""

    def select_issue(issues: list[JiraIssue]) -> JiraIssue | None:
        if not issues:
            typer.echo("No active Jira issues available to start a task from.")
            return None

        typer.echo("Active issues not yet linked to a workspace:")
        for position, issue in enumerate(issues, start=1):
            key_display = (
                _hyperlink(issue.key, f"{base_url}/browse/{issue.key}")
                if base_url
                else issue.key
            )
            status_display = f" ({issue.status})" if issue.status else ""
            typer.echo(
                f"  {position}. {key_display} [{issue.priority}]{status_display} {issue.summary}"
            )

        choice = typer.prompt("Pick an issue number (or 0 to cancel)", default="0")
        try:
            selected = int(choice)
        except ValueError:
            selected = 0
        if selected < 1 or selected > len(issues):
            return None
        return issues[selected - 1]

    def confirm_transition(issue: JiraIssue) -> bool:
        status_display = issue.status or "an unknown state"
        key_display = (
            _hyperlink(issue.key, f"{base_url}/browse/{issue.key}") if base_url else issue.key
        )
        return typer.confirm(
            f"{key_display} is currently '{status_display}'. Move it to 'In Progress'?",
            default=True,
        )

    def confirm_grow(count: int) -> bool:
        return typer.confirm(
            f"All {count} workspaces are in use. Add another workspace?", default=True
        )

    try:
        result = create_task_from_jira(select_issue, confirm_transition, confirm_grow)
    except (ConfigError, TaskError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if result is None:
        typer.echo("Cancelled.")
        return

    _print_task_created(result)


def _prompt_from_list(label: str, options: tuple[str, ...], default: str) -> str:
    """Show a numbered list of `options` and return the user's pick (or `default`)."""
    default_index = options.index(default) + 1 if default in options else 1
    typer.echo(f"{label}:")
    for position, option in enumerate(options, start=1):
        marker = " (default)" if position == default_index else ""
        typer.echo(f"  {position}. {option}{marker}")

    choice = typer.prompt("Pick a number", default=str(default_index))
    try:
        selected = int(choice)
    except ValueError:
        selected = default_index
    if selected < 1 or selected > len(options):
        selected = default_index
    return options[selected - 1]


_COMPONENT_COLUMNS = 3


def _prompt_component(components: list[JiraComponent]) -> JiraComponent | None:
    """Show `components` as a numbered, 3-column list and return the user's pick (or None)."""
    labels = ["0. (none)"] + [
        f"{position}. {component.name}" for position, component in enumerate(components, start=1)
    ]
    column_width = max(len(label) for label in labels) + 2

    typer.echo("Components:")
    for row_start in range(0, len(labels), _COMPONENT_COLUMNS):
        row = labels[row_start : row_start + _COMPONENT_COLUMNS]
        typer.echo("".join(label.ljust(column_width) for label in row))

    choice = typer.prompt("Pick a component number", default="0")
    try:
        selected = int(choice)
    except ValueError:
        selected = 0
    if selected < 1 or selected > len(components):
        return None
    return components[selected - 1]


def _confirm_create(fields: list[tuple[str, str]]) -> bool:
    """Print a summary of the issue about to be created and ask for confirmation."""
    typer.echo("\nAbout to create this issue:")
    for label, value in fields:
        typer.echo(f"  {label}: {value}")
    return typer.confirm("Proceed?", default=True)


@jira_app.command("create")
def jira_create(
    github_url: str | None = typer.Argument(
        None,
        help="Optional GitHub issue/PR URL (e.g. .../issues/263 or .../pull/410) to "
        "pre-fill the summary/description from.",
    ),
) -> None:
    """Create a new Jira issue interactively.

    Prompts for the issue type (Bug/Story/Task, default Story — defaults
    to Bug when GITHUB_URL points at an issue labeled "bug"), summary
    (pre-filled from the GitHub issue/PR title when given), whether to
    assign it to yourself (default yes), priority (default Medium), a
    component from the project's component list, whether to add it to the
    project's current sprint (default yes), an estimate in hours, and an
    optional description (pre-filled from the GitHub issue/PR body when
    given). Requires `jira.project_key` (and Jira credentials) in the
    config file. Shows a summary of the issue and asks for confirmation
    before creating it.
    """

    def prompt_type(default: str) -> str:
        return _prompt_from_list("Type", ISSUE_TYPES, default)

    def prompt_summary(default: str) -> str:
        if default:
            return str(typer.prompt("Summary", default=default))
        return str(typer.prompt("Summary"))

    def confirm_assign_self() -> bool:
        return typer.confirm("Assign to yourself?", default=True)

    def prompt_priority(default: str) -> str:
        return _prompt_from_list("Priority", PRIORITIES, default)

    def confirm_sprint() -> bool:
        return typer.confirm("Add to the current sprint?", default=True)

    def prompt_estimate_hours() -> str:
        return str(typer.prompt("Estimate in hours (optional)", default=""))

    def prompt_description(default: str) -> str:
        return str(typer.prompt("Description (optional)", default=default))

    def warn(message: str) -> None:
        typer.echo(f"Warning: {message}", err=True)

    prompts = IssueDraftPrompts(
        prompt_type=prompt_type,
        prompt_summary=prompt_summary,
        confirm_assign_self=confirm_assign_self,
        prompt_priority=prompt_priority,
        select_component=_prompt_component,
        confirm_sprint=confirm_sprint,
        prompt_estimate_hours=prompt_estimate_hours,
        prompt_description=prompt_description,
        confirm_create=_confirm_create,
        warn=warn,
    )

    try:
        result = create_issue_interactive(prompts, github_url)
    except (ConfigError, JiraCreateError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    if result is None:
        typer.echo("Cancelled.")
        return

    typer.echo(f"Created {_hyperlink(result.key, result.url)}")


@jira_app.command("log-time")
def jira_log_time(
    hours: str | None = typer.Argument(
        None,
        metavar="[Xh]",
        help='Manually log this many hours (e.g. "2h" or "1.5h") instead of reading the '
        "Tracker timer's elapsed time.",
    ),
    comment: str | None = typer.Option(
        None, "--comment", "-m", help="Worklog description/comment to attach in Jira."
    ),
    no_reset: bool = typer.Option(
        False,
        "--no-reset",
        help="Don't reset the tracker after logging (leaves its elapsed time as-is). "
        "Only meaningful without a manual [Xh] duration.",
    ),
    jira_key: str | None = _jira_key_option(),
) -> None:
    """Log the active task's tracked time to its Jira issue.

    With no argument, reads the elapsed time from the ET-<n> Tracker timer
    bound to the active workspace, logs it as a Jira worklog for the linked
    issue (via Jira's own worklog API, which also shows up in Tempo
    timesheets when Tempo is configured to sync native Jira worklogs), and
    resets the tracker to 0 afterwards (unless --no-reset is given).

    Given an [Xh] duration (e.g. "et jira log-time 2h"), logs that duration
    instead, without reading or resetting the Tracker timer at all.
    """
    if hours is not None and no_reset:
        typer.echo(
            "Error: --no-reset only applies when logging the Tracker timer's elapsed "
            "time, not a manual [Xh] duration",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        if hours is not None:
            seconds = parse_hours_to_seconds(hours)
            result = log_manual_time_for_current_workspace(
                seconds, description=comment, issue_key=jira_key
            )
        else:
            result = log_time_for_current_workspace(
                description=comment, reset=not no_reset, issue_key=jira_key
            )
    except (ConfigError, WorkspaceError, JiraLogTimeError, ValueError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    duration = format_duration(result.seconds_logged)
    typer.echo(
        f"Logged {duration} to {_jira_ref_link(result.issue_key)} "
        f"(workspace {result.workspace_index + 1})"
    )
    if result.tracker_reset:
        typer.echo("Reset tracker to 0")


@jira_app.command("complete")
def jira_complete(
    comment: str | None = typer.Option(
        None, "--comment", "-m", help="Worklog description/comment to attach in Jira."
    ),
    jira_key: str | None = _jira_key_option(),
) -> None:
    """Log the active task's tracked time to Jira, then optionally clean up.

    Logs the active workspace's tracked time to its Jira issue (like `et
    jira log-time`), tells you how much was logged, then asks whether to
    delete the workspace (reclaiming its GNOME workspace slot) and whether
    to move the linked Jira issue to "Done". Both actions are skipped unless
    confirmed.
    """

    def on_logged(result: LogTimeResult) -> None:
        duration = format_duration(result.seconds_logged)
        typer.echo(
            f"Logged {duration} to {_jira_ref_link(result.issue_key)} "
            f"(workspace {result.workspace_index + 1})"
        )

    def confirm_delete(result: LogTimeResult) -> bool:
        return typer.confirm(f"Delete workspace {result.workspace_index + 1}?")

    def confirm_done(result: LogTimeResult) -> bool:
        return typer.confirm(f"Move {_jira_key_link(result.issue_key)} to 'Done'?")

    try:
        result = complete_task_for_current_workspace(
            comment=comment,
            issue_key=jira_key,
            on_logged=on_logged,
            confirm_delete=confirm_delete,
            confirm_done=confirm_done,
        )
    except (ConfigError, WorkspaceError, JiraLogTimeError, TaskError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    workspace_number = result.log_result.workspace_index + 1
    if result.workspace_freed:
        typer.echo(f"Deleted workspace {workspace_number}")
    if result.moved_to_done:
        typer.echo(f"Moved {_jira_key_link(result.log_result.issue_key)} to 'Done'")


@jira_app.command("comment")
def jira_comment(
    message: str | None = typer.Argument(
        None, help="Comment text to add to the Jira issue."
    ),
    jira_key: str | None = _jira_key_option(),
) -> None:
    """Add a comment to the current task's linked Jira issue.

    Defaults to the Jira issue linked to the active workspace; pass
    -j/--jira to comment on a different issue instead. Prompts for the
    message if not given as an argument.
    """
    if message is None:
        message = typer.prompt("Comment")

    try:
        issue_key = add_comment_to_current_workspace(message, issue_key=jira_key)
    except (ConfigError, WorkspaceError, JiraLogTimeError, TaskError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error

    typer.echo(f"Added comment to {_jira_key_link(issue_key)}")


# Direct-argument shortcuts for `et jira status`: CLI-facing value -> the
# internal target status string `set_status_for_current_workspace` expects.
_STATUS_SHORTCUTS = {"in-progress": IN_PROGRESS_STATUS, "blocked": BLOCKED_STATUS}
_STATUS_LABELS = {"in-progress": "In Progress", "blocked": "Blocked"}


@jira_app.command("status")
def jira_status(
    status: str | None = typer.Argument(
        None,
        help='Target status: "in-progress" or "blocked". Omit to pick interactively from '
        "the full workflow list.",
    ),
    jira_key: str | None = _jira_key_option(),
) -> None:
    """Move the active task's linked Jira issue to a new status.

    Given "in-progress" or "blocked", transitions the linked issue
    immediately (no confirmation). With no argument, shows the ticket's
    current status and a numbered list of the team's workflow statuses
    (Untriaged through Done/Rejected) to choose from.
    """
    try:
        if status is not None:
            normalized = status.strip().lower()
            if normalized not in _STATUS_SHORTCUTS:
                choices = ", ".join(_STATUS_SHORTCUTS)
                typer.echo(f"Error: status must be one of: {choices}", err=True)
                raise typer.Exit(code=1)
            issue_key = set_status_for_current_workspace(
                _STATUS_SHORTCUTS[normalized], issue_key=jira_key
            )
            typer.echo(f"Moved {_jira_key_link(issue_key)} to '{_STATUS_LABELS[normalized]}'")
            return

        issue_key, current_status = get_current_status_for_current_workspace(
            issue_key=jira_key
        )
        typer.echo(f"{_jira_key_link(issue_key)} is currently: {current_status}")
        for index, name in enumerate(STATUS_WORKFLOW_ORDER, start=1):
            typer.echo(f"  {index}. {name}")

        choice = typer.prompt("Choose a status (blank to cancel)", default="", show_default=False)
        choice = choice.strip()
        if not choice:
            typer.echo("Cancelled.")
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(STATUS_WORKFLOW_ORDER)):
            typer.echo("Error: invalid choice", err=True)
            raise typer.Exit(code=1)

        target_status = STATUS_WORKFLOW_ORDER[int(choice) - 1]
        issue_key = set_status_for_current_workspace(target_status, issue_key=jira_key)
        typer.echo(f"Moved {_jira_key_link(issue_key)} to '{target_status}'")
    except (ConfigError, WorkspaceError, JiraLogTimeError, TaskError) as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error
