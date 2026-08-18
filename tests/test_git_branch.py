"""Tests for et.git_branch, mocking subprocess.run and et.jira's Jira calls."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from et.config import JiraConfig
from et.git_branch import (
    BranchCreateResult,
    GitBranchError,
    build_branch_name,
    create_and_checkout_branch,
    create_branch_interactive,
    default_branch_type,
    is_inside_git_repo,
    slugify,
)
from et.jira import JiraError, JiraIssueBasis


def _config() -> JiraConfig:
    return JiraConfig(
        base_url="https://example.atlassian.net/",
        email="me@example.com",
        pat="secret-token",
        jql="assignee = currentUser()",
    )


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# --- slugify -----------------------------------------------------------------


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Add wildcard SNI support") == "add-wildcard-sni-support"


def test_slugify_collapses_repeated_and_strips_edge_hyphens():
    assert slugify("  Fix -- TLS/relation!! ") == "fix-tls-relation"


def test_slugify_of_empty_string_is_empty():
    assert slugify("") == ""


# --- default_branch_type ------------------------------------------------------


def test_default_branch_type_bug_is_fix():
    assert default_branch_type("Bug", []) == "fix"


def test_default_branch_type_story_is_feat():
    assert default_branch_type("Story", []) == "feat"


def test_default_branch_type_task_is_chore():
    assert default_branch_type("Task", []) == "chore"


def test_default_branch_type_unknown_issue_type_is_chore():
    assert default_branch_type("Epic", []) == "chore"


def test_default_branch_type_documentation_label_wins_over_bug():
    assert default_branch_type("Bug", ["Documentation"]) == "docs"


def test_default_branch_type_documentation_label_wins_over_story():
    assert default_branch_type("Story", ["backend", "documentation"]) == "docs"


def test_default_branch_type_documentation_label_wins_over_task():
    assert default_branch_type("Task", ["documentation"]) == "docs"


# --- build_branch_name ---------------------------------------------------------


def test_build_branch_name_joins_type_slug_and_key():
    name = build_branch_name("feat", "tcp wildcard SNI support", "ISD-1234")

    assert name == "feat/tcp-wildcard-sni-support-isd-1234"


def test_build_branch_name_rejects_invalid_type():
    with pytest.raises(GitBranchError, match="invalid branch type"):
        build_branch_name("bogus", "a description", "ISD-1234")


def test_build_branch_name_rejects_empty_description():
    with pytest.raises(GitBranchError, match="empty once slugified"):
        build_branch_name("feat", "!!!", "ISD-1234")


# --- is_inside_git_repo / create_and_checkout_branch --------------------------


@patch("et.git_branch.subprocess.run")
def test_is_inside_git_repo_true(mock_run):
    mock_run.return_value = _completed(returncode=0, stdout="true\n")

    assert is_inside_git_repo() is True


@patch("et.git_branch.subprocess.run")
def test_is_inside_git_repo_false_on_nonzero_exit(mock_run):
    mock_run.return_value = _completed(returncode=128, stderr="not a git repository")

    assert is_inside_git_repo() is False


@patch("et.git_branch.subprocess.run", side_effect=OSError("git not found"))
def test_is_inside_git_repo_false_when_git_missing(mock_run):
    assert is_inside_git_repo() is False


@patch("et.git_branch.subprocess.run")
def test_create_and_checkout_branch_succeeds(mock_run):
    mock_run.return_value = _completed(returncode=0)

    create_and_checkout_branch("feat/some-branch-isd-1")

    args = mock_run.call_args[0][0]
    assert args == ["git", "checkout", "-b", "feat/some-branch-isd-1"]


@patch("et.git_branch.subprocess.run")
def test_create_and_checkout_branch_raises_on_failure(mock_run):
    mock_run.return_value = _completed(returncode=128, stderr="already exists")

    with pytest.raises(GitBranchError, match="already exists"):
        create_and_checkout_branch("feat/some-branch-isd-1")


@patch("et.git_branch.subprocess.run", side_effect=OSError("git not found"))
def test_create_and_checkout_branch_raises_when_git_missing(mock_run):
    with pytest.raises(GitBranchError, match="git not found"):
        create_and_checkout_branch("feat/some-branch-isd-1")


# --- create_branch_interactive -------------------------------------------------


@patch("et.git_branch.create_and_checkout_branch")
@patch("et.git_branch.branch_exists", return_value=False)
@patch("et.git_branch.is_inside_git_repo", return_value=True)
@patch("et.git_branch.fetch_branch_basis")
def test_create_branch_interactive_builds_and_creates_branch(
    mock_fetch, mock_inside_repo, mock_exists, mock_create
):
    mock_fetch.return_value = JiraIssueBasis(
        summary="Add wildcard SNI support", issue_type="Story", labels=()
    )

    result = create_branch_interactive(
        _config(),
        "ISD-1234",
        select_type=lambda default: default,
        edit_description=lambda default: default,
    )

    assert result == BranchCreateResult(
        name="feat/add-wildcard-sni-support-isd-1234", issue_key="ISD-1234"
    )
    mock_create.assert_called_once_with("feat/add-wildcard-sni-support-isd-1234")


@patch("et.git_branch.create_and_checkout_branch")
@patch("et.git_branch.branch_exists", return_value=False)
@patch("et.git_branch.is_inside_git_repo", return_value=True)
@patch("et.git_branch.fetch_branch_basis")
def test_create_branch_interactive_honors_type_and_description_overrides(
    mock_fetch, mock_inside_repo, mock_exists, mock_create
):
    mock_fetch.return_value = JiraIssueBasis(summary="A bug", issue_type="Bug", labels=())

    result = create_branch_interactive(
        _config(),
        "ISD-1",
        select_type=lambda default: "chore",
        edit_description=lambda default: "custom text here",
    )

    assert result is not None
    assert result.name == "chore/custom-text-here-isd-1"


@patch("et.git_branch.is_inside_git_repo", return_value=True)
@patch("et.git_branch.fetch_branch_basis")
def test_create_branch_interactive_cancelled_at_type_selection(mock_fetch, mock_inside_repo):
    mock_fetch.return_value = JiraIssueBasis(summary="A bug", issue_type="Bug", labels=())

    result = create_branch_interactive(
        _config(),
        "ISD-1",
        select_type=lambda default: None,
        edit_description=lambda default: default,
    )

    assert result is None


@patch("et.git_branch.is_inside_git_repo", return_value=True)
@patch("et.git_branch.fetch_branch_basis")
def test_create_branch_interactive_cancelled_at_description_edit(mock_fetch, mock_inside_repo):
    mock_fetch.return_value = JiraIssueBasis(summary="A bug", issue_type="Bug", labels=())

    result = create_branch_interactive(
        _config(),
        "ISD-1",
        select_type=lambda default: default,
        edit_description=lambda default: None,
    )

    assert result is None


@patch("et.git_branch.create_and_checkout_branch")
@patch("et.git_branch.branch_exists", return_value=False)
@patch("et.git_branch.is_inside_git_repo", return_value=True)
@patch("et.git_branch.fetch_branch_basis")
def test_create_branch_interactive_announces_issue_before_prompting(
    mock_fetch, mock_inside_repo, mock_exists, mock_create
):
    basis = JiraIssueBasis(summary="Add wildcard SNI support", issue_type="Story", labels=())
    mock_fetch.return_value = basis
    announced: list[JiraIssueBasis] = []

    result = create_branch_interactive(
        _config(),
        "ISD-1234",
        select_type=lambda default: default,
        edit_description=lambda default: default,
        announce_issue=announced.append,
    )

    assert result is not None
    assert announced == [basis]



    with patch("et.git_branch.is_inside_git_repo", return_value=False):
        with pytest.raises(GitBranchError, match="not inside a git repository"):
            create_branch_interactive(
                _config(),
                "ISD-1",
                select_type=lambda default: default,
                edit_description=lambda default: default,
            )


@patch("et.git_branch.branch_exists", return_value=True)
@patch("et.git_branch.is_inside_git_repo", return_value=True)
@patch("et.git_branch.fetch_branch_basis")
def test_create_branch_interactive_raises_when_branch_already_exists(
    mock_fetch, mock_inside_repo, mock_exists
):
    mock_fetch.return_value = JiraIssueBasis(summary="A bug", issue_type="Bug", labels=())

    with pytest.raises(GitBranchError, match="already exists"):
        create_branch_interactive(
            _config(),
            "ISD-1",
            select_type=lambda default: default,
            edit_description=lambda default: default,
        )


@patch("et.git_branch.is_inside_git_repo", return_value=True)
@patch("et.git_branch.fetch_issue_basis", side_effect=JiraError("boom"))
def test_create_branch_interactive_wraps_jira_errors(mock_fetch_basis, mock_inside_repo):
    with pytest.raises(GitBranchError, match="boom"):
        create_branch_interactive(
            _config(),
            "ISD-1",
            select_type=lambda default: default,
            edit_description=lambda default: default,
        )
