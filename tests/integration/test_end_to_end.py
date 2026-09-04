"""End-to-end integration tests for the `et` CLI.

Unlike the unit tests (which mock each module's public helpers), these
exercise the *whole* stack — Typer command -> workspaces/et_extension ->
gsettings/gdbus -> subprocess — and only replace the two real process
boundaries: `subprocess.run` (the actual `gsettings`/`wmctrl`/`gdbus` calls)
and `shutil.which` (binary discovery).

`FakeSystem` stands in for the machine: it keeps GSettings keys and the et
GNOME Shell extension's per-workspace counters in memory, and answers the
handful of external commands et shells out to, so a command's full
read/modify/write cycle can be asserted against persisted state.
"""

from __future__ import annotations

import ast
import re
import subprocess
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from et.cli import app

runner = CliRunner()

WORKSPACE_NAMES = ("org.gnome.desktop.wm.preferences", "workspace-names")
NUM_WORKSPACES = ("org.gnome.desktop.wm.preferences", "num-workspaces")
DYNAMIC_WORKSPACES = ("org.gnome.mutter", "dynamic-workspaces")


class FakeSystem:
    """In-memory stand-in for gsettings/wmctrl/the et GNOME Shell extension's D-Bus service."""

    def __init__(self, active_workspace: int = 0) -> None:
        self.active_workspace = active_workspace
        # workspace index -> {"elapsed": int, "running": bool}, mirroring the
        # et extension's own per-workspace counter state. A missing entry
        # means "never prepared", which reads the same as a fresh counter
        # (elapsed=0, running=False) -- the "always present" model described
        # in et.et_extension.
        self.counters: dict[int, dict[str, object]] = {}
        # workspace index -> {"label": str, "estimateSeconds": int}, the
        # other half of a prepared workspace's entry (kept in a separate
        # dict here purely for fake-implementation convenience; the real
        # extension stores both together). Follows the same
        # missing-means-never-prepared convention as `counters`.
        self.metadata: dict[int, dict[str, object]] = {}
        self.gsettings: dict[tuple[str, str], str] = {
            WORKSPACE_NAMES: "@as []",
            NUM_WORKSPACES: "1",
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

    @staticmethod
    def _parse_uint(token: str) -> int:
        return int(token.split()[-1])

    @staticmethod
    def _parse_gvariant_string(token: str) -> str:
        match = re.match(r"'((?:[^'\\]|\\.)*)'", token)
        assert match is not None, f"unparseable gvariant string: {token!r}"
        return re.sub(r"\\(.)", r"\1", match.group(1))

    @staticmethod
    def _format_gvariant_string(value: str) -> str:
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    @staticmethod
    def _parse_moves(token: str) -> list[tuple[int, int]]:
        pairs = re.findall(r"uint32\s+(\d+)\s*,\s*uint32\s+(\d+)", token)
        return [(int(old), int(new)) for old, new in pairs]

    def _counter(self, index: int) -> dict[str, object]:
        return self.counters.get(index, {"elapsed": 0, "running": False})

    def _run_gdbus(self, args):
        # Generically dispatches every `org.gnome.Shell.Extensions.Et`
        # method used by et.et_extension, keeping counters state in memory
        # instead of hardcoding a single reply the way the old fake did.
        assert args[1] == "call"
        method = args[8].rsplit(".", 1)[-1]
        call_args = args[9:]

        if method == "GetActiveWorkspaceIndex":
            return self._completed(stdout=f"(uint32 {self.active_workspace},)\n")

        if method == "PrepareWorkspace":
            index = self._parse_uint(call_args[0])
            self.counters[index] = {"elapsed": 0, "running": False}
            self.metadata[index] = {
                "label": self._parse_gvariant_string(call_args[1]),
                "estimateSeconds": self._parse_uint(call_args[2]),
            }
            return self._completed(stdout="()\n")

        if method == "GetWorkspaceCounter":
            index = self._parse_uint(call_args[0])
            counter = self._counter(index)
            running = "true" if counter["running"] else "false"
            return self._completed(stdout=f"(uint64 {counter['elapsed']}, {running})\n")

        if method == "GetWorkspaceMetadata":
            index = self._parse_uint(call_args[0])
            if index not in self.metadata:
                return self._completed(
                    returncode=1,
                    stderr=(
                        "Error: GDBus.Error:org.gnome.Shell.Extensions.Et.Error.NotFound: "
                        f"workspace {index} has no prepared counter"
                    ),
                )
            entry = self.metadata[index]
            label = self._format_gvariant_string(str(entry["label"]))
            return self._completed(stdout=f"({label}, uint64 {entry['estimateSeconds']})\n")

        if method == "ResetWorkspaceCounter":
            index = self._parse_uint(call_args[0])
            self.counters[index] = {"elapsed": 0, "running": False}
            return self._completed(stdout="()\n")

        if method == "RemoveWorkspace":
            index = self._parse_uint(call_args[0])
            self.counters.pop(index, None)
            self.metadata.pop(index, None)
            return self._completed(stdout="()\n")

        if method == "RemapWorkspaces":
            moves = self._parse_moves(call_args[0])
            # Snapshot every source before writing any destination, so a
            # chain of moves (e.g. a left-shift) reads pre-move data only --
            # mirroring the extension's own atomic semantics.
            snapshot = {old: self._counter(old) for old, _new in moves}
            metadata_snapshot = {old: self.metadata.get(old) for old, _new in moves}
            targets = {new for _old, new in moves}
            for old, new in moves:
                self.counters[new] = snapshot[old]
                if metadata_snapshot[old] is not None:
                    self.metadata[new] = metadata_snapshot[old]
                else:
                    self.metadata.pop(new, None)
            for old, _new in moves:
                if old not in targets:
                    self.counters.pop(old, None)
                    self.metadata.pop(old, None)
            return self._completed(stdout="()\n")

        if method == "SetWorkspaceMetadata":
            index = self._parse_uint(call_args[0])
            self.metadata[index] = {
                "label": self._parse_gvariant_string(call_args[1]),
                "estimateSeconds": self._parse_uint(call_args[2]),
            }
            return self._completed(stdout="()\n")

        raise AssertionError(f"unexpected et extension method: {method}")

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


def test_ws_rename_syncs_label_of_an_already_prepared_workspace(system):
    system.active_workspace = 1
    system.counters[1] = {"elapsed": 900, "running": False}
    system.metadata[1] = {"label": "Old label", "estimateSeconds": 3600}

    result = runner.invoke(app, ["ws", "rename", "New name"])

    assert result.exit_code == 0, result.output
    assert "Renamed workspace 2 to 'New name'" in result.output
    # Label follows the rename, estimate is preserved, counter is untouched.
    assert system.metadata[1] == {"label": "New name", "estimateSeconds": 3600}
    assert system.counters[1] == {"elapsed": 900, "running": False}


def test_ws_rename_all_syncs_label_of_already_prepared_workspaces(system, tmp_path):
    _write_config(
        tmp_path,
        "  - name: One\n  - name: Two\n",
    )
    system.metadata[1] = {"label": "Stale label", "estimateSeconds": 1800}
    system.counters[1] = {"elapsed": 42, "running": False}

    result = runner.invoke(app, ["ws", "rename", "--all"])

    assert result.exit_code == 0, result.output
    assert system.metadata[1] == {"label": "Two", "estimateSeconds": 1800}
    assert system.counters[1] == {"elapsed": 42, "running": False}
    # Workspace 0 was never prepared -- renaming it must not start tracking.
    assert 0 not in system.metadata
    assert 0 not in system.counters


def test_ws_rename_does_not_error_on_an_unprepared_workspace(system):
    system.active_workspace = 0

    result = runner.invoke(app, ["ws", "rename", "static workspace"])

    assert result.exit_code == 0, result.output
    assert "Renamed workspace 1 to 'static workspace'" in result.output
    assert 0 not in system.metadata
    assert 0 not in system.counters


def _write_config(tmp_path, workspaces_yaml: str) -> None:
    (tmp_path / "config.yaml").write_text(f"workspaces:\n{workspaces_yaml}")


def test_info_shows_extension_counter_elapsed_time(system, tmp_path):
    _write_config(
        tmp_path,
        "  - name: Fix login timeout\n    ref: jira:PROJ-1\n    description: Fix it\n",
    )
    system.active_workspace = 0
    system.counters[0] = {"elapsed": 5445, "running": True}

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0, result.output
    assert "Workspace 1: Fix login timeout" in result.output
    assert "Time spent: 1h 30m 45s (running)" in result.output


def test_ws_delete_shifts_remaining_workspace_counter_into_freed_slot(system, tmp_path):
    _write_config(
        tmp_path,
        "  - name: ISD-A\n    ref: jira:ISD-A\n"
        "  - name: ET-2\n"
        "  - name: ISD-C\n    ref: jira:ISD-C\n",
    )
    system.gsettings[WORKSPACE_NAMES] = repr(["ISD-A", "ET-2", "ISD-C"])
    system.gsettings[NUM_WORKSPACES] = "3"
    system.active_workspace = 1
    system.counters[0] = {"elapsed": 100, "running": False}
    system.counters[1] = {"elapsed": 50, "running": False}
    system.counters[2] = {"elapsed": 200, "running": True}

    result = runner.invoke(app, ["ws", "delete"])

    assert result.exit_code == 0, result.output
    assert "Deleted workspace 2 (now managing 2 workspaces)" in result.output
    assert system.gsettings[NUM_WORKSPACES] == "2"
    # `rename_all_workspaces` only overwrites the renamed prefix, so the
    # stale trailing entry from the now-unused slot 2 is left in place.
    assert system.read_string_array(*WORKSPACE_NAMES) == ["ISD-A", "ISD-C", "ISD-C"]
    # Slot 1's own (unrelated) counter is discarded, and slot 2's counter
    # follows its workspace into slot 1 via a single atomic remap.
    assert system.counters[0] == {"elapsed": 100, "running": False}
    assert system.counters[1] == {"elapsed": 200, "running": True}
    assert 2 not in system.counters


def test_ws_delete_rejects_linked_workspace_without_force(system, tmp_path):
    _write_config(tmp_path, "  - name: ISD-A\n    ref: jira:ISD-A\n  - name: ET-2\n")
    system.gsettings[WORKSPACE_NAMES] = repr(["ISD-A", "ET-2"])
    system.gsettings[NUM_WORKSPACES] = "2"
    system.active_workspace = 0

    result = runner.invoke(app, ["ws", "delete"])

    assert result.exit_code == 1
    assert "linked to ISD-A" in result.output


