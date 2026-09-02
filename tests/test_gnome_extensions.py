"""Tests for et.gnome_extensions, mocking the gnome-extensions subprocess calls."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from et.gnome_extensions import (
    GnomeExtensionsError,
    disable_extension,
    enable_extension,
    is_extension_enabled,
    reload_around,
)

UUID = "tracker@aliakseiz.github.com"


def _completed(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@patch("et.gnome_extensions.shutil.which", return_value="/usr/bin/gnome-extensions")
@patch("et.gnome_extensions.subprocess.run")
def test_is_extension_enabled_true_when_listed(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout=f"other-ext@example.com\n{UUID}\n")
    assert is_extension_enabled(UUID) is True


@patch("et.gnome_extensions.shutil.which", return_value="/usr/bin/gnome-extensions")
@patch("et.gnome_extensions.subprocess.run")
def test_is_extension_enabled_false_when_not_listed(mock_run, _mock_which):
    mock_run.return_value = _completed(stdout="other-ext@example.com\n")
    assert is_extension_enabled(UUID) is False


@patch("et.gnome_extensions.shutil.which", return_value=None)
def test_is_extension_enabled_raises_when_binary_missing(_mock_which):
    with pytest.raises(GnomeExtensionsError, match="gnome-extensions"):
        is_extension_enabled(UUID)


@patch("et.gnome_extensions.time.sleep")
@patch("et.gnome_extensions.shutil.which", return_value="/usr/bin/gnome-extensions")
@patch("et.gnome_extensions.subprocess.run")
def test_is_extension_enabled_retries_then_succeeds(mock_run, _mock_which, mock_sleep):
    mock_run.side_effect = [
        _completed(stderr="Failed to connect to GNOME Shell", returncode=1),
        _completed(stderr="Failed to connect to GNOME Shell", returncode=1),
        _completed(stdout=f"{UUID}\n"),
    ]
    assert is_extension_enabled(UUID) is True
    assert mock_run.call_count == 3
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(5)


@patch("et.gnome_extensions.time.sleep")
@patch("et.gnome_extensions.shutil.which", return_value="/usr/bin/gnome-extensions")
@patch("et.gnome_extensions.subprocess.run")
def test_is_extension_enabled_raises_after_max_attempts(mock_run, _mock_which, mock_sleep):
    mock_run.return_value = _completed(stderr="Failed to connect to GNOME Shell", returncode=1)
    with pytest.raises(GnomeExtensionsError, match="list failed"):
        is_extension_enabled(UUID)
    assert mock_run.call_count == 3
    assert mock_sleep.call_count == 2


@patch("et.gnome_extensions.shutil.which", return_value="/usr/bin/gnome-extensions")
@patch("et.gnome_extensions.subprocess.run")
def test_disable_extension_raises_on_nonzero_exit(mock_run, _mock_which):
    mock_run.return_value = _completed(stderr="boom", returncode=1)
    with pytest.raises(GnomeExtensionsError, match="disable failed"):
        disable_extension(UUID)


@patch("et.gnome_extensions.shutil.which", return_value="/usr/bin/gnome-extensions")
@patch("et.gnome_extensions.subprocess.run")
def test_enable_extension_raises_on_nonzero_exit(mock_run, _mock_which):
    mock_run.return_value = _completed(stderr="boom", returncode=1)
    with pytest.raises(GnomeExtensionsError, match="enable failed"):
        enable_extension(UUID)


@patch("et.gnome_extensions.enable_extension")
@patch("et.gnome_extensions.disable_extension")
@patch("et.gnome_extensions.is_extension_enabled", return_value=True)
def test_reload_around_disables_and_reenables_when_enabled(
    mock_is_enabled, mock_disable, mock_enable
):
    with reload_around(UUID):
        mock_disable.assert_called_once_with(UUID)
        mock_enable.assert_not_called()
    mock_enable.assert_called_once_with(UUID)


@patch("et.gnome_extensions.enable_extension")
@patch("et.gnome_extensions.disable_extension")
@patch("et.gnome_extensions.is_extension_enabled", return_value=False)
def test_reload_around_is_noop_when_not_enabled(mock_is_enabled, mock_disable, mock_enable):
    with reload_around(UUID):
        pass
    mock_disable.assert_not_called()
    mock_enable.assert_not_called()


@patch("et.gnome_extensions.enable_extension")
@patch("et.gnome_extensions.disable_extension")
@patch("et.gnome_extensions.is_extension_enabled", return_value=True)
def test_reload_around_reenables_even_if_block_raises(mock_is_enabled, mock_disable, mock_enable):
    with pytest.raises(ValueError), reload_around(UUID):
        raise ValueError("boom")
    mock_enable.assert_called_once_with(UUID)
