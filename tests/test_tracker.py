"""Tests for et.tracker, mocking the gsettings, gnome_extensions, and workspaces layers."""

from __future__ import annotations

import json
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from et.gnome_extensions import GnomeExtensionsError
from et.gsettings import GSettingsError
from et.tracker import (
    TrackerError,
    build_new_timer,
    find_timer_for_workspace,
    format_duration,
    parse_hours_to_seconds,
    prepare_timer_for_workspace,
)

SETTINGS_SENTINEL = {"id": "settings", "totalTimeSelected": True}


def test_find_timer_for_workspace_returns_matching_entry():
    entries = [
        SETTINGS_SENTINEL,
        {"id": "a", "name": "mails", "workspaceId": 0},
        {"id": "b", "name": "handson", "workspaceId": 1},
    ]
    assert find_timer_for_workspace(entries, 1) == {"id": "b", "name": "handson", "workspaceId": 1}


def test_find_timer_for_workspace_ignores_sentinel_and_returns_none_when_missing():
    entries = [SETTINGS_SENTINEL, {"id": "a", "name": "mails", "workspaceId": 0}]
    assert find_timer_for_workspace(entries, 5) is None


def test_build_new_timer_matches_tracker_default_shape():
    timer = build_new_timer(2, "focus")
    assert timer["name"] == "focus"
    assert timer["workspaceId"] == 2
    assert timer["timeElapsed"] == 0
    assert timer["running"] is False
    assert timer["selected"] is False
    assert timer["autoResume"] is True
    assert isinstance(timer["id"], str) and timer["id"]


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch("et.tracker.gsettings.read_string_array", return_value=[])
def test_prepare_timer_creates_timer_for_explicit_index(mock_read, mock_write, mock_reload):
    name, created = prepare_timer_for_workspace(4, 5)
    assert (name, created) == ("ET-5", True)
    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    assert written_entries[0]["workspaceId"] == 4


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[
        json.dumps(
            {
                "id": "existing",
                "name": "mails",
                "timeElapsed": 42,
                "running": True,
                "selected": False,
                "workspaceId": 2,
            }
        ),
    ],
)
def test_prepare_timer_resets_reused_slot_time(mock_read, mock_write, mock_reload):
    name, created = prepare_timer_for_workspace(2, 3)
    assert (name, created) == ("mails", False)
    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    assert written_entries[0]["timeElapsed"] == 0
    assert written_entries[0]["running"] is False


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[
        json.dumps(
            {
                "id": "existing",
                "name": "ET-3",
                "timeElapsed": 0,
                "running": False,
                "selected": False,
                "workspaceId": 2,
            }
        ),
    ],
)
def test_prepare_timer_is_noop_when_reused_slot_already_zero(mock_read, mock_write, mock_reload):
    name, created = prepare_timer_for_workspace(2, 3)
    assert (name, created) == ("ET-3", False)
    mock_write.assert_not_called()


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[
        json.dumps(SETTINGS_SENTINEL),
        json.dumps(
            {"id": "stale", "name": "ET-9", "timeElapsed": 99, "workspaceId": 8}
        ),
        json.dumps(
            {"id": "keep", "name": "my-notes", "timeElapsed": 7, "workspaceId": 8}
        ),
    ],
)
def test_prepare_timer_removes_orphaned_et_timers_beyond_count(mock_read, mock_write, mock_reload):
    name, created = prepare_timer_for_workspace(0, 3)
    assert (name, created) == ("ET-1", True)
    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    names = [entry.get("name") for entry in written_entries]
    assert "ET-9" not in names  # orphaned et timer beyond the workspace count
    assert "my-notes" in names  # user-named timer left untouched
    assert SETTINGS_SENTINEL in written_entries
    assert "ET-1" in names


@patch("et.tracker.reload_around", return_value=nullcontext())
@patch("et.tracker.gsettings.write_string_array")
@patch(
    "et.tracker.gsettings.read_string_array",
    return_value=[json.dumps(SETTINGS_SENTINEL)],
)
def test_prepare_timer_preserves_sentinel_entry(mock_read, mock_write, mock_reload):
    prepare_timer_for_workspace(0, 1)
    written_entries = [json.loads(raw) for raw in mock_write.call_args[0][2]]
    assert written_entries[0] == SETTINGS_SENTINEL
    assert len(written_entries) == 2


@patch("et.tracker.gsettings.read_string_array", side_effect=GSettingsError("no schema"))
def test_prepare_timer_wraps_gsettings_error(mock_read):
    with pytest.raises(TrackerError, match="Tracker GNOME extension"):
        prepare_timer_for_workspace(0, 1)


@patch("et.tracker.reload_around", side_effect=GnomeExtensionsError("could not disable"))
@patch("et.tracker.gsettings.write_string_array")
@patch("et.tracker.gsettings.read_string_array", return_value=[])
def test_prepare_timer_wraps_gnome_extensions_error(mock_read, mock_write, mock_reload):
    with pytest.raises(TrackerError, match="reload the Tracker extension"):
        prepare_timer_for_workspace(0, 1)
    mock_write.assert_not_called()


def test_format_duration_renders_hours_minutes_seconds():
    assert format_duration(3725) == "1h 2m 5s"
    assert format_duration(0) == "0h 0m 0s"
    assert format_duration(59.9) == "0h 0m 59s"


@pytest.mark.parametrize(
    ("text", "expected_seconds"),
    [
        ("2h", 7200),
        ("1h", 3600),
        ("1.5h", 5400),
        ("0.5h", 1800),
        ("2H", 7200),
        (" 2h ", 7200),
    ],
)
def test_parse_hours_to_seconds_parses_valid_durations(text, expected_seconds):
    assert parse_hours_to_seconds(text) == expected_seconds


@pytest.mark.parametrize(
    "text",
    ["2", "2m", "2h30m", "abc", "", "1.5", "-1h", "0h"],
)
def test_parse_hours_to_seconds_rejects_invalid_durations(text):
    with pytest.raises(ValueError, match="invalid duration"):
        parse_hours_to_seconds(text)
