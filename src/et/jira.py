"""Client for Jira Cloud's REST API, used to fetch the user's active issues
and (for `et jira log-time`/`comment`/`status`) to log work, add comments,
fetch/transition an issue's status.

Talks to Jira Cloud's `/rest/api/3/search/jql` endpoint (the old
`/rest/api/3/search` endpoint was retired by Atlassian and now returns HTTP
410) using HTTP Basic auth (email + API token — Jira Cloud has no
bearer-PAT mode). The new endpoint paginates via a `nextPageToken` cursor
rather than `startAt`/`total`, so all pages are fetched and concatenated.
Has no Typer/CLI dependency; the HTTP call goes through `requests` so it
can be unit tested by mocking `requests.get`/`requests.post`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import requests

from et.config import JiraConfig

SEARCH_PATH = "rest/api/3/search/jql"
WORKLOG_PATH_TEMPLATE = "rest/api/3/issue/{key}/worklog"
TRANSITIONS_PATH_TEMPLATE = "rest/api/3/issue/{key}/transitions"
COMMENT_PATH_TEMPLATE = "rest/api/3/issue/{key}/comment"
USER_SEARCH_PATH = "rest/api/3/user/search"
MYSELF_PATH = "rest/api/3/myself"
COMPONENTS_PATH_TEMPLATE = "rest/api/3/project/{key}/components"
FIELD_PATH = "rest/api/3/field"
ISSUE_PATH = "rest/api/3/issue"
BOARD_PATH = "rest/agile/1.0/board"
BOARD_SPRINT_PATH_TEMPLATE = "rest/agile/1.0/board/{board_id}/sprint"
AGILE_ISSUE_PATH_TEMPLATE = "rest/agile/1.0/issue/{key}"
SPRINT_ISSUE_PATH_TEMPLATE = "rest/agile/1.0/sprint/{sprint_id}/issue"
SPRINT_FIELD_NAME = "Sprint"
BUG_LINK_FIELD_NAME = "Bug link"

logger = logging.getLogger(__name__)


class JiraError(RuntimeError):
    """Raised when the Jira API cannot be reached or returns an error."""


class JiraBoardWithoutSprintsError(JiraError):
    """Raised when `fetch_active_sprint` is called on a board that has no sprints.

    This happens for Kanban boards (or any board not configured for
    Scrum), which Jira's `/rest/agile/1.0/board/{id}/sprint` endpoint
    rejects with HTTP 400 and an "does not support sprints" message.
    """


@dataclass(frozen=True)
class JiraIssue:
    """One issue fetched from Jira."""

    key: str
    summary: str
    priority: str
    status: str = ""
    original_estimate_seconds: int | None = None


@dataclass(frozen=True)
class JiraTransition:
    """One workflow transition available for an issue (from Jira's transitions API)."""

    id: str
    name: str
    to_status: str


@dataclass(frozen=True)
class JiraComponent:
    """One component defined on a Jira project."""

    id: str
    name: str


@dataclass(frozen=True)
class JiraSprint:
    """One sprint fetched from a Jira Agile board."""

    id: str
    name: str


@dataclass(frozen=True)
class JiraIssueBasis:
    """The fields `et git create-branch` needs to name a branch after an issue."""

    summary: str
    issue_type: str
    labels: tuple[str, ...]


def text_to_adf(text: str) -> dict[str, object]:
    """Wrap plain text in the minimal Atlassian Document Format Jira expects.

    Used for both worklog comments and issue descriptions. Blank lines
    split `text` into separate paragraphs so multi-line input reads
    naturally in Jira's rich-text renderer.
    """
    paragraphs = text.split("\n\n") if text else [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": paragraph}]}
            for paragraph in paragraphs
        ],
    }


def _request(
    jira_config: JiraConfig,
    method: Literal["GET", "POST"],
    url: str,
    *,
    expected_statuses: tuple[int, ...],
    params: dict[str, str] | None = None,
    json: dict[str, object] | None = None,
) -> requests.Response:
    """Send one authenticated Jira request and validate its status."""
    try:
        if method == "GET":
            response = requests.get(
                url,
                params=params,
                auth=(jira_config.email, jira_config.pat),
                timeout=30,
            )
        else:
            response = requests.post(
                url,
                json=json,
                auth=(jira_config.email, jira_config.pat),
                timeout=30,
            )
    except requests.RequestException as exc:
        raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

    if response.status_code not in expected_statuses:
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )
    return response


