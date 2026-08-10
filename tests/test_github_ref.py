"""Tests for et.github_ref, mocking subprocess.run."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from et.github_ref import (
    GithubRef,
    GithubRefDetails,
    GithubRefError,
    fetch_github_ref,
    parse_github_url,
)


def test_parse_github_url_parses_issue_url():
    ref = parse_github_url("https://github.com/canonical/wazuh-server-operator/issues/263")

    assert ref == GithubRef(
        owner="canonical", repo="wazuh-server-operator", number=263, kind="issue"
    )


def test_parse_github_url_parses_pr_url():
    ref = parse_github_url("https://github.com/canonical/wazuh-server-operator/pull/410")

    assert ref == GithubRef(owner="canonical", repo="wazuh-server-operator", number=410, kind="pr")
    assert ref.repo_slug == "canonical/wazuh-server-operator"


def test_parse_github_url_rejects_unrecognized_url():
    with pytest.raises(GithubRefError, match="not a recognized GitHub"):
        parse_github_url("https://github.com/canonical/wazuh-server-operator")


def test_parse_github_url_rejects_non_github_url():
    with pytest.raises(GithubRefError, match="not a recognized GitHub"):
        parse_github_url("https://example.com/issues/1")


def _run_result(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


@patch("et.github_ref.subprocess.run")
def test_fetch_github_ref_parses_issue_with_bug_label(mock_run):
    mock_run.return_value = _run_result(
        0,
        stdout=json.dumps(
            {
                "title": "Server crashes on startup",
                "body": "Steps to reproduce...",
                "labels": [{"name": "bug"}, {"name": "priority:high"}],
            }
        ),
    )

    ref = GithubRef(owner="canonical", repo="wazuh-server-operator", number=263, kind="issue")
    details = fetch_github_ref(ref)

    assert details == GithubRefDetails(
        title="Server crashes on startup", body="Steps to reproduce...", is_bug=True
    )
    args = mock_run.call_args.args[0]
    assert args[:3] == ["gh", "issue", "view"]
    assert "-R" in args and "canonical/wazuh-server-operator" in args


@patch("et.github_ref.subprocess.run")
def test_fetch_github_ref_issue_without_bug_label(mock_run):
    mock_run.return_value = _run_result(
        0,
        stdout=json.dumps(
            {"title": "Improve docs", "body": "", "labels": [{"name": "documentation"}]}
        ),
    )

    ref = GithubRef(owner="canonical", repo="wazuh-server-operator", number=1, kind="issue")
    details = fetch_github_ref(ref)

    assert details.is_bug is False


@patch("et.github_ref.subprocess.run")
def test_fetch_github_ref_parses_pr(mock_run):
    mock_run.return_value = _run_result(
        0, stdout=json.dumps({"title": "Fix login bug", "body": "This fixes it."})
    )

    ref = GithubRef(owner="canonical", repo="wazuh-server-operator", number=410, kind="pr")
    details = fetch_github_ref(ref)

    assert details == GithubRefDetails(title="Fix login bug", body="This fixes it.", is_bug=False)
    args = mock_run.call_args.args[0]
    assert args[:3] == ["gh", "pr", "view"]


@patch("et.github_ref.subprocess.run")
def test_fetch_github_ref_raises_on_nonzero_exit(mock_run):
    mock_run.return_value = _run_result(1, stderr="issue not found")

    ref = GithubRef(owner="canonical", repo="wazuh-server-operator", number=999, kind="issue")
    with pytest.raises(GithubRefError, match="issue not found"):
        fetch_github_ref(ref)


@patch("et.github_ref.subprocess.run")
def test_fetch_github_ref_raises_on_invalid_json(mock_run):
    mock_run.return_value = _run_result(0, stdout="not json")

    ref = GithubRef(owner="canonical", repo="wazuh-server-operator", number=1, kind="issue")
    with pytest.raises(GithubRefError, match="could not parse"):
        fetch_github_ref(ref)


@patch("et.github_ref.subprocess.run", side_effect=FileNotFoundError("no such file"))
def test_fetch_github_ref_raises_when_gh_not_installed(mock_run):
    ref = GithubRef(owner="canonical", repo="wazuh-server-operator", number=1, kind="issue")
    with pytest.raises(GithubRefError, match="could not run"):
        fetch_github_ref(ref)
