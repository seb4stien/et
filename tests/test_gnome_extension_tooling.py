"""Tests for the GNOME Shell extension validation and isolation tooling."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
EXTENSION_DIR = REPO_ROOT / "gnome-extension" / "et@seb4stien.github.com"
VALIDATOR = REPO_ROOT / "scripts" / "check-gnome-shell-extension.py"
ISOLATED_ENV = REPO_ROOT / "scripts" / "lib" / "with-isolated-gnome-shell-env.sh"


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