def _response_json(response: requests.Response) -> object:
    """Parse a Jira response body as JSON with a consistent error."""
    try:
        return response.json()
    except ValueError as exc:
        raise JiraError(f"could not parse Jira API response as JSON: {exc}") from exc


def _get_json(jira_config: JiraConfig, url: str, params: dict[str, str] | None = None) -> object:
    """GET `url` with Jira Basic auth and return the parsed JSON body."""
    response = _request(
        jira_config,
        "GET",
        url,
        expected_statuses=(200,),
        params=params,
    )
    return _response_json(response)


def _jira_url(jira_config: JiraConfig, path: str) -> str:
    return jira_config.base_url.rstrip("/") + "/" + path


def search_user_account_id(jira_config: JiraConfig, email: str) -> str | None:
    """Return the Jira accountId for `email`, or None if no user matches.

    Calls Jira's `GET /rest/api/3/user/search?query=<email>` endpoint and
    returns the first match's `accountId`.
    """
    url = _jira_url(jira_config, USER_SEARCH_PATH)
    payload = _get_json(jira_config, url, params={"query": email})
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, dict):
        return None
    account_id = first.get("accountId")
    return account_id if isinstance(account_id, str) else None


def fetch_components(jira_config: JiraConfig, project_key: str) -> list[JiraComponent]:
    """Fetch the components defined on `project_key`.

    Calls Jira's `GET /rest/api/3/project/{key}/components` endpoint.
    """
    url = _jira_url(jira_config, COMPONENTS_PATH_TEMPLATE.format(key=project_key))
    payload = _get_json(jira_config, url)
    if not isinstance(payload, list):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON list")

    components: list[JiraComponent] = []
    for raw_component in payload:
        if not isinstance(raw_component, dict):
            continue
        component_id = raw_component.get("id")
        name = raw_component.get("name")
        if isinstance(component_id, str) and isinstance(name, str):
            components.append(JiraComponent(id=component_id, name=name))
    return components


def discover_board_id(
    jira_config: JiraConfig, project_key: str, board_type: str | None = None
) -> str | None:
    """Return the first Jira Agile board id associated with `project_key`.

    Calls Jira's `GET /rest/agile/1.0/board?projectKeyOrId=<key>` endpoint,
    optionally filtered to `board_type` (e.g. `"scrum"`, since sprints only
    exist on Scrum boards — Kanban boards reject the sprint endpoint
    outright). Returns None if no matching board is found.
    """
    url = _jira_url(jira_config, BOARD_PATH)
    params = {"projectKeyOrId": project_key}
    if board_type:
        params["type"] = board_type
    payload = _get_json(jira_config, url, params=params)
    values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(values, list) or not values:
        return None
    first = values[0]
    if not isinstance(first, dict):
        return None
    board_id = first.get("id")
    return str(board_id) if board_id is not None else None


def fetch_active_sprints(jira_config: JiraConfig, board_id: str) -> list[JiraSprint]:
    """Return every currently active sprint on `board_id` (empty if there are none).

    Calls Jira's `GET /rest/agile/1.0/board/{id}/sprint?state=active`
    endpoint. A board can have more than one concurrently active sprint
    (e.g. separate sprints per sub-team), so callers that need "the"
    active sprint must pick one themselves rather than assuming there's
    only ever zero or one. Raises `JiraBoardWithoutSprintsError` (a
    `JiraError` subclass) if `board_id` doesn't support sprints at all
    (e.g. it's a Kanban board), so callers can distinguish "wrong kind of
    board" from "no sprint currently active" or a transient API failure.
    """
    url = _jira_url(jira_config, BOARD_SPRINT_PATH_TEMPLATE.format(board_id=board_id))

    response = _request(
        jira_config,
        "GET",
        url,
        expected_statuses=(200, 400),
        params={"state": "active"},
    )

    if response.status_code == 400 and _mentions_unsupported_sprints(response.text):
        raise JiraBoardWithoutSprintsError(
            f"board {board_id} does not support sprints (it's likely a Kanban board)"
        )

    if response.status_code != 200:
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )

    payload = _response_json(response)

    values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        return []

    sprints: list[JiraSprint] = []
    for raw_sprint in values:
        if not isinstance(raw_sprint, dict):
            continue
        sprint_id = raw_sprint.get("id")
        if sprint_id is None:
            continue
        name = raw_sprint.get("name")
        sprints.append(JiraSprint(id=str(sprint_id), name=name if isinstance(name, str) else ""))
    return sprints


