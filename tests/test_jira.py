"""Tests for et.jira, mocking requests.get/requests.post."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from et.config import JiraConfig
from et.jira import (
    JiraComponent,
    JiraError,
    JiraIssue,
    JiraSprint,
    create_comment,
    create_issue,
    create_worklog,
    discover_board_id,
    fetch_active_issues,
    fetch_active_sprint,
    fetch_bug_link_field_id,
    fetch_components,
    fetch_issue_status,
    fetch_sprint_field_id,
    fetch_transitions,
    search_user_account_id,
    text_to_adf,
    transition_issue,
)


def _config(**overrides: object) -> JiraConfig:
    defaults = dict(
        base_url="https://example.atlassian.net/",
        email="me@example.com",
        pat="secret-token",
        jql="assignee = currentUser()",
    )
    defaults.update(overrides)
    return JiraConfig(**defaults)  # type: ignore[arg-type]


def _response(issues: list[dict], next_page_token: str | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    payload: dict[str, object] = {"issues": issues}
    if next_page_token is not None:
        payload["nextPageToken"] = next_page_token
    response.json.return_value = payload
    return response


def _issue(key: str, summary: str, priority: str, status: str | None = None) -> dict:
    fields: dict[str, object] = {"summary": summary, "priority": {"name": priority}}
    if status is not None:
        fields["status"] = {"name": status}
    return {"key": key, "fields": fields}


@patch("et.jira.requests.get")
def test_fetch_active_issues_sorts_by_decreasing_priority(mock_get):
    mock_get.return_value = _response(
        [
            _issue("PROJ-1", "Low priority task", "Low"),
            _issue("PROJ-2", "Urgent task", "Highest"),
            _issue("PROJ-3", "Medium task", "Medium"),
        ]
    )

    issues = fetch_active_issues(_config())

    assert [issue.key for issue in issues] == ["PROJ-2", "PROJ-3", "PROJ-1"]
    assert issues[0] == JiraIssue(key="PROJ-2", summary="Urgent task", priority="Highest")


@patch("et.jira.requests.get")
def test_fetch_active_issues_passes_jql_and_basic_auth(mock_get):
    mock_get.return_value = _response([])
    config = _config(base_url="https://example.atlassian.net")

    fetch_active_issues(config)

    args, kwargs = mock_get.call_args_list[0]
    assert args[0] == "https://example.atlassian.net/rest/api/3/search/jql"
    assert kwargs["params"] == {"jql": config.jql, "fields": "summary,priority,status"}
    assert kwargs["auth"] == (config.email, config.pat)


@patch("et.jira.requests.get")
def test_fetch_active_issues_unknown_priority_sorts_last_and_warns(mock_get, caplog):
    mock_get.return_value = _response(
        [
            _issue("PROJ-1", "Custom priority task", "Blocker"),
            _issue("PROJ-2", "Known priority task", "Low"),
        ]
    )

    with caplog.at_level("WARNING", logger="et.jira"):
        issues = fetch_active_issues(_config())

    assert [issue.key for issue in issues] == ["PROJ-2", "PROJ-1"]
    assert "PROJ-1" in caplog.text


@patch("et.jira.requests.get")
def test_fetch_active_issues_skips_issue_without_key(mock_get, caplog):
    mock_get.return_value = _response(
        [
            {"fields": {"summary": "no key", "priority": {"name": "High"}}},
            _issue("PROJ-2", "Has key", "Low"),
        ]
    )

    with caplog.at_level("WARNING", logger="et.jira"):
        issues = fetch_active_issues(_config())

    assert [issue.key for issue in issues] == ["PROJ-2"]
    assert "missing or invalid 'key'" in caplog.text


@patch("et.jira.requests.get")
def test_fetch_active_issues_raises_on_non_200_status(mock_get):
    response = MagicMock()
    response.status_code = 401
    response.text = "Unauthorized"
    mock_get.return_value = response

    with pytest.raises(JiraError, match="401"):
        fetch_active_issues(_config())


@patch("et.jira.requests.get")
def test_fetch_active_issues_follows_next_page_token_pagination(mock_get):
    mock_get.side_effect = [
        _response([_issue("PROJ-1", "First page task", "High")], next_page_token="page-2"),
        _response([_issue("PROJ-2", "Second page task", "Highest")]),
    ]

    issues = fetch_active_issues(_config())

    assert mock_get.call_count == 2
    first_kwargs = mock_get.call_args_list[0].kwargs
    second_kwargs = mock_get.call_args_list[1].kwargs
    assert "nextPageToken" not in first_kwargs["params"]
    assert second_kwargs["params"]["nextPageToken"] == "page-2"
    assert {issue.key for issue in issues} == {"PROJ-1", "PROJ-2"}


@patch("et.jira.requests.get")
def test_fetch_active_issues_reports_rejected_credentials_when_no_issues_match(mock_get):
    """Jira answers an unauthenticated search with 200 and no issues, not 401."""
    unauthorized = MagicMock()
    unauthorized.status_code = 401
    unauthorized.text = "Client must be authenticated to access this resource."
    mock_get.side_effect = [_response([]), unauthorized]

    with pytest.raises(JiraError, match="credentials"):
        fetch_active_issues(_config())

    assert mock_get.call_args_list[1].args[0].endswith("/rest/api/3/myself")


@patch("et.jira.requests.get")
def test_fetch_active_issues_returns_empty_when_credentials_are_accepted(mock_get):
    accepted = MagicMock()
    accepted.status_code = 200
    mock_get.side_effect = [_response([]), accepted]

    assert fetch_active_issues(_config()) == []


@patch("et.jira.requests.get")
def test_fetch_active_issues_skips_credential_check_when_issues_match(mock_get):
    mock_get.return_value = _response([_issue("PROJ-1", "Task", "High")])

    fetch_active_issues(_config())

    assert mock_get.call_count == 1


@patch("et.jira.requests.get")
def test_fetch_active_issues_logs_request_and_page_sizes_at_debug(mock_get, caplog):
    mock_get.side_effect = [
        _response([], next_page_token="page-2"),
        _response([_issue("PROJ-1", "Task", "High")]),
    ]

    with caplog.at_level("DEBUG", logger="et.jira"):
        fetch_active_issues(_config(jql="assignee = currentUser() AND statusCategory != Done"))

    assert "assignee = currentUser() AND statusCategory != Done" in caplog.text
    assert "https://example.atlassian.net/rest/api/3/search/jql" in caplog.text
    assert "0 issue(s)" in caplog.text
    assert "1 issue(s)" in caplog.text
    assert "secret-token" not in caplog.text


@patch("et.jira.requests.get")
def test_fetch_active_issues_follows_pagination_past_empty_pages(mock_get):
    """Jira's bounded scan can return an empty page that still has a next page."""
    mock_get.side_effect = [
        _response([], next_page_token="page-2"),
        _response([_issue("PROJ-1", "Task behind an empty page", "High")]),
    ]

    issues = fetch_active_issues(_config())

    assert mock_get.call_count == 2
    assert [issue.key for issue in issues] == ["PROJ-1"]


