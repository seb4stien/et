"""Client for Jira Cloud's REST API, used to fetch the user's active issues
and (for `et jira log-time`) to log work against an issue.

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

import requests

from et.config import JiraConfig

SEARCH_PATH = "rest/api/3/search/jql"
WORKLOG_PATH_TEMPLATE = "rest/api/3/issue/{key}/worklog"
TRANSITIONS_PATH_TEMPLATE = "rest/api/3/issue/{key}/transitions"
USER_SEARCH_PATH = "rest/api/3/user/search"
COMPONENTS_PATH_TEMPLATE = "rest/api/3/project/{key}/components"
FIELD_PATH = "rest/api/3/field"
ISSUE_PATH = "rest/api/3/issue"
BOARD_PATH = "rest/agile/1.0/board"
BOARD_SPRINT_PATH_TEMPLATE = "rest/agile/1.0/board/{board_id}/sprint"
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


def _get_json(jira_config: JiraConfig, url: str, params: dict[str, str] | None = None) -> object:
    """GET `url` with Jira Basic auth and return the parsed JSON body.

    Raises `JiraError` if the request cannot be made, Jira returns a
    non-200 status, or the body isn't valid JSON.
    """
    try:
        response = requests.get(
            url,
            params=params,
            auth=(jira_config.email, jira_config.pat),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

    if response.status_code != 200:
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise JiraError(f"could not parse Jira API response as JSON: {exc}") from exc


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


def fetch_active_sprint(jira_config: JiraConfig, board_id: str) -> JiraSprint | None:
    """Return the currently active sprint on `board_id`, or None if there isn't one.

    Calls Jira's `GET /rest/agile/1.0/board/{id}/sprint?state=active`
    endpoint. Raises `JiraBoardWithoutSprintsError` (a `JiraError`
    subclass) if `board_id` doesn't support sprints at all (e.g. it's a
    Kanban board), so callers can distinguish "wrong kind of board" from
    "no sprint currently active" or a transient API failure.
    """
    url = _jira_url(jira_config, BOARD_SPRINT_PATH_TEMPLATE.format(board_id=board_id))

    try:
        response = requests.get(
            url,
            params={"state": "active"},
            auth=(jira_config.email, jira_config.pat),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

    if response.status_code == 400 and _mentions_unsupported_sprints(response.text):
        raise JiraBoardWithoutSprintsError(
            f"board {board_id} does not support sprints (it's likely a Kanban board)"
        )

    if response.status_code != 200:
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise JiraError(f"could not parse Jira API response as JSON: {exc}") from exc

    values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(values, list) or not values:
        return None
    first = values[0]
    if not isinstance(first, dict):
        return None
    sprint_id = first.get("id")
    name = first.get("name")
    if sprint_id is None:
        return None
    return JiraSprint(id=str(sprint_id), name=name if isinstance(name, str) else "")


def _mentions_unsupported_sprints(response_text: str) -> bool:
    """Return True if `response_text` (a Jira error body) says a board lacks sprints."""
    return "does not support sprints" in response_text.lower()


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

    try:
        response = requests.post(
            url,
            json={"fields": fields},
            auth=(jira_config.email, jira_config.pat),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

    if response.status_code not in (200, 201):
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise JiraError(f"could not parse Jira API response as JSON: {exc}") from exc

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

    try:
        response = requests.post(
            url,
            json=payload,
            auth=(jira_config.email, jira_config.pat),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

    if response.status_code not in (200, 201):
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )

    try:
        payload_response = response.json()
    except ValueError as exc:
        raise JiraError(f"could not parse Jira API response as JSON: {exc}") from exc

    if not isinstance(payload_response, dict):
        raise JiraError(f"unexpected Jira API response from {url}: not a JSON object")

    return payload_response


def _fetch_issue_pages(jira_config: JiraConfig, url: str) -> list[object]:
    """Fetch every page of raw issue dicts for `jira_config.jql`, via `nextPageToken`."""
    all_issues_raw: list[object] = []
    next_page_token: str | None = None

    while True:
        params: dict[str, str] = {"jql": jira_config.jql, "fields": "summary,priority,status"}
        if next_page_token is not None:
            params["nextPageToken"] = next_page_token

        try:
            response = requests.get(
                url,
                params=params,
                auth=(jira_config.email, jira_config.pat),
                timeout=30,
            )
        except requests.RequestException as exc:
            raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

        if response.status_code != 200:
            raise JiraError(
                f"Jira API request to {url} failed with status {response.status_code}: "
                f"{response.text.strip()[:500]}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise JiraError(f"could not parse Jira API response as JSON: {exc}") from exc

        issues_raw = payload.get("issues") if isinstance(payload, dict) else None
        if not isinstance(issues_raw, list):
            raise JiraError(f"unexpected Jira API response from {url}: no 'issues' list")

        all_issues_raw.extend(issues_raw)

        next_page_token = payload.get("nextPageToken") if isinstance(payload, dict) else None
        if not next_page_token or not issues_raw:
            break

    return all_issues_raw


def fetch_active_issues(jira_config: JiraConfig) -> list[JiraIssue]:
    """Fetch issues matching `jira_config.jql`, sorted by decreasing priority.

    Sorting uses `jira_config.priority_order` (highest priority first, ties
    broken by the order the API returned them in); issues whose priority
    name isn't in that list sort after all known priorities, with a warning
    printed to stderr for each one.
    """
    url = jira_config.base_url.rstrip("/") + "/" + SEARCH_PATH
    issues_raw = _fetch_issue_pages(jira_config, url)

    issues: list[JiraIssue] = []
    for raw_issue in issues_raw:
        if not isinstance(raw_issue, dict):
            continue
        key = raw_issue.get("key")
        if not isinstance(key, str) or not key:
            logger.warning("skipping Jira issue with missing or invalid 'key': %r", raw_issue)
            continue
        fields = raw_issue.get("fields", {})
        priority_field = fields.get("priority") or {}
        status_field = fields.get("status") or {}
        issues.append(
            JiraIssue(
                key=key,
                summary=fields.get("summary") or "",
                priority=priority_field.get("name") or "",
                status=status_field.get("name") or "",
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

    try:
        response = requests.get(
            url,
            auth=(jira_config.email, jira_config.pat),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

    if response.status_code != 200:
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise JiraError(f"could not parse Jira API response as JSON: {exc}") from exc

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
        to_field = raw_transition.get("to") or {}
        transitions.append(
            JiraTransition(
                id=transition_id,
                name=raw_transition.get("name") or "",
                to_status=to_field.get("name") or "",
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

    try:
        response = requests.post(
            url,
            json={"transition": {"id": transition_id}},
            auth=(jira_config.email, jira_config.pat),
            timeout=30,
        )
    except requests.RequestException as exc:
        raise JiraError(f"could not reach Jira at {url}: {exc}") from exc

    if response.status_code not in (200, 204):
        raise JiraError(
            f"Jira API request to {url} failed with status {response.status_code}: "
            f"{response.text.strip()[:500]}"
        )