def _mentions_unsupported_sprints(response_text: str) -> bool:
    """Return True if `response_text` (a Jira error body) says a board lacks sprints."""
    return "does not support sprints" in response_text.lower()


def fetch_issue_sprint(jira_config: JiraConfig, issue_key: str) -> JiraSprint | None:
    """Return `issue_key`'s current (open) sprint, or None if it isn't in one.

    Calls Jira's Agile API `GET /rest/agile/1.0/issue/{key}?fields=sprint`
    endpoint, which — unlike the plain issue API — exposes the issue's
    current sprint directly without needing to look up the "Sprint" custom
    field's id first. Used by `et jira start KEY` to check whether an issue
    already belongs to the project's active sprint before adding it.
    Raises `JiraError` if the request cannot be made or Jira rejects it.
    """
    url = _jira_url(jira_config, AGILE_ISSUE_PATH_TEMPLATE.format(key=issue_key))
    payload = _get_json(jira_config, url, params={"fields": "sprint"})
    if not isinstance(payload, dict):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON object")

    fields = payload.get("fields")
    sprint_field = fields.get("sprint") if isinstance(fields, dict) else None
    if not isinstance(sprint_field, dict):
        return None

    sprint_id = sprint_field.get("id")
    if sprint_id is None:
        return None
    name = sprint_field.get("name")
    return JiraSprint(id=str(sprint_id), name=name if isinstance(name, str) else "")


def add_issue_to_sprint(jira_config: JiraConfig, sprint_id: str, issue_key: str) -> None:
    """Add `issue_key` to the sprint identified by `sprint_id`.

    Calls Jira's Agile API `POST /rest/agile/1.0/sprint/{sprint_id}/issue`
    endpoint (which returns 204 No Content on success) with
    `{"issues": [issue_key]}`. Used by `et jira start KEY` to bring an issue
    into the project's current active sprint when it isn't already there.
    Raises `JiraError` if the request cannot be made or Jira rejects it.
    """
    url = _jira_url(jira_config, SPRINT_ISSUE_PATH_TEMPLATE.format(sprint_id=sprint_id))

    _request(
        jira_config,
        "POST",
        url,
        expected_statuses=(200, 204),
        json={"issues": [issue_key]},
    )


def fetch_field_id_by_name(jira_config: JiraConfig, field_name: str) -> str | None:
    """Return the custom field id Jira uses for `field_name` on this instance.

    Custom field ids (e.g. `customfield_10020`) aren't standardized across
    Jira Cloud instances, so fields are looked up by their display name via
    `GET /rest/api/3/field` rather than hardcoded. Returns None if no field
    named `field_name` is found.
    """
    url = _jira_url(jira_config, FIELD_PATH)
    payload = _get_json(jira_config, url)
    if not isinstance(payload, list):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON list")

    for raw_field in payload:
        if not isinstance(raw_field, dict):
            continue
        if raw_field.get("name") == field_name:
            field_id = raw_field.get("id")
            return field_id if isinstance(field_id, str) else None
    return None


def fetch_sprint_field_id(jira_config: JiraConfig) -> str | None:
    """Return the custom field id Jira uses for "Sprint" on this instance."""
    return fetch_field_id_by_name(jira_config, SPRINT_FIELD_NAME)


def fetch_bug_link_field_id(jira_config: JiraConfig) -> str | None:
    """Return the custom field id Jira uses for "Bug link" on this instance."""
    return fetch_field_id_by_name(jira_config, BUG_LINK_FIELD_NAME)


def create_issue(jira_config: JiraConfig, fields: dict[str, object]) -> str:
    """Create a Jira issue with the given `fields` payload and return its key.

    Calls Jira's `POST /rest/api/3/issue` endpoint with `{"fields": fields}`.
    Raises `JiraError` if the request cannot be made or Jira rejects it.
    """
    url = _jira_url(jira_config, ISSUE_PATH)

    response = _request(
        jira_config,
        "POST",
        url,
        expected_statuses=(200, 201),
        json={"fields": fields},
    )
    payload = _response_json(response)

    key = payload.get("key") if isinstance(payload, dict) else None
    if not isinstance(key, str) or not key:
        raise JiraError(f"unexpected Jira API response from {url}: no 'key' in response")
    return key