@patch("et.jira.requests.get", side_effect=requests.ConnectionError("no route to host"))
def test_fetch_active_issues_wraps_network_errors(mock_get):
    with pytest.raises(JiraError, match="no route to host"):
        fetch_active_issues(_config())


@patch("et.jira.requests.get")
def test_fetch_active_issues_respects_custom_priority_order(mock_get):
    mock_get.return_value = _response(
        [
            _issue("PROJ-1", "Task A", "High"),
            _issue("PROJ-2", "Task B", "Low"),
        ]
    )
    config = _config(priority_order=["Low", "High"])

    issues = fetch_active_issues(config)

    assert [issue.key for issue in issues] == ["PROJ-2", "PROJ-1"]


@patch("et.jira.requests.get")
def test_fetch_active_issues_includes_status(mock_get):
    mock_get.return_value = _response([_issue("PROJ-1", "Task A", "High", status="In Progress")])

    issues = fetch_active_issues(_config())

    assert issues[0].status == "In Progress"


@patch("et.jira.requests.get")
def test_fetch_active_issues_defaults_status_to_empty_string_when_missing(mock_get):
    mock_get.return_value = _response([_issue("PROJ-1", "Task A", "High")])

    issues = fetch_active_issues(_config())

    assert issues[0].status == ""


def _json_response(status_code: int, payload: object) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = str(payload)
    return response


@patch("et.jira.requests.post")
def test_create_worklog_posts_seconds_to_issue_worklog_endpoint(mock_post):
    mock_post.return_value = _json_response(201, {"id": "191776"})
    config = _config(base_url="https://example.atlassian.net")

    result = create_worklog(config, "PROJ-1", 780)

    assert result == {"id": "191776"}
    args, kwargs = mock_post.call_args
    assert args[0] == "https://example.atlassian.net/rest/api/3/issue/PROJ-1/worklog"
    assert kwargs["json"] == {"timeSpentSeconds": 780}
    assert kwargs["auth"] == (config.email, config.pat)


