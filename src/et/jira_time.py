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
all. Has no Typer/CLI dependency.
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


def resolve_active_issue(config: EtConfig) -> tuple[JiraConfig, int, str]:
    """Return the active workspace's (jira_config, workspace_index, issue_key).

    Raises `JiraLogTimeError` if there's no 'jira' config block, or no Jira
    issue is linked to the active workspace (via a "jira:<KEY>" ref).
    Shared by every current-workspace Jira action (log-time, comment,
    status).
    """
    if config.jira is None:
        raise JiraLogTimeError(
            "no 'jira' block found in the config file "
            "(add base_url/email/pat/jql under a top-level 'jira:' key)"
        )

    index = workspaces.get_active_workspace_index()
    entry = config.workspaces[index] if index < len(config.workspaces) else None
    issue_key = jira_key_from_ref(entry.ref) if entry else None
    if issue_key is None:
        raise JiraLogTimeError(f"no Jira issue linked to workspace {index + 1}")

    return config.jira, index, issue_key


def log_time_for_current_workspace(
    *, description: str | None = None, reset: bool = True
) -> LogTimeResult:
    """Log the active workspace's ET-<n> tracker elapsed time to its Jira issue.

    Raises `ConfigError` if the config file is missing/malformed,
    `WorkspaceError` if the active workspace can't be determined, and
    `JiraLogTimeError` if: there's no 'jira' config block, no Jira issue is
    linked to the active workspace, no Tracker timer is bound to it (or its
    elapsed time is under `MIN_LOGGABLE_SECONDS`), or the Jira API call
    fails.

    On success, resets the tracker to 0 (unless `reset=False`) so the same
    elapsed time isn't accidentally logged again later.
    """
    config: EtConfig = load_config()
    jira_config, index, issue_key = resolve_active_issue(config)

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
        create_worklog(jira_config, issue_key, seconds, comment=description)
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
        issue_key=issue_key,
        seconds_logged=seconds,
        tracker_reset=reset,
    )


def log_manual_time_for_current_workspace(
    seconds: int, *, description: str | None = None
) -> LogTimeResult:
    """Log a manually-specified `seconds` duration to the active workspace's Jira issue.

    Like `log_time_for_current_workspace`, but the duration comes from the
    caller (e.g. `et jira log-time 2h`) instead of the Tracker timer's
    elapsed time — so the Tracker timer is never read or reset. Raises
    `ConfigError`/`WorkspaceError`/`JiraLogTimeError` under the same
    conditions as `log_time_for_current_workspace` (minus the
    Tracker-timer-related ones).
    """
    config: EtConfig = load_config()
    jira_config, index, issue_key = resolve_active_issue(config)

    try:
        create_worklog(jira_config, issue_key, seconds, comment=description)
    except JiraError as exc:
        raise JiraLogTimeError(str(exc)) from exc

    return LogTimeResult(
        workspace_index=index,
        issue_key=issue_key,
        seconds_logged=seconds,
        tracker_reset=False,
    )