def create_worklog(
    jira_config: JiraConfig, issue_key: str, seconds: int, comment: str | None = None
) -> dict[str, object]:
    """Log `seconds` of work against `issue_key` via Jira's own worklog API.

    This is Jira's native worklog feature (`POST
    /rest/api/3/issue/{key}/worklog`), not Tempo's — but worklogs created
    this way still show up in Tempo timesheets when Tempo is configured to
    sync native Jira worklogs, which avoids needing a separate Tempo API
    token. Returns the created worklog's raw JSON. Raises `JiraError` if the
    request cannot be made or Jira rejects it.
    """
    url = jira_config.base_url.rstrip("/") + "/" + WORKLOG_PATH_TEMPLATE.format(key=issue_key)
    payload: dict[str, object] = {"timeSpentSeconds": seconds}
    if comment:
        payload["comment"] = text_to_adf(comment)

    response = _request(
        jira_config,
        "POST",
        url,
        expected_statuses=(200, 201),
        json=payload,
    )
    payload_response = _response_json(response)

    if not isinstance(payload_response, dict):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON object")

    return payload_response


def create_comment(jira_config: JiraConfig, issue_key: str, body: str) -> dict[str, object]:
    """Add a comment to `issue_key` via Jira's own comment API.

    Calls Jira's `POST /rest/api/3/issue/{key}/comment` endpoint with
    `body` converted to Atlassian Document Format via `text_to_adf`.
    Returns the created comment's raw JSON. Raises `JiraError` if the
    request cannot be made or Jira rejects it.
    """
    url = jira_config.base_url.rstrip("/") + "/" + COMMENT_PATH_TEMPLATE.format(key=issue_key)

    response = _request(
        jira_config,
        "POST",
        url,
        expected_statuses=(200, 201),
        json={"body": text_to_adf(body)},
    )
    payload_response = _response_json(response)

    if not isinstance(payload_response, dict):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON object")

    return payload_response


def fetch_issue_status(jira_config: JiraConfig, issue_key: str) -> str:
    """Return `issue_key`'s current workflow status name.

    Calls Jira's `GET /rest/api/3/issue/{key}?fields=status` endpoint.
    Raises `JiraError` if the request cannot be made, Jira rejects it, or
    the response has no usable status name.
    """
    url = _jira_url(jira_config, f"{ISSUE_PATH}/{issue_key}")
    payload = _get_json(jira_config, url, params={"fields": "status"})
    if not isinstance(payload, dict):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON object")

    fields = payload.get("fields")
    status_field = fields.get("status") if isinstance(fields, dict) else None
    name = status_field.get("name") if isinstance(status_field, dict) else None
    if not isinstance(name, str) or not name:
        raise JiraError(f"unexpected Jira API response from {url}: no status name found")
    return name


def _parse_original_estimate_seconds(fields: dict[str, object]) -> int | None:
    """Return `fields.timetracking.originalEstimateSeconds` if it's a valid duration.

    Must be present and a nonnegative `int` (explicitly excluding `bool`,
    which is a subclass of `int` in Python) to be considered valid;
    otherwise returns `None` rather than raising, since a missing/malformed
    estimate shouldn't fail the whole issue fetch.
    """
    timetracking = fields.get("timetracking")
    if not isinstance(timetracking, dict):
        return None

    value = timetracking.get("originalEstimateSeconds")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None

    return value


def fetch_issue(jira_config: JiraConfig, issue_key: str) -> JiraIssue:
    """Return `issue_key`'s summary, priority, status, and original estimate as a `JiraIssue`.

    Calls Jira's
    `GET /rest/api/3/issue/{key}?fields=summary,priority,status,timetracking`
    endpoint. Used by `et jira start KEY` to look up an issue given directly
    by key, rather than picked from `fetch_active_issues`'s candidate list.
    Raises `JiraError` if the request cannot be made, Jira rejects it, or
    the response has no usable summary/status.
    """
    url = _jira_url(jira_config, f"{ISSUE_PATH}/{issue_key}")
    payload = _get_json(
        jira_config, url, params={"fields": "summary,priority,status,timetracking"}
    )
    if not isinstance(payload, dict):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON object")

    fields = payload.get("fields")
    if not isinstance(fields, dict):
        raise JiraError(f"unexpected Jira API response from {url}: no fields found")

    summary = fields.get("summary")
    if not isinstance(summary, str) or not summary:
        raise JiraError(f"unexpected Jira API response from {url}: no summary found")

    status_field = fields.get("status") or {}
    status = status_field.get("name") if isinstance(status_field, dict) else None
    if not isinstance(status, str) or not status:
        raise JiraError(f"unexpected Jira API response from {url}: no status name found")

    priority_field = fields.get("priority") or {}
    priority = priority_field.get("name") if isinstance(priority_field, dict) else None

    return JiraIssue(
        key=issue_key,
        summary=summary,
        priority=priority if isinstance(priority, str) else "",
        status=status,
        original_estimate_seconds=_parse_original_estimate_seconds(fields),
    )


