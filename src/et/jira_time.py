"""Orchestrates `et jira log-time`: log the active workspace's Tracker time
to its linked Jira issue.

Reads the ET-<n> Tracker timer bound to the active workspace, resolves the
Jira issue linked to that workspace (its "jira:<KEY>" ref, normally set by
`et jira start`), and logs the elapsed time as a Jira worklog for that issue
(via `et.jira.create_worklog` — Jira's own worklog API, which still shows up
in Tempo timesheets when Tempo is configured to sync native Jira worklogs,
without needing a separate Tempo API token). On success the timer is reset
to 0 (unless disabled), since that same elapsed time has just been recorded
and shouldn't be logged again next time. Also exposes
`log_manual_time_for_current_workspace`, used by `et jira log-time [Xh]` to
log a manually-specified duration without touching the Tracker timer at
all, and `log_time_for_all_workspaces`, used by `et jira log-time --all` to
log every Jira-linked workspace's tracker in one go regardless of which
workspace is active. Has no Typer/CLI dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

from et import tracker, workspaces
from et.config import EtConfig, JiraConfig, load_config
from et.jira import JiraError, create_worklog
from et.jira_ref import jira_key_from_ref
from et.tracker import TrackerError

# Below this, the elapsed time is almost certainly just a stray few seconds
# (e.g. a timer left running by accident) rather than real tracked work.
MIN_LOGGABLE_SECONDS = 60


class JiraLogTimeError(RuntimeError):
    """Raised when the active workspace's tracked time cannot be logged to Jira."""


@dataclass(frozen=True)
class LogTimeResult:
    """Summary of what `log_time_for_current_workspace` did."""

    workspace_index: int
    issue_key: str
    seconds_logged: int
    tracker_reset: bool


@dataclass(frozen=True)
class SkippedWorkspace:
    """A Jira-linked workspace that `log_time_for_all_workspaces` didn't log."""

    workspace_index: int
    issue_key: str
    reason: str


@dataclass(frozen=True)
class AllLogTimeResult:
    """Summary of what `log_time_for_all_workspaces` did, across every workspace."""

    logged: list[LogTimeResult]
    skipped: list[SkippedWorkspace]


def resolve_issue_key(config: EtConfig, *, issue_key: str | None = None) -> tuple[JiraConfig, str]:
    """Return (jira_config, issue_key) to act against, without requiring an active workspace.

    When `issue_key` is given (e.g. from `et jira`'s `-j/--jira KEY`
    override), it's used directly — no GNOME/workspace lookup happens at
    all, so this works even outside a managed workspace. Otherwise falls
    back to `resolve_active_issue`, which resolves the active workspace's
    linked issue. Used by actions that don't need the workspace index
    itself (comment, status) — `log-time`/`complete` use
    `resolve_active_issue` directly since they also need the index (for
    the Tracker timer / workspace deletion).

    Raises `JiraLogTimeError` if there's no 'jira' config block, or (when
    `issue_key` isn't given) no Jira issue is linked to the active
    workspace.
    """
    if issue_key is not None:
        if config.jira is None:
            raise JiraLogTimeError(
                "no 'jira' block found in the config file "
                "(add base_url/email/pat/jql under a top-level 'jira:' key)"
            )
        return config.jira, issue_key

    jira_config, _, resolved_key = resolve_active_issue(config)
    return jira_config, resolved_key


