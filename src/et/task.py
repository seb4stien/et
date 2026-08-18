"""Orchestrates the `et jira` command group: a friendlier, task-centric
layer on top of the Tracker and Jira integrations (`et.tracker`,
`et.jira`, `et.jira_ref`, `et.jira_time`), plus `et ws`.

`et jira start` allocates a free workspace slot among GNOME's fixed set of
workspaces (prompting, via an injected `confirm_grow` callback, to add one
more workspace when they're all taken), creates its Tracker timer, and
switches GNOME to it — moving the terminal window it's run from along with
it — picking the slot's name/description/Jira link from the user's active
Jira issues (and offering to move the selected issue to "In Progress" if it
isn't already). `et jira complete` logs the active workspace's tracked time
to Jira (reusing `et.jira_time.log_time_for_current_workspace`), then — each
behind its own confirmation prompt — optionally deletes that workspace
(reclaiming its GNOME workspace slot the same way `et ws delete` does:
shrinking GNOME's workspace count and shifting every non-`static` slot
after it one slot to the left, moving each one's Tracker timer along with
it, then switching to a surviving workspace) and/or moves the linked Jira
issue to "Done". `et jira status`/`et jira comment` reuse the same
active-workspace-to-issue-key resolution (`et.jira_time.resolve_issue_key`,
or `resolve_active_issue` when the workspace index is also needed)
to transition the linked issue's status or add a comment to it. Every
current-ticket action also accepts an `issue_key` override (`et jira`'s
`-j/--jira KEY`), letting it target a different Jira issue than the one
linked to the active workspace. Has no
Typer/CLI dependency.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from et import tracker, workspaces
from et.config import (
    ConfigError,
    EtConfig,
    JiraConfig,
    WorkspaceConfigEntry,
    load_config,
    save_config,
)
from et.jira import (
    JiraError,
    JiraIssue,
    create_comment,
    fetch_active_issues,
    fetch_issue_status,
    fetch_transitions,
    transition_issue,
)
from et.jira_ref import JIRA_REF_PREFIX, default_entry, jira_key_from_ref, truncate_summary
from et.jira_time import LogTimeResult, log_time_for_current_workspace, resolve_issue_key
from et.tracker import TrackerError, find_timer_for_workspace
from et.workspaces import WorkspaceError
from et.ws import WsDeleteError, delete_active_workspace

IN_PROGRESS_STATUS = "in progress"
DONE_STATUS = "done"
BLOCKED_STATUS = "blocked"

# Hardcoded, workflow-ordered (todo -> done) list of statuses offered by
# `et jira status`'s interactive (no-argument) prompt. Kept as a fixed list
# rather than fetched live from Jira per the team's fixed workflow shape;
# transitioning still validates against the live `fetch_transitions` API.
STATUS_WORKFLOW_ORDER = [
    "Untriaged",
    "Triaged",
    "In Progress",
    "Blocked",
    "In Review",
    "To Be Deployed",
    "Done",
    "Rejected",
]


class TaskError(RuntimeError):
    """Raised when a task cannot be created or completed."""


@dataclass(frozen=True)
class TaskCreateResult:
    """Summary of what `create_task_workspace` did."""

    workspace_index: int
    name: str
    ref: str | None
    timer_created: bool
    window_moved: bool


@dataclass(frozen=True)
class TaskCompleteResult:
    """Summary of what `complete_task_for_current_workspace` did."""

    log_result: LogTimeResult
    workspace_freed: bool
    moved_to_done: bool


def _find_free_slot(workspaces_list: list[WorkspaceConfigEntry], count: int) -> int | None:
    """Return the first free (non-static, ref-less) slot within `count` workspaces.

    Only considers slots `0..count-1` (the workspaces GNOME actually has).
    Slots past the end of `workspaces_list` but still within `count` count as
    free bare slots. Returns None when all `count` workspaces are taken.
    """
    for index, entry in enumerate(workspaces_list[:count]):
        if entry.type != "static" and entry.ref is None:
            return index
    if len(workspaces_list) < count:
        return len(workspaces_list)
    return None


def create_task_workspace(
    name: str,
    description: str | None = None,
    ref: str | None = None,
    *,
    confirm_grow: Callable[[int], bool] = lambda count: False,
) -> TaskCreateResult | None:
    """Allocate a free workspace slot for a new task, name it, and switch to it.

    Picks the first non-`static`, unlinked slot among the fixed set of GNOME
    workspaces (`num-workspaces`). If every workspace is already taken, calls
    `confirm_grow(count)`; when it returns True the workspace count is bumped
    by one (via `set_workspace_count`) and the new slot is used, otherwise
    this returns `None` without changing anything. Saves the updated config,
    creates the slot's Tracker timer, renames the GNOME workspaces to match,
    switches the active GNOME workspace to the new slot, and best-effort moves
    the currently focused window (typically the terminal the command was run
    from) there too — this last step is skipped without failing the whole
    command if unsupported (e.g. for native Wayland clients, which have no
    addressable X11 window for `wmctrl` to move); `TaskCreateResult.window_moved`
    reports whether it worked.

    Raises `ConfigError` if the config file is missing/malformed, and
    `WorkspaceError`/`TrackerError` (via `TaskError`) if the underlying
    GNOME/Tracker operations fail.
    """
    config: EtConfig = load_config()

    try:
        count = workspaces.get_workspace_count()
    except WorkspaceError as exc:
        raise TaskError(str(exc)) from exc

    slot = _find_free_slot(config.workspaces, count)
    grew = False
    if slot is None:
        if not confirm_grow(count):
            return None
        slot = count
        grew = True

    workspaces_list = list(config.workspaces)
    while len(workspaces_list) <= slot:
        workspaces_list.append(default_entry(len(workspaces_list), "dynamic"))

    workspaces_list[slot] = WorkspaceConfigEntry(
        name=name,
        type=workspaces_list[slot].type,
        ref=ref,
        description=description,
    )

    try:
        if grew:
            workspaces.set_workspace_count(count + 1)
        timer_created = tracker.prepare_timer_for_workspace(
            slot, count + 1 if grew else count
        )[1]
        save_config(replace(config, workspaces=workspaces_list))
        workspaces.rename_all_workspaces([entry.name for entry in workspaces_list])
        workspaces.switch_to_workspace(slot)
    except (WorkspaceError, TrackerError, ConfigError) as exc:
        raise TaskError(str(exc)) from exc

    # Best-effort: moving the focused window across workspaces requires an
    # addressable X11 window, which native Wayland clients (e.g. many
    # terminal emulators under GNOME/Wayland) don't have. Don't fail task
    # creation — which has already fully succeeded at this point — just
    # because this cosmetic step isn't supported in the current session.
    try:
        workspaces.move_active_window_to_workspace(slot)
        window_moved = True
    except WorkspaceError:
        window_moved = False

    return TaskCreateResult(
        workspace_index=slot,
        name=name,
        ref=ref,
        timer_created=timer_created,
        window_moved=window_moved,
    )



def _transition_to_status(
    jira_config: JiraConfig, issue_key: str, target_status: str, *, display: str
) -> None:
    """Move `issue_key` to the transition whose target status is `target_status`.

    `target_status` is matched case-insensitively against each available
    transition's destination status; `display` is the human-readable label
    used in the error message. Raises `TaskError` if no such transition
    exists, or (via `JiraError`) if the Jira API call fails.
    """
    try:
        transitions = fetch_transitions(jira_config, issue_key)
    except JiraError as exc:
        raise TaskError(str(exc)) from exc

    normalized = target_status.strip().lower()
    target = next(
        (t for t in transitions if t.to_status.strip().lower() == normalized), None
    )
    if target is None:
        raise TaskError(f"no transition to '{display}' available for {issue_key}")

    try:
        transition_issue(jira_config, issue_key, target.id)
    except JiraError as exc:
        raise TaskError(str(exc)) from exc


def set_status_for_current_workspace(status: str, *, issue_key: str | None = None) -> str:
    """Move the linked Jira issue to `status`.

    Resolves the issue to act on the same way `et jira log-time` does
    (the active workspace's linked issue, or `issue_key` if given, e.g. via
    `-j/--jira`), then transitions it to the workflow transition whose
    destination status matches `status` (case-insensitively) via
    `_transition_to_status`. Returns the issue key, so the caller can
    report which issue was changed.

    Raises `ConfigError` if the config file is missing/malformed,
    `WorkspaceError` if the active workspace can't be determined (only when
    `issue_key` isn't given), `JiraLogTimeError` (from
    `resolve_issue_key`) if there's no 'jira' config block or no issue
    linked to the active workspace, and `TaskError` if no matching
    transition exists or the Jira API call fails.
    """
    config: EtConfig = load_config()
    jira_config, resolved_key = resolve_issue_key(config, issue_key=issue_key)

    _transition_to_status(jira_config, resolved_key, status, display=status)
    return resolved_key


def get_current_status_for_current_workspace(*, issue_key: str | None = None) -> tuple[str, str]:
    """Return (issue_key, current_status) for the issue to act on.

    Used by `et jira status`'s interactive (no-argument) flow to display
    the ticket's current status before prompting for a new one. Resolves
    the issue the same way `set_status_for_current_workspace` does (the
    active workspace's linked issue, or `issue_key` if given).

    Raises `ConfigError`, `WorkspaceError`, or `JiraLogTimeError` under the
    same conditions as `set_status_for_current_workspace`, and `TaskError`
    if the Jira API call fails.
    """
    config: EtConfig = load_config()
    jira_config, resolved_key = resolve_issue_key(config, issue_key=issue_key)

    try:
        status = fetch_issue_status(jira_config, resolved_key)
    except JiraError as exc:
        raise TaskError(str(exc)) from exc

    return resolved_key, status


def add_comment_to_current_workspace(body: str, *, issue_key: str | None = None) -> str:
    """Add a comment to `issue_key`, or the active workspace's linked issue if omitted.

    Resolves the issue to comment on the same way `set_status_for_current_workspace`
    does (no workspace resolution at all when `issue_key` is given, e.g. via
    `-j/--jira`, so this works even without an active linked workspace).
    Returns the issue key the comment was added to.

    Raises `ConfigError` if the config file is missing/malformed,
    `WorkspaceError` if the active workspace can't be determined (only when
    `issue_key` isn't given), `JiraLogTimeError` (from
    `resolve_issue_key`) if there's no 'jira' config block or no issue
    linked to the active workspace, and `TaskError` if the Jira API call
    fails.
    """
    config: EtConfig = load_config()
    jira_config, resolved_key = resolve_issue_key(config, issue_key=issue_key)

    try:
        create_comment(jira_config, resolved_key, body)
    except JiraError as exc:
        raise TaskError(str(exc)) from exc

    return resolved_key


def create_task_from_jira(
    select_issue: Callable[[list[JiraIssue]], JiraIssue | None],
    confirm_transition: Callable[[JiraIssue], bool] | None = None,
    confirm_grow: Callable[[int], bool] = lambda count: False,
) -> TaskCreateResult | None:
    """Create a task workspace from one of the user's active Jira issues.

    Fetches active Jira issues, filters out any already linked to an
    existing workspace, then calls `select_issue(candidates)` with the
    remaining ones — `select_issue` is responsible for presenting the
    choice to the user (and returns `None` to cancel, in which case this
    returns `None` without changing anything).

    If the selected issue isn't already "In Progress" and `confirm_transition`
    is given, calls `confirm_transition(issue)` — if it returns `True`, the
    issue is moved to its "In Progress" transition before the workspace is
    created.

    `confirm_grow(count)` is forwarded to `create_task_workspace`: it's
    called only when all `count` workspaces are taken, to ask whether to add
    one more (returning `None` here if declined).

    Raises `ConfigError` if the config file is missing/malformed,
    `TaskError` if there's no `jira` config block, and `JiraError` (via
    `TaskError`) if the Jira API call fails.
    """
    config: EtConfig = load_config()
    if config.jira is None:
        raise TaskError(
            "no 'jira' block found in the config file "
            "(add base_url/email/pat/jql under a top-level 'jira:' key)"
        )

    try:
        issues = fetch_active_issues(config.jira)
    except JiraError as exc:
        raise TaskError(str(exc)) from exc

    tracked_keys = {
        jira_key_from_ref(entry.ref)
        for entry in config.workspaces
        if jira_key_from_ref(entry.ref) is not None
    }
    candidates = [issue for issue in issues if issue.key not in tracked_keys]

    issue = select_issue(candidates)
    if issue is None:
        return None

    if (
        issue.status.strip().lower() != IN_PROGRESS_STATUS
        and confirm_transition is not None
        and confirm_transition(issue)
    ):
        _transition_to_status(config.jira, issue.key, IN_PROGRESS_STATUS, display="In Progress")

    return create_task_workspace(
        name=truncate_summary(issue.summary),
        description=issue.summary,
        ref=f"{JIRA_REF_PREFIX}{issue.key}",
        confirm_grow=confirm_grow,
    )


def _free_workspace_slot(index: int) -> None:
    """Delete the completed workspace, reclaiming its GNOME workspace slot.

    Delegates to `delete_active_workspace` — the same logic `et ws delete`
    uses — so the workspace is actually removed rather than merely blanked:
    GNOME's workspace count is decremented, every non-`static` slot after
    the freed one (and its Tracker timer) shifts one slot to the left to
    close the gap, and GNOME switches to a surviving workspace. `force=True`
    because the slot is still linked to the just-completed Jira issue (whose
    Tracker timer was already reset to zero by the logging step, so
    discarding it loses nothing).

    Falls back to resetting the slot to a bare "ET-<n>" entry (without
    touching GNOME's workspace count) when there is only a single workspace
    left, since GNOME can't drop below one workspace.

    Raises `TaskError` if the underlying config/tracker/GNOME operations
    fail.
    """
    try:
        count = workspaces.get_workspace_count()
    except WorkspaceError as exc:
        raise TaskError(str(exc)) from exc

    if count <= 1:
        _reset_workspace_slot(index)
        return

    try:
        delete_active_workspace(force=True)
    except (ConfigError, WorkspaceError, WsDeleteError) as exc:
        raise TaskError(str(exc)) from exc


def _reset_workspace_slot(index: int) -> None:
    """Reset workspace `index` to a bare "ET-<n>" slot and drop its timer.

    Used when the workspace can't be reclaimed by shrinking GNOME's
    workspace count (only one workspace remains): clears the slot's
    name/ref/description and discards its (already-reset) Tracker timer,
    leaving GNOME's workspace count untouched.

    Raises `TaskError` if the underlying config/tracker operations fail.
    """
    config: EtConfig = load_config()
    workspaces_list = list(config.workspaces)
    if index < len(workspaces_list):
        workspaces_list[index] = default_entry(index, workspaces_list[index].type)

    try:
        entries = tracker.load_timers()
        stale_timer = find_timer_for_workspace(entries, index)
        if stale_timer is not None:
            entries.remove(stale_timer)
            tracker.save_timers_with_reload(entries, "clearing timer after completing a task")
        save_config(replace(config, workspaces=workspaces_list))
        workspaces.rename_all_workspaces([entry.name for entry in workspaces_list])
    except (ConfigError, WorkspaceError, TrackerError) as exc:
        raise TaskError(str(exc)) from exc


def complete_task_for_current_workspace(
    comment: str | None = None,
    *,
    issue_key: str | None = None,
    on_logged: Callable[[LogTimeResult], None] = lambda result: None,
    confirm_delete: Callable[[LogTimeResult], bool] = lambda result: False,
    confirm_done: Callable[[LogTimeResult], bool] = lambda result: False,
) -> TaskCompleteResult:
    """Log the active workspace's tracked time to Jira, then optionally clean up.

    Delegates the logging step to
    `et.jira_time.log_time_for_current_workspace` (which already resets the
    tracker on success) and, once it succeeds, calls `on_logged(log_result)`
    so the caller can report the logged time before any prompts. Logs
    against `issue_key` if given (e.g. via `-j/--jira`), instead of the
    issue linked to the active workspace — workspace deletion still applies
    to the active workspace regardless, since it's not tied to which issue
    the time was logged against.

    Then `confirm_delete(log_result)` is called: if it returns `True`, the
    workspace is deleted the same way `et ws delete` does — GNOME's
    workspace count is decremented to reclaim the slot, every non-`static`
    slot after it (with its Tracker timer) shifts one slot to the left to
    close the gap, GNOME workspace names are renamed to match, and GNOME
    switches to a surviving workspace. When only a single workspace remains
    (so GNOME can't shrink further) the slot is just reset to a bare
    "ET-<n>" entry instead. If it returns `False`, the workspace is left
    as-is.

    Finally `confirm_done(log_result)` is called: if it returns `True`, the
    linked Jira issue is moved through its "Done" transition.

    Raises `ConfigError`, `WorkspaceError`, or `JiraLogTimeError` (all
    propagated unchanged from `log_time_for_current_workspace`) if logging
    fails — in which case the workspace and issue are left untouched. Raises
    `TaskError` if freeing the workspace or transitioning the issue fails.
    """
    log_result = log_time_for_current_workspace(
        description=comment, reset=True, issue_key=issue_key
    )
    on_logged(log_result)

    workspace_freed = False
    if confirm_delete(log_result):
        _free_workspace_slot(log_result.workspace_index)
        workspace_freed = True

    moved_to_done = False
    if confirm_done(log_result):
        config: EtConfig = load_config()
        if config.jira is None:
            raise TaskError(
                "no 'jira' block found in the config file "
                "(add base_url/email/pat/jql under a top-level 'jira:' key)"
            )
        _transition_to_status(config.jira, log_result.issue_key, DONE_STATUS, display="Done")
        moved_to_done = True

    return TaskCompleteResult(
        log_result=log_result,
        workspace_freed=workspace_freed,
        moved_to_done=moved_to_done,
    )


__all__ = [
    "TaskError",
    "TaskCreateResult",
    "TaskCompleteResult",
    "create_task_workspace",
    "create_task_from_jira",
    "complete_task_for_current_workspace",
    "set_status_for_current_workspace",
    "get_current_status_for_current_workspace",
    "add_comment_to_current_workspace",
]
