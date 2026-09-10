"""Tests for the GNOME Shell extension validation and isolation tooling."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
EXTENSION_DIR = REPO_ROOT / "gnome-extension" / "et@seb4stien.github.com"
VALIDATOR = REPO_ROOT / "scripts" / "check-gnome-shell-extension.py"
ISOLATED_ENV = REPO_ROOT / "scripts" / "lib" / "with-isolated-gnome-shell-env.sh"
ENABLE_EXTENSION_SETTING = REPO_ROOT / "scripts" / "lib" / "enable-extension-setting.sh"


def test_validator_accepts_source_and_complete_archive(tmp_path: Path) -> None:
    archive = tmp_path / "extension.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in EXTENSION_DIR.rglob("*"):
            if path.is_file() and path.name != "gschemas.compiled":
                output.write(path, path.relative_to(EXTENSION_DIR))

    result = subprocess.run(
        ["python3", str(VALIDATOR), str(EXTENSION_DIR), "--archive", str(archive)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_validator_rejects_missing_display_module(tmp_path: Path) -> None:
    source = tmp_path / "extension"
    shutil.copytree(EXTENSION_DIR, source)
    (source / "lib" / "display.js").unlink()

    result = subprocess.run(
        ["python3", str(VALIDATOR), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "lib/display.js" in result.stderr


def test_isolated_environment_stages_extension_without_using_host_home(tmp_path: Path) -> None:
    host_home = tmp_path / "host-home"
    host_home.mkdir()
    marker = host_home / "marker"
    marker.write_text("unchanged")
    output = tmp_path / "environment.json"
    temporary_parent = tmp_path / "custom-tmp"
    temporary_parent.mkdir()
    command = (
        'python3 -c "import json, os, pathlib; '
        "root = pathlib.Path(os.environ['ET_TEST_ROOT']); "
        "extension = pathlib.Path(os.environ['ET_EXTENSION_DIR']); "
        "assert pathlib.Path(os.environ['HOME']).is_relative_to(root); "
        "assert pathlib.Path(os.environ['XDG_CONFIG_HOME']).is_relative_to(root); "
        "assert (extension / 'extension.js').is_file(); "
        "assert (extension / 'lib' / 'display.js').is_file(); "
        "assert (extension / 'schemas' / 'gschemas.compiled').is_file(); "
        "pathlib.Path(os.environ['OUTPUT']).write_text(json.dumps({'root': str(root)}))\""
    )

    result = subprocess.run(
        [str(ISOLATED_ENV), "bash", "-c", command],
        env={
            **os.environ,
            "HOME": str(host_home),
            "OUTPUT": str(output),
            "TMPDIR": str(temporary_parent),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "unchanged"
    staged_root = Path(json.loads(output.read_text())["root"])
    assert staged_root.parent == temporary_parent
    assert not staged_root.exists()


def test_enable_extension_setting_handles_typed_empty_array(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls"
    fake_gsettings = fake_bin / "gsettings"
    fake_gsettings.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = get ]; then\n"
        "    echo '@as []'\n"
        "else\n"
        "    printf '%s\\n' \"$*\" >\"${CALLS_FILE}\"\n"
        "fi\n"
    )
    fake_gsettings.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {ENABLE_EXTENSION_SETTING}; "
            "enable_gnome_extension_in_settings et@example.com",
        ],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "CALLS_FILE": str(calls)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text().strip() == (
        "set org.gnome.shell enabled-extensions ['et@example.com']"
    )


@pytest.mark.parametrize("persistent", [False, True])
def test_cleanup_retries_removal_failure(tmp_path: Path, persistent: bool) -> None:
    test_root = tmp_path / "et-gnome-shell-test-cleanup"
    (test_root / "runtime" / "doc").mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls"
    real_rm = shutil.which("rm")
    assert real_rm is not None
    fake_rm = fake_bin / "rm"
    fake_rm.write_text(
        "#!/usr/bin/env bash\n"
        'echo called >> "$CALLS_FILE"\n'
        'if [ "$PERSISTENT" = true ] || [ "$(wc -l < "$CALLS_FILE")" -eq 1 ]; then\n'
        '    echo "simulated portal teardown race" >&2\n'
        "    exit 1\n"
        "fi\n"
        'exec "$REAL_RM" "$@"\n'
    )
    fake_rm.chmod(0o755)
    result = subprocess.run(
        [str(REPO_ROOT / "scripts/lib/remove-isolated-gnome-shell-env.sh"), str(test_root)],
        env={
            **os.environ,
            "TMPDIR": str(tmp_path),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CALLS_FILE": str(calls),
            "REAL_RM": real_rm,
            "PERSISTENT": str(persistent).lower(),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == (1 if persistent else 0), result.stderr
    assert test_root.exists() is persistent
    assert len(calls.read_text().splitlines()) == (3 if persistent else 2)
