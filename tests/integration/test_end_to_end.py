"""End-to-end integration tests for the `et` CLI.

Unlike the unit tests (which mock each module's public helpers), these
exercise the *whole* stack — Typer command -> tracker/workspaces ->
gsettings/gnome-extensions -> subprocess — and only replace the two real
process boundaries: `subprocess.run` (the actual `gsettings`/`wmctrl`/
`gnome-extensions` calls) and `shutil.which` (binary discovery).

`FakeSystem` stands in for the machine: it keeps GSettings keys in an
in-memory dict and answers the handful of external commands et shells out
to, so a command's full read/modify/write cycle can be asserted against
persisted state.
"""

from __future__ import annotations

import ast
import subprocess
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from et.cli import app

runner = CliRunner()

WORKSPACE_NAMES = ("org.gnome.desktop.wm.preferences", "workspace-names")
TRACKER_TIMERS = ("org.gnome.shell.extensions.tracker", "timers")
DYNAMIC_WORKSPACES = ("org.gnome.mutter", "dynamic-workspaces")
TRACKER_UUID = "tracker@aliakseiz.github.com"


class FakeSystem:
    """In-memory stand-in for gsettings/wmctrl/gnome-extensions."""

    def __init__(self, active_workspace: int = 0) -> None:
        self.active_workspace = active_workspace
        self.enabled_extensions = {TRACKER_UUID}
        self.gsettings: dict[tuple[str, str], str] = {
            WORKSPACE_NAMES: "@as []",
            TRACKER_TIMERS: "@as []",
            DYNAMIC_WORKSPACES: "false",
        }

    def run(self, args, capture_output=True, text=True, check=False, env=None):
        program = args[0]
        if program == "gsettings":
            return self._run_gsettings(args)
        if program == "wmctrl":
            return self._run_wmctrl()
        if program == "gdbus":
            return self._run_gdbus(args)
        if program == "gnome-extensions":
            return self._run_gnome_extensions(args)
        raise AssertionError(f"unexpected command: {args!r}")

    def _completed(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        return subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _run_gsettings(self, args):
        action = args[1]
        schema, key = args[2], args[3]
        if action == "get":
            return self._completed(stdout=self.gsettings.get((schema, key), "@as []") + "\n")
        if action == "set":
            self.gsettings[(schema, key)] = args[4]
            return self._completed()
        raise AssertionError(f"unexpected gsettings action: {action}")

    def _run_wmctrl(self):
        lines = []
        for index in range(4):
            marker = "*" if index == self.active_workspace else "-"
            lines.append(f"{index}  {marker} DG: 1920x1080  VP: 0,0  WA: 0,0 1920x1080  W{index}")
        return self._completed(stdout="\n".join(lines) + "\n")

    def _run_gdbus(self, args):
        # Simulates the `et` GNOME Shell extension's D-Bus reply, used on
        # Wayland sessions instead of `wmctrl -d`.
        assert args[1] == "call"
        return self._completed(stdout=f"(uint32 {self.active_workspace},)\n")

    def _run_gnome_extensions(self, args):
        action = args[1]
        if action == "list":
            return self._completed(stdout="\n".join(sorted(self.enabled_extensions)) + "\n")
        uuid = args[2]
        if action == "disable":
            self.enabled_extensions.discard(uuid)
        elif action == "enable":
            self.enabled_extensions.add(uuid)
        return self._completed()

    def read_string_array(self, schema: str, key: str) -> list[str]:
        raw = self.gsettings[(schema, key)]
        if raw.startswith("@as "):
            raw = raw[len("@as "):]
        return ast.literal_eval(raw)


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv("ET_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    fake = FakeSystem()
    with (
        patch("shutil.which", return_value="/usr/bin/fake"),
        patch("subprocess.run", side_effect=fake.run),
    ):
        yield fake


@pytest.fixture
def wayland_system(tmp_path, monkeypatch):
    """Same as `system`, but simulating a Wayland session (uses gdbus, not wmctrl)."""
    monkeypatch.setenv("ET_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    fake = FakeSystem()
    with (
        patch("shutil.which", return_value="/usr/bin/fake"),
        patch("subprocess.run", side_effect=fake.run),
    ):
        yield fake


def test_ws_rename_persists_active_workspace_name(system):
    system.active_workspace = 2

    result = runner.invoke(app, ["ws", "rename", "focus"])

    assert result.exit_code == 0, result.output
    assert "Renamed workspace 3 to 'focus'" in result.output
    assert system.read_string_array(*WORKSPACE_NAMES) == ["", "", "focus"]


def test_ws_rename_persists_active_workspace_name_on_wayland(wayland_system):
    wayland_system.active_workspace = 2

    result = runner.invoke(app, ["ws", "rename", "focus"])

    assert result.exit_code == 0, result.output
    assert "Renamed workspace 3 to 'focus'" in result.output
    assert wayland_system.read_string_array(*WORKSPACE_NAMES) == ["", "", "focus"]