def resolve_active_issue(
    config: EtConfig, *, issue_key: str | None = None
) -> tuple[JiraConfig, int, str]:
    """Return (jira_config, workspace_index, issue_key) to act against.

    When `issue_key` is given, it's used directly (e.g. from `et jira`'s
    `-j/--jira KEY` override) — the active workspace's linked ref is never
    consulted for the issue key, only for its index (still needed by
    Tracker-timer-based actions like `log-time`). Otherwise the active
    workspace's linked issue ("jira:<KEY>" ref) is resolved as before.

    Raises `JiraLogTimeError` if there's no 'jira' config block, or (when
    `issue_key` isn't given) no Jira issue is linked to the active
    workspace. Shared by every current-workspace Jira action (log-time,
    comment, status, complete).
    """
    if config.jira is None:
        raise JiraLogTimeError(
            "no 'jira' block found in the config file "
            "(add base_url/email/pat/jql under a top-level 'jira:' key)"
        )

    index = workspaces.get_active_workspace_index()
    if issue_key is not None:
        return config.jira, index, issue_key

    entry = config.workspaces[index] if index < len(config.workspaces) else None
    resolved_key = jira_key_from_ref(entry.ref) if entry else None
    if resolved_key is None:
        raise JiraLogTimeError(f"no Jira issue linked to workspace {index + 1}")

    return config.jira, index, resolved_key


def log_time_for_current_workspace(
    *, description: str | None = None, reset: bool = True, issue_key: str | None = None
) -> LogTimeResult:
    """Log the active workspace's ET-<n> tracker elapsed time to its Jira issue.

    Logs against `issue_key` if given (e.g. via `-j/--jira`), instead of the
    issue linked to the active workspace — the Tracker timer read/reset
    still applies to the active workspace regardless.

    Raises `ConfigError` if the config file is missing/malformed,
    `WorkspaceError` if the active workspace can't be determined, and
    `JiraLogTimeError` if: there's no 'jira' config block, no Jira issue is
    linked to the active workspace (and none was given explicitly), no
    Tracker timer is bound to it (or its elapsed time is under
    `MIN_LOGGABLE_SECONDS`), or the Jira API call fails.

    On success, resets the tracker to 0 (unless `reset=False`) so the same
    elapsed time isn't accidentally logged again later.
    """
    config: EtConfig = load_config()
    jira_config, index, resolved_key = resolve_active_issue(config, issue_key=issue_key)

    entries = tracker.load_timers()
    timer = tracker.find_timer_for_workspace(entries, index)
    elapsed = timer.get("timeElapsed", 0) if timer is not None else 0
    seconds = int(elapsed) if isinstance(elapsed, (int, float)) else 0
    if seconds < MIN_LOGGABLE_SECONDS:
        raise JiraLogTimeError(
            f"workspace {index + 1}'s tracker has only {seconds}s elapsed "
            f"(minimum {MIN_LOGGABLE_SECONDS}s to log)"
        )

    try:
        create_worklog(jira_config, resolved_key, seconds, comment=description)
    except JiraError as exc:
        raise JiraLogTimeError(str(exc)) from exc

    if reset:
        assert timer is not None  # seconds >= MIN_LOGGABLE_SECONDS implies a timer was found
        timer["timeElapsed"] = 0
        timer["running"] = False
        try:
            tracker.save_timers_with_reload(entries, "resetting timer after logging to Jira")
        except TrackerError as exc:
            raise JiraLogTimeError(str(exc)) from exc

    return LogTimeResult(
        workspace_index=index,
        issue_key=resolved_key,
        seconds_logged=seconds,
        tracker_reset=reset,
    )


def log_manual_time_for_current_workspace(
    seconds: int, *, description: str | None = None, issue_key: str | None = None
) -> LogTimeResult:
    """Log a manually-specified `seconds` duration to the active workspace's Jira issue.

    Like `log_time_for_current_workspace`, but the duration comes from the
    caller (e.g. `et jira log-time 2h`) instead of the Tracker timer's
    elapsed time — so the Tracker timer is never read or reset. Logs
    against `issue_key` if given (e.g. via `-j/--jira`), instead of the
    issue linked to the active workspace. Raises
    `ConfigError`/`WorkspaceError`/`JiraLogTimeError` under the same
    conditions as `log_time_for_current_workspace` (minus the
    Tracker-timer-related ones).
    """
    config: EtConfig = load_config()
    jira_config, index, resolved_key = resolve_active_issue(config, issue_key=issue_key)

    try:
        create_worklog(jira_config, resolved_key, seconds, comment=description)
    except JiraError as exc:
        raise JiraLogTimeError(str(exc)) from exc

    return LogTimeResult(
        workspace_index=index,
        issue_key=resolved_key,
        seconds_logged=seconds,
        tracker_reset=False,
    )