@patch("et.jira.requests.post")
def test_create_worklog_includes_comment_as_adf_document_when_given(mock_post):
    mock_post.return_value = _json_response(201, {"id": "191776"})

    create_worklog(_config(), "PROJ-1", 780, comment="Investigating ISD-1")

    _args, kwargs = mock_post.call_args
    assert kwargs["json"]["comment"] == {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Investigating ISD-1"}],
            }
        ],
    }


@patch("et.jira.requests.post")
def test_create_worklog_omits_comment_when_not_given(mock_post):
    mock_post.return_value = _json_response(201, {"id": "191776"})

    create_worklog(_config(), "PROJ-1", 780)

    _args, kwargs = mock_post.call_args
    assert "comment" not in kwargs["json"]


@patch("et.jira.requests.post")
def test_create_worklog_raises_on_non_2xx_status(mock_post):
    mock_post.return_value = _json_response(400, {"errorMessages": ["bad request"]})

    with pytest.raises(JiraError, match="400"):
        create_worklog(_config(), "PROJ-1", 780)


@patch("et.jira.requests.post", side_effect=requests.ConnectionError("no route to host"))
def test_create_worklog_wraps_network_errors(mock_post):
    with pytest.raises(JiraError, match="no route to host"):
        create_worklog(_config(), "PROJ-1", 780)


# --- fetch_transitions / transition_issue -----------------------------------


@patch("et.jira.requests.get")
def test_fetch_transitions_parses_id_name_and_target_status(mock_get):
    mock_get.return_value = _json_response(
        200,
        {
            "transitions": [
                {"id": "11", "name": "Start progress", "to": {"name": "In Progress"}},
                {"id": "21", "name": "Done", "to": {"name": "Done"}},
            ]
        },
    )

    transitions = fetch_transitions(_config(base_url="https://example.atlassian.net"), "PROJ-1")

    args, kwargs = mock_get.call_args
    assert args[0] == "https://example.atlassian.net/rest/api/3/issue/PROJ-1/transitions"
    assert kwargs["auth"] == (_config().email, _config().pat)
    assert [(t.id, t.name, t.to_status) for t in transitions] == [
        ("11", "Start progress", "In Progress"),
        ("21", "Done", "Done"),
    ]


@patch("et.jira.requests.get")
def test_fetch_transitions_skips_transitions_without_id(mock_get):
    mock_get.return_value = _json_response(
        200,
        {"transitions": [{"name": "Broken", "to": {"name": "In Progress"}}]},
    )

    transitions = fetch_transitions(_config(), "PROJ-1")

    assert transitions == []


@patch("et.jira.requests.get")
def test_fetch_transitions_raises_on_non_200_status(mock_get):
    mock_get.return_value = _json_response(404, {"errorMessages": ["not found"]})

    with pytest.raises(JiraError, match="404"):
        fetch_transitions(_config(), "PROJ-1")


@patch("et.jira.requests.get", side_effect=requests.ConnectionError("no route to host"))
def test_fetch_transitions_wraps_network_errors(mock_get):
    with pytest.raises(JiraError, match="no route to host"):
        fetch_transitions(_config(), "PROJ-1")


@patch("et.jira.requests.post")
def test_transition_issue_posts_transition_id(mock_post):
    mock_post.return_value = _json_response(204, {})
    config = _config(base_url="https://example.atlassian.net")

    transition_issue(config, "PROJ-1", "11")

    args, kwargs = mock_post.call_args
    assert args[0] == "https://example.atlassian.net/rest/api/3/issue/PROJ-1/transitions"
    assert kwargs["json"] == {"transition": {"id": "11"}}
    assert kwargs["auth"] == (config.email, config.pat)


@patch("et.jira.requests.post")
def test_transition_issue_raises_on_non_2xx_status(mock_post):
    mock_post.return_value = _json_response(400, {"errorMessages": ["bad request"]})

    with pytest.raises(JiraError, match="400"):
        transition_issue(_config(), "PROJ-1", "11")


@patch("et.jira.requests.post", side_effect=requests.ConnectionError("no route to host"))
def test_transition_issue_wraps_network_errors(mock_post):
    with pytest.raises(JiraError, match="no route to host"):
        transition_issue(_config(), "PROJ-1", "11")


# --- create_comment -----------------------------------------------------