def fetch_issue_basis(jira_config: JiraConfig, issue_key: str) -> JiraIssueBasis:
    """Return `issue_key`'s summary, issue type name, and labels.

    Calls Jira's `GET /rest/api/3/issue/{key}?fields=summary,issuetype,labels`
    endpoint. Used by `et git create-branch` to propose a branch name.
    Raises `JiraError` if the request cannot be made, Jira rejects it, or
    the response has no usable summary/issue type.
    """
    url = _jira_url(jira_config, f"{ISSUE_PATH}/{issue_key}")
    payload = _get_json(jira_config, url, params={"fields": "summary,issuetype,labels"})
    if not isinstance(payload, dict):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON object")

    fields = payload.get("fields")
    if not isinstance(fields, dict):
        raise JiraError(f"unexpected Jira API response from {url}: no fields found")

    summary = fields.get("summary")
    if not isinstance(summary, str) or not summary:
        raise JiraError(f"unexpected Jira API response from {url}: no summary found")

    issue_type_field = fields.get("issuetype")
    issue_type = issue_type_field.get("name") if isinstance(issue_type_field, dict) else None
    if not isinstance(issue_type, str) or not issue_type:
        raise JiraError(f"unexpected Jira API response from {url}: no issue type found")

    labels_raw = fields.get("labels")
    labels = tuple(label for label in labels_raw if isinstance(label, str)) \
        if isinstance(labels_raw, list) else ()

    return JiraIssueBasis(summary=summary, issue_type=issue_type, labels=labels)


def _fetch_issue_pages(jira_config: JiraConfig, url: str) -> list[object]:
    """Fetch every page of raw issue dicts for `jira_config.jql`, via `nextPageToken`."""
    all_issues_raw: list[object] = []
    next_page_token: str | None = None

    while True:
        params: dict[str, str] = {
            "jql": jira_config.jql,
            "fields": "summary,priority,status,timetracking",
        }
        if next_page_token is not None:
            params["nextPageToken"] = next_page_token

        logger.debug("GET %s as %s with params %r", url, jira_config.email, params)

        response = _request(
            jira_config,
            "GET",
            url,
            expected_statuses=(200,),
            params=params,
        )
        payload = _response_json(response)

        issues_raw = payload.get("issues") if isinstance(payload, dict) else None
        if not isinstance(issues_raw, list):
            raise JiraError(f"unexpected Jira API response from {url}: no 'issues' list")

        keys = [issue.get("key", "?") for issue in issues_raw if isinstance(issue, dict)]
        logger.debug("  -> %d issue(s): %s", len(issues_raw), keys)

        all_issues_raw.extend(issues_raw)

        # Jira's bounded scan may hand back an empty page that still has a
        # nextPageToken, so only the missing token means "no more results".
        next_page_token = payload.get("nextPageToken") if isinstance(payload, dict) else None
        if not next_page_token:
            break

    return all_issues_raw


def _check_credentials(jira_config: JiraConfig) -> None:
    """Raise `JiraError` if Jira doesn't accept `jira_config`'s email/token.

    Unlike the search endpoint, `/myself` has no anonymous mode: it answers
    401 when the credentials aren't usable. A network failure here is
    ignored, since the caller's own request already succeeded.
    """
    url = jira_config.base_url.rstrip("/") + "/" + MYSELF_PATH

    try:
        response = requests.get(url, auth=(jira_config.email, jira_config.pat), timeout=30)
    except requests.RequestException:
        return

    logger.debug("credential check: GET %s -> %d", url, response.status_code)

    if response.status_code in (401, 403):
        raise JiraError(
            f"Jira rejected your credentials (status {response.status_code}): check jira.email "
            f"and jira.pat in your config file. Jira Cloud API tokens expire, and a truncated "
            f"or stale token makes searches return no issues instead of failing outright."
        )