def log_time_for_all_workspaces(
    *, description: str | None = None, reset: bool = True
) -> AllLogTimeResult:
    """Log every Jira-linked workspace's tracked time to its own Jira issue.

    Unlike `log_time_for_current_workspace`, this never consults the
    active GNOME workspace: it walks every entry in the config's
    `workspaces` list, and for each one with a linked Jira issue
    ("jira:<KEY>" ref) logs its ET-<n> Tracker timer's elapsed time to that
    issue. Workspaces with no linked issue are ignored entirely (not even
    reported).

    A workspace is skipped (recorded in `AllLogTimeResult.skipped`, not
    raised) when: it has no Tracker timer, its elapsed time is under
    `MIN_LOGGABLE_SECONDS`, or the Jira API call for that one issue fails
    — so a single bad workspace never stops the rest from being logged.
    A skipped workspace's timer is left untouched either way.

    On success for a given workspace, if `reset` is True, that workspace's
    timer is zeroed and saved immediately (one `save_timers_with_reload`
    call per successfully-logged workspace, not batched at the end) — so
    an interruption partway through, or a later workspace's Jira call
    failing, never leaves an already-logged workspace un-reset, and never
    resets a workspace whose Jira call actually failed.

    Raises `ConfigError` if the config file is missing/malformed, and
    `JiraLogTimeError` only when there's no 'jira' config block at all
    (every other failure becomes a per-workspace skip instead).
    """
    config: EtConfig = load_config()
    if config.jira is None:
        raise JiraLogTimeError(
            "no 'jira' block found in the config file "
            "(add base_url/email/pat/jql under a top-level 'jira:' key)"
        )
    jira_config = config.jira

    entries = tracker.load_timers()
    logged: list[LogTimeResult] = []
    skipped: list[SkippedWorkspace] = []

    for index, entry in enumerate(config.workspaces):
        issue_key = jira_key_from_ref(entry.ref)
        if issue_key is None:
            continue

        timer = tracker.find_timer_for_workspace(entries, index)
        elapsed = timer.get("timeElapsed", 0) if timer is not None else 0
        seconds = int(elapsed) if isinstance(elapsed, (int, float)) else 0

        if timer is None:
            skipped.append(
                SkippedWorkspace(
                    workspace_index=index,
                    issue_key=issue_key,
                    reason="no Tracker timer for this workspace",
                )
            )
            continue

        if seconds < MIN_LOGGABLE_SECONDS:
            skipped.append(
                SkippedWorkspace(
                    workspace_index=index,
                    issue_key=issue_key,
                    reason=f"only {seconds}s elapsed (minimum {MIN_LOGGABLE_SECONDS}s to log)",
                )
            )
            continue

        try:
            create_worklog(jira_config, issue_key, seconds, comment=description)
        except JiraError as exc:
            skipped.append(
                SkippedWorkspace(workspace_index=index, issue_key=issue_key, reason=str(exc))
            )
            continue

        if reset:
            timer["timeElapsed"] = 0
            timer["running"] = False
            try:
                tracker.save_timers_with_reload(
                    entries, f"resetting workspace {index + 1}'s timer after logging to Jira"
                )
            except TrackerError as exc:
                raise JiraLogTimeError(str(exc)) from exc

        logged.append(
            LogTimeResult(
                workspace_index=index,
                issue_key=issue_key,
                seconds_logged=seconds,
                tracker_reset=reset,
            )
        )

    return AllLogTimeResult(logged=logged, skipped=skipped)