@patch("et.jira.requests.post")
def test_create_comment_posts_body_as_adf_document(mock_post):
    mock_post.return_value = _json_response(201, {"id": "10001"})
    config = _config(base_url="https://example.atlassian.net")

    result = create_comment(config, "PROJ-1", "Looks good to me")

    assert result == {"id": "10001"}
    args, kwargs = mock_post.call_args
    assert args[0] == "https://example.atlassian.net/rest/api/3/issue/PROJ-1/comment"
    assert kwargs["json"] == {"body": text_to_adf("Looks good to me")}
    assert kwargs["auth"] == (config.email, config.pat)


@patch("et.jira.requests.post")
def test_create_comment_raises_on_non_2xx_status(mock_post):
    mock_post.return_value = _json_response(400, {"errorMessages": ["bad request"]})

    with pytest.raises(JiraError, match="400"):
        create_comment(_config(), "PROJ-1", "a comment")


@patch("et.jira.requests.post", side_effect=requests.ConnectionError("no route to host"))
def test_create_comment_wraps_network_errors(mock_post):
    with pytest.raises(JiraError, match="no route to host"):
        create_comment(_config(), "PROJ-1", "a comment")


# --- fetch_issue_status ---------------------------------------------------


@patch("et.jira.requests.get")
def test_fetch_issue_status_returns_status_name(mock_get):
    mock_get.return_value = _json_response(200, {"fields": {"status": {"name": "In Progress"}}})
    config = _config(base_url="https://example.atlassian.net")

    status = fetch_issue_status(config, "PROJ-1")

    assert status == "In Progress"
    args, kwargs = mock_get.call_args
    assert args[0] == "https://example.atlassian.net/rest/api/3/issue/PROJ-1"
    assert kwargs["params"] == {"fields": "status"}
    assert kwargs["auth"] == (config.email, config.pat)


@patch("et.jira.requests.get")
def test_fetch_issue_status_raises_when_no_status_in_response(mock_get):
    mock_get.return_value = _json_response(200, {"fields": {}})

    with pytest.raises(JiraError, match="no status name found"):
        fetch_issue_status(_config(), "PROJ-1")


@patch("et.jira.requests.get")
def test_fetch_issue_status_raises_on_non_200_status(mock_get):
    mock_get.return_value = _json_response(404, {"errorMessages": ["not found"]})

    with pytest.raises(JiraError, match="404"):
        fetch_issue_status(_config(), "PROJ-1")


@patch("et.jira.requests.get", side_effect=requests.ConnectionError("no route to host"))
def test_fetch_issue_status_wraps_network_errors(mock_get):
    with pytest.raises(JiraError, match="no route to host"):
        fetch_issue_status(_config(), "PROJ-1")


def test_text_to_adf_splits_paragraphs_on_blank_lines():
    doc = text_to_adf("first paragraph\n\nsecond paragraph")

    assert doc == {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "first paragraph"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "second paragraph"}]},
        ],
    }


@patch("et.jira.requests.get")
def test_search_user_account_id_returns_first_match(mock_get):
    mock_get.return_value = _json_response(200, [{"accountId": "abc123"}])

    account_id = search_user_account_id(_config(), "me@example.com")

    assert account_id == "abc123"
    args, kwargs = mock_get.call_args
    assert kwargs["params"] == {"query": "me@example.com"}


@patch("et.jira.requests.get")
def test_search_user_account_id_returns_none_when_no_match(mock_get):
    mock_get.return_value = _json_response(200, [])

    assert search_user_account_id(_config(), "nobody@example.com") is None


@patch("et.jira.requests.get")
def test_fetch_components_parses_id_and_name(mock_get):
    mock_get.return_value = _json_response(
        200, [{"id": "10000", "name": "Backend"}, {"id": "10001", "name": "Frontend"}]
    )

    components = fetch_components(_config(), "PROJ")

    assert components == [
        JiraComponent(id="10000", name="Backend"),
        JiraComponent(id="10001", name="Frontend"),
    ]
    args, _kwargs = mock_get.call_args
    assert args[0] == "https://example.atlassian.net/rest/api/3/project/PROJ/components"


@patch("et.jira.requests.get")
def test_discover_board_id_returns_first_board(mock_get):
    mock_get.return_value = _json_response(200, {"values": [{"id": 42}, {"id": 43}]})

    board_id = discover_board_id(_config(), "PROJ")

    assert board_id == "42"


@patch("et.jira.requests.get")
def test_discover_board_id_returns_none_when_no_boards(mock_get):
    mock_get.return_value = _json_response(200, {"values": []})

    assert discover_board_id(_config(), "PROJ") is None


