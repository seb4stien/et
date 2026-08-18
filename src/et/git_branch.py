"""Orchestrates `et git create-branch`: create a git branch named after a Jira issue.

Follows Canonical's platform-engineering-docs PR branch naming convention
(`type/scope-short-description[-identifier]`, e.g.
`feat/tcp-wildcard-sni-support-isd-1234`): the `type/` prefix comes from a
fixed list and is derived from the Jira issue's type/labels (with the user
able to override it), the middle segment is a slug of the issue's summary
(freely editable by the user), and the trailing identifier is always the
resolved Jira issue key (not editable). Shells out to `git` for the actual
repo interaction, mirroring `et.github_ref`'s use of `subprocess.run` so it
stays mockable/unit-testable without a real git repo. Has no Typer/CLI
dependency; collects user input via injected callbacks like
`et.jira_create.create_issue_interactive`.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from et.config import JiraConfig
from et.jira import JiraError, JiraIssueBasis, fetch_issue_basis

BRANCH_TYPES = ("feat", "fix", "docs", "chore", "test", "ci")

DOCUMENTATION_LABEL = "documentation"
BUG_ISSUE_TYPE = "Bug"
STORY_ISSUE_TYPE = "Story"

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")
_SLUG_REPEATED_HYPHENS = re.compile(r"-{2,}")


class GitBranchError(RuntimeError):
    """Raised when a branch can't be named or created from a Jira issue."""


@dataclass(frozen=True)
class BranchCreateResult:
    """Summary of what `create_branch_interactive` did."""

    name: str
    issue_key: str


def slugify(text: str) -> str:
    """Lowercase `text` and turn it into a hyphen-separated slug.

    Non-alphanumeric characters become hyphens, repeated hyphens collapse
    to one, and leading/trailing hyphens are stripped. Used both for the
    Jira summary (the `scope-short-description` segment) and defensively
    for the Jira key identifier.
    """
    slug = _SLUG_INVALID_CHARS.sub("-", text.lower())
    slug = _SLUG_REPEATED_HYPHENS.sub("-", slug)
    return slug.strip("-")


def default_branch_type(issue_type: str, labels: Sequence[str]) -> str:
    """Return the default branch type for an issue's type/labels.

    A `documentation` label (case-insensitive) always wins and defaults to
    `docs`, regardless of issue type. Otherwise `Bug` -> `fix`, `Story` ->
    `feat`, and anything else (e.g. `Task`) -> `chore`. The result is
    always one of `BRANCH_TYPES` and is only ever a *default*: the caller
    is expected to let the user override it.
    """
    if any(label.strip().lower() == DOCUMENTATION_LABEL for label in labels):
        return "docs"
    if issue_type == BUG_ISSUE_TYPE:
        return "fix"
    if issue_type == STORY_ISSUE_TYPE:
        return "feat"
    return "chore"


def build_branch_name(branch_type: str, description_slug: str, jira_key: str) -> str:
    """Build the final `type/scope-short-description-jirakey` branch name.

    `description_slug` is expected to already be a slug (e.g. via
    `slugify`); `jira_key` is slugified defensively so it lowercases
    cleanly regardless of the project key's casing. Raises `GitBranchError`
    if `description_slug` is empty after slugifying, or if `branch_type`
    isn't one of `BRANCH_TYPES`.
    """
    if branch_type not in BRANCH_TYPES:
        raise GitBranchError(
            f"invalid branch type {branch_type!r}, expected one of {', '.join(BRANCH_TYPES)}"
        )

    slug = slugify(description_slug)
    if not slug:
        raise GitBranchError("branch description is empty once slugified")

    return f"{branch_type}/{slug}-{slugify(jira_key)}"


def fetch_branch_basis(jira_config: JiraConfig, issue_key: str) -> JiraIssueBasis:
    """Fetch `issue_key`'s summary/type/labels, wrapping `JiraError` as `GitBranchError`."""
    try:
        return fetch_issue_basis(jira_config, issue_key)
    except JiraError as exc:
        raise GitBranchError(str(exc)) from exc


def is_inside_git_repo() -> bool:
    """Return whether the current directory is inside a git working tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def branch_exists(name: str) -> bool:
    """Return whether a local branch named `name` already exists."""
    try:
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitBranchError(f"could not check for existing branch {name!r}: {exc}") from exc
    return result.returncode == 0


def create_and_checkout_branch(name: str) -> None:
    """Create branch `name` from the current HEAD and switch to it.

    Raises `GitBranchError` if `git` isn't available or `git checkout -b`
    fails (e.g. the branch already exists, or there's no repo/HEAD).
    """
    try:
        result = subprocess.run(
            ["git", "checkout", "-b", name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitBranchError(f"could not run 'git checkout -b {name}': {exc}") from exc

    if result.returncode != 0:
        raise GitBranchError(f"'git checkout -b {name}' failed: {result.stderr.strip()}")


def create_branch_interactive(
    jira_config: JiraConfig,
    issue_key: str,
    select_type: Callable[[str], str | None],
    edit_description: Callable[[str], str | None],
    confirm_name: Callable[[str], bool] = lambda name: True,
    announce_issue: Callable[[JiraIssueBasis], None] = lambda basis: None,
) -> BranchCreateResult | None:
    """Interactively build a branch name for `issue_key` and create it.

    Fetches the issue's summary/type/labels and calls `announce_issue(basis)`
    with them straight away (e.g. so the caller can display the ticket's
    summary/link before any prompting happens). Then computes the default
    branch type and calls `select_type(default_type)` to let the caller
    accept it or offer an override (returning `None` cancels). Then calls
    `edit_description(default_slug)` with a slug of the issue summary,
    letting the caller present it as editable text (returning `None`
    cancels). The resulting `type/description-jirakey` name is passed to
    `confirm_name` for a final go/no-go (default: always proceeds), then
    the branch is created and checked out via `create_and_checkout_branch`.

    Raises `GitBranchError` if not inside a git repo, if the Jira issue
    can't be fetched, if the computed branch name already exists as a
    local branch, or if `git checkout -b` fails.
    """
    if not is_inside_git_repo():
        raise GitBranchError("not inside a git repository")

    basis = fetch_branch_basis(jira_config, issue_key)
    announce_issue(basis)
    default_type = default_branch_type(basis.issue_type, basis.labels)

    branch_type = select_type(default_type)
    if branch_type is None:
        return None

    default_slug = slugify(basis.summary)
    description_slug = edit_description(default_slug)
    if description_slug is None:
        return None

    name = build_branch_name(branch_type, description_slug, issue_key)

    if branch_exists(name):
        raise GitBranchError(f"branch {name!r} already exists")

    if not confirm_name(name):
        return None

    create_and_checkout_branch(name)
    return BranchCreateResult(name=name, issue_key=issue_key)
