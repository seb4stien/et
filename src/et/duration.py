"""Small, dependency-free helpers for formatting/parsing time durations.

Used by `et.cli`/`et.ws` to display a workspace counter's elapsed time
(`format_duration`) and by `et jira log-time [Xh]` to parse a
manually-specified duration (`parse_hours_to_seconds`). Neither function
does any I/O, so they're kept separate from `et.et_extension` (the D-Bus
client that actually reads/writes counters) to stay independently testable
and reusable regardless of how a duration's seconds were obtained.
"""

from __future__ import annotations

import re

_HOURS_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[hH]\s*$")


def format_duration(seconds: float) -> str:
    """Format a number of seconds as e.g. "2h 15m 30s"."""
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s"


def parse_hours_to_seconds(text: str) -> int:
    """Parse an hours-only duration like "2h" or "1.5h" into whole seconds.

    Used for `et jira log-time`'s manual duration argument. Raises
    `ValueError` if `text` isn't a positive number followed by "h" (e.g.
    missing the "h" suffix, non-numeric, zero, or negative).
    """
    match = _HOURS_DURATION_RE.match(text)
    if match is None:
        raise ValueError(f"invalid duration {text!r}: expected a format like '2h' or '1.5h'")

    hours = float(match.group(1))
    if hours <= 0:
        raise ValueError(f"invalid duration {text!r}: must be greater than 0h")

    return round(hours * 3600)


__all__ = ["format_duration", "parse_hours_to_seconds"]
