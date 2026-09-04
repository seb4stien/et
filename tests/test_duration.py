"""Tests for et.duration: format_duration/parse_hours_to_seconds."""

from __future__ import annotations

import pytest

from et.duration import format_duration, parse_hours_to_seconds


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
