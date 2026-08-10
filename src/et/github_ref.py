"""Parses a GitHub issue/PR URL and fetches its title/body/labels via `gh`.

Used by `et jira create <GITHUB_URL>` to pre-fill an issue's summary and
description. Shells out to the `gh` CLI (already authenticated in this
environment) rather than calling GitHub's REST API directly, so no GitHub
token needs to live in et's own config. Has no Typer/CLI dependency; the
subprocess call is mockable via `subprocess.run`.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

GITHUB_URL_PATTERN = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/"
    r"(?P<kind>issues|pull)/(?P<number>\d+)/?$"
)

BUG_LABEL_NAME = "bug"


class GithubRefError(RuntimeError):
    """Raised when a GitHub URL can't be parsed or `gh` can't fetch it."""


@dataclass(frozen=True)
class GithubRef:
    """A parsed GitHub issue/PR URL."""

    owner: str
    repo: str
    number: int
    kind: str  # "issue" or "pr"

    @property
    def repo_slug(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class GithubRefDetails:
    """Title/body/label info fetched for a `GithubRef` via `gh`."""

    title: str
    body: str
    is_bug: bool


def parse_github_url(url: str) -> GithubRef:
    """Parse a GitHub issue or PR URL into a `GithubRef`.

    Supports `https://github.com/<owner>/<repo>/issues/<n>` and
    `.../pull/<n>`. Raises `GithubRefError` if `url` doesn't match either
    shape.
    """
    match = GITHUB_URL_PATTERN.match(url.strip())
    if not match:
        raise GithubRefError(f"not a recognized GitHub issue/PR URL: {url}")

    kind = "issue" if match.group("kind") == "issues" else "pr"
    return GithubRef(
        owner=match.group("owner"),
        repo=match.group("repo"),
        number=int(match.group("number")),
        kind=kind,
    )


def fetch_github_ref(ref: GithubRef) -> GithubRefDetails:
    """Fetch `ref`'s title/body (and, for issues, whether it's labeled "bug") via `gh`.

    Runs `gh issue view` or `gh pr view` with `--json`. Raises
    `GithubRefError` if `gh` isn't available/authenticated or the call
    fails (e.g. the issue/PR doesn't exist).
    """
    fields = "title,body,labels" if ref.kind == "issue" else "title,body"
    subcommand = "issue" if ref.kind == "issue" else "pr"
    command = [
        "gh",
        subcommand,
        "view",
        str(ref.number),
        "-R",
        ref.repo_slug,
        "--json",
        fields,
    ]

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GithubRefError(f"could not run 'gh {subcommand} view': {exc}") from exc

    if result.returncode != 0:
        raise GithubRefError(
            f"'gh {subcommand} view' failed for {ref.repo_slug}#{ref.number}: "
            f"{result.stderr.strip()}"
        )

    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise GithubRefError(f"could not parse 'gh {subcommand} view' output as JSON: {exc}") \
            from exc

    if not isinstance(payload, dict):
        raise GithubRefError(f"unexpected 'gh {subcommand} view' output: not a JSON object")

    title = payload.get("title")
    body = payload.get("body")
    labels_raw = payload.get("labels") if ref.kind == "issue" else []

    is_bug = False
    if isinstance(labels_raw, list):
        for raw_label in labels_raw:
            if isinstance(raw_label, dict) and str(raw_label.get("name", "")).lower() == (
                BUG_LABEL_NAME
            ):
                is_bug = True
                break

    return GithubRefDetails(
        title=title if isinstance(title, str) else "",
        body=body if isinstance(body, str) else "",
        is_bug=is_bug,
    )
