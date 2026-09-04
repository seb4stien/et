"""Thin wrapper around the `gsettings` CLI.

Shells out to `gsettings` to read/write GNOME's own settings: workspace
names (an array-of-strings `as` value), the fixed workspace count, and
whether dynamic workspaces are enabled. Has no Typer/CLI dependency so
callers can unit test by mocking `subprocess.run`.
"""

from __future__ import annotations

import ast
import shutil
import subprocess


class GSettingsError(RuntimeError):
    """Raised when a `gsettings` read/write operation cannot be completed."""


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise GSettingsError(f"required command not found: {name}")


def read_string_array(schema: str, key: str) -> list[str]:
    """Return the current array-of-strings value of `schema`'s `key`."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "get", schema, key],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GSettingsError(f"gsettings get failed: {result.stderr.strip()}")

    raw = result.stdout.strip()
    if raw.startswith("@as "):
        raw = raw[len("@as "):]
    try:
        values = ast.literal_eval(raw)
    except (ValueError, SyntaxError) as exc:
        raise GSettingsError(f"could not parse {schema} {key} value: {raw!r}") from exc

    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise GSettingsError(f"unexpected {schema} {key} value: {raw!r}")

    return values


def _set_raw(schema: str, key: str, raw_value: str) -> None:
    """Write a raw GVariant-syntax value string to `schema`'s `key`."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "set", schema, key, raw_value],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GSettingsError(f"gsettings set failed: {result.stderr.strip()}")


def write_string_array(schema: str, key: str, values: list[str]) -> None:
    """Write the given list of strings to `schema`'s `key`."""
    _set_raw(schema, key, repr(values))


def set_int(schema: str, key: str, value: int) -> None:
    """Write an integer value to `schema`'s `key`."""
    _set_raw(schema, key, str(value))


def _get_raw(schema: str, key: str) -> str:
    """Return the raw, stripped stdout of `gsettings get schema key`."""
    _require_binary("gsettings")
    result = subprocess.run(
        ["gsettings", "get", schema, key],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GSettingsError(f"gsettings get failed: {result.stderr.strip()}")
    return result.stdout.strip()


def read_boolean(schema: str, key: str) -> bool:
    """Return the current boolean value of `schema`'s `key`."""
    raw = _get_raw(schema, key)
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise GSettingsError(f"unexpected {schema} {key} boolean value: {raw!r}")


def read_int(schema: str, key: str) -> int:
    """Return the current integer value of `schema`'s `key`.

    Tolerates GVariant type-annotated output (e.g. "uint32 4") by taking the
    trailing token.
    """
    raw = _get_raw(schema, key)
    token = raw.split()[-1] if raw else raw
    try:
        return int(token)
    except ValueError as exc:
        raise GSettingsError(f"could not parse {schema} {key} value: {raw!r}") from exc