def fetch_active_issues(jira_config: JiraConfig) -> list[JiraIssue]:
    """Fetch issues matching `jira_config.jql`, sorted by decreasing priority.

    Sorting uses `jira_config.priority_order` (highest priority first, ties
    broken by the order the API returned them in); issues whose priority
    name isn't in that list sort after all known priorities, with a warning
    printed to stderr for each one.

    Raises `JiraError` if Jira rejects the configured credentials, which an
    empty result set can otherwise hide (see `_check_credentials`).
    """
    url = jira_config.base_url.rstrip("/") + "/" + SEARCH_PATH
    issues_raw = _fetch_issue_pages(jira_config, url)

    # Jira serves an unauthenticated search anonymously rather than
    # refusing it, so a bad token looks like "you have no issues".
    if not issues_raw:
        _check_credentials(jira_config)

    issues: list[JiraIssue] = []
    for raw_issue in issues_raw:
        if not isinstance(raw_issue, dict):
            continue
        key = raw_issue.get("key")
        if not isinstance(key, str) or not key:
            logger.warning("skipping Jira issue with missing or invalid 'key': %r", raw_issue)
            continue
        fields = raw_issue.get("fields")
        if not isinstance(fields, dict):
            logger.warning("skipping Jira issue %s with invalid 'fields': %r", key, fields)
            continue
        priority_field = fields.get("priority")
        status_field = fields.get("status")
        priority = priority_field.get("name") if isinstance(priority_field, dict) else ""
        status = status_field.get("name") if isinstance(status_field, dict) else ""
        summary = fields.get("summary")
        issues.append(
            JiraIssue(
                key=key,
                summary=summary if isinstance(summary, str) else "",
                priority=priority if isinstance(priority, str) else "",
                status=status if isinstance(status, str) else "",
                original_estimate_seconds=_parse_original_estimate_seconds(fields),
            )
        )

    rank = {name: index for index, name in enumerate(jira_config.priority_order)}
    unranked = len(jira_config.priority_order)

    for issue in issues:
        if issue.priority not in rank:
            logger.warning(
                "issue %s has unknown priority '%s', treating it as lowest priority",
                issue.key,
                issue.priority,
            )

    indexed = list(enumerate(issues))
    indexed.sort(key=lambda pair: (rank.get(pair[1].priority, unranked), pair[0]))
    return [issue for _, issue in indexed]


def fetch_transitions(jira_config: JiraConfig, issue_key: str) -> list[JiraTransition]:
    """Fetch the workflow transitions currently available for `issue_key`.

    Calls Jira's `GET /rest/api/3/issue/{key}/transitions` endpoint. Raises
    `JiraError` if the request cannot be made or Jira rejects it.
    """
    url = jira_config.base_url.rstrip("/") + "/" + TRANSITIONS_PATH_TEMPLATE.format(key=issue_key)

    response = _request(jira_config, "GET", url, expected_statuses=(200,))
    payload = _response_json(response)

    transitions_raw = payload.get("transitions") if isinstance(payload, dict) else None
    if not isinstance(transitions_raw, list):
        raise JiraError(f"unexpected Jira API response from {url}: no 'transitions' list")

    transitions: list[JiraTransition] = []
    for raw_transition in transitions_raw:
        if not isinstance(raw_transition, dict):
            continue
        transition_id = raw_transition.get("id")
        if not isinstance(transition_id, str) or not transition_id:
            continue
        to_field = raw_transition.get("to")
        to_status = to_field.get("name") if isinstance(to_field, dict) else ""
        name = raw_transition.get("name")
        transitions.append(
            JiraTransition(
                id=transition_id,
                name=name if isinstance(name, str) else "",
                to_status=to_status if isinstance(to_status, str) else "",
            )
        )

    return transitions


def transition_issue(jira_config: JiraConfig, issue_key: str, transition_id: str) -> None:
    """Move `issue_key` through the workflow transition identified by `transition_id`.

    Calls Jira's `POST /rest/api/3/issue/{key}/transitions` endpoint (which
    returns 204 No Content on success). Raises `JiraError` if the request
    cannot be made or Jira rejects it.
    """
    url = jira_config.base_url.rstrip("/") + "/" + TRANSITIONS_PATH_TEMPLATE.format(key=issue_key)

    _request(
        jira_config,
        "POST",
        url,
        expected_statuses=(200, 204),
        json={"transition": {"id": transition_id}},
    )