@patch("et.jira.requests.get")
def test_fetch_active_sprint_returns_first_active_sprint(mock_get):
    mock_get.return_value = _json_response(200, {"values": [{"id": 7, "name": "Sprint 7"}]})

    sprint = fetch_active_sprint(_config(), "42")

    assert sprint == JiraSprint(id="7", name="Sprint 7")


@patch("et.jira.requests.get")
def test_fetch_active_sprint_returns_none_when_no_active_sprint(mock_get):
    mock_get.return_value = _json_response(200, {"values": []})

    assert fetch_active_sprint(_config(), "42") is None


@patch("et.jira.requests.get")
def test_fetch_sprint_field_id_finds_field_by_name(mock_get):
    mock_get.return_value = _json_response(
        200,
        [
            {"id": "summary", "name": "Summary"},
            {"id": "customfield_10020", "name": "Sprint"},
        ],
    )

    assert fetch_sprint_field_id(_config()) == "customfield_10020"


@patch("et.jira.requests.get")
def test_fetch_sprint_field_id_returns_none_when_not_found(mock_get):
    mock_get.return_value = _json_response(200, [{"id": "summary", "name": "Summary"}])

    assert fetch_sprint_field_id(_config()) is None


@patch("et.jira.requests.get")
def test_fetch_bug_link_field_id_finds_field_by_name(mock_get):
    mock_get.return_value = _json_response(
        200,
        [
            {"id": "summary", "name": "Summary"},
            {"id": "customfield_10050", "name": "Bug link"},
        ],
    )

    assert fetch_bug_link_field_id(_config()) == "customfield_10050"


@patch("et.jira.requests.get")
def test_fetch_bug_link_field_id_returns_none_when_not_found(mock_get):
    mock_get.return_value = _json_response(200, [{"id": "summary", "name": "Summary"}])

    assert fetch_bug_link_field_id(_config()) is None


@patch("et.jira.requests.post")
def test_create_issue_returns_key(mock_post):
    mock_post.return_value = _json_response(201, {"id": "10005", "key": "PROJ-42"})

    key = create_issue(_config(), {"summary": "Do the thing"})

    assert key == "PROJ-42"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://example.atlassian.net/rest/api/3/issue"
    assert kwargs["json"] == {"fields": {"summary": "Do the thing"}}


@patch("et.jira.requests.post")
def test_create_issue_raises_on_non_2xx_status(mock_post):
    mock_post.return_value = _json_response(400, {"errorMessages": ["bad request"]})

    with pytest.raises(JiraError, match="400"):
        create_issue(_config(), {"summary": "Do the thing"})


@patch("et.jira.requests.post", side_effect=requests.ConnectionError("no route to host"))
def test_create_issue_wraps_network_errors(mock_post):
    with pytest.raises(JiraError, match="no route to host"):
        create_issue(_config(), {"summary": "Do the thing"})


@patch("et.jira.requests.get")
def test_discover_board_id_filters_by_board_type(mock_get):
    mock_get.return_value = _json_response(200, {"values": [{"id": 7}]})

    board_id = discover_board_id(_config(), "PROJ", board_type="scrum")

    assert board_id == "7"
    _args, kwargs = mock_get.call_args
    assert kwargs["params"] == {"projectKeyOrId": "PROJ", "type": "scrum"}


@patch("et.jira.requests.get")
def test_discover_board_id_omits_type_param_by_default(mock_get):
    mock_get.return_value = _json_response(200, {"values": [{"id": 7}]})

    discover_board_id(_config(), "PROJ")

    _args, kwargs = mock_get.call_args
    assert kwargs["params"] == {"projectKeyOrId": "PROJ"}


@patch("et.jira.requests.get")
def test_fetch_active_sprint_raises_board_without_sprints_error(mock_get):
    mock_get.return_value = _json_response(
        400, {"errorMessages": ["The board does not support sprints"], "errors": {}}
    )

    from et.jira import JiraBoardWithoutSprintsError

    with pytest.raises(JiraBoardWithoutSprintsError, match="does not support sprints"):
        fetch_active_sprint(_config(), "1304")


@patch("et.jira.requests.get")
def test_fetch_active_sprint_raises_generic_error_on_other_400(mock_get):
    mock_get.return_value = _json_response(400, {"errorMessages": ["something else broke"]})

    with pytest.raises(JiraError, match="400"):
        fetch_active_sprint(_config(), "1304")
