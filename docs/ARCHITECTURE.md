# Architecture

This document covers the internal design of `et` and its companion GNOME
Shell extension — the *why* behind how the pieces fit together. For
day-to-day usage, see the [README](../README.md); for the development
workflow (running tests, linting, building), see
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Overview

`et` is a [Typer](https://typer.tiangolo.com/)-based CLI (`src/et/cli.py`)
that's a thin presentation layer over a set of Typer-free, independently
unit-testable modules:

- `et.config` — loads/saves `~/.config/et/config.yaml` (the `jira` block
  and the `workspaces` list).
- `et.workspaces` / `et.ws` — GNOME workspace management (renaming,
  deleting, reordering) via `gsettings`.
- `et.et_extension` — talks to the bundled GNOME Shell extension over
  D-Bus (`gdbus`) to read/prepare per-workspace counters and display state.
- `et.jira` — a thin Jira Cloud REST API client (`requests`-based).
- `et.jira_create`, `et.jira_time`, `et.task`, `et.git_branch`,
  `et.config_wizard` — orchestration modules that combine the above to
  implement each `et` subcommand's business logic, each taking injected
  prompt/confirm/warn callbacks so they can be unit tested without Typer
  or a live terminal.

Business-logic modules never import `typer`; `cli.py` is the only module
that wires interactive prompts to `typer.prompt`/`typer.confirm` and
converts raised errors into `Error: ...` messages + a non-zero exit code.
This split is what lets almost all of `et`'s behavior be tested with plain
pytest, without spawning a shell or a real GNOME session.

## The `et` GNOME Shell extension

`wmctrl` relies on the X11 window-manager protocol, so it can't detect the
active workspace on a Wayland session (the default since Ubuntu 26.04).
Since GNOME Shell doesn't expose the active workspace over D-Bus by
default, this repo ships a companion extension,
[`gnome-extension/et@seb4stien.github.com`](../gnome-extension/et@seb4stien.github.com),
that exposes Shell state through D-Bus. It also owns the per-workspace
counters: when an `et`-managed `dynamic` workspace is active, its counter
runs automatically; switching away or locking the session pauses it, and
returning or unlocking resumes it. Ordinary keyboard/mouse inactivity
still counts.

The extension is **ticket-system agnostic** by design. The CLI can give it
a workspace label and reference estimate (for Jira workspaces these are the
issue summary and original estimate), but the extension treats them as
generic display values it neither parses nor validates — this keeps the
extension reusable if `et` ever grows a second ticket-system backend. Its
preferences independently control whether the label, estimate, and current
counter appear, and whether the display is shown in the top panel and
GNOME workspace switcher. The default is to show the label, original
estimate, and counter on both surfaces. Whenever the top panel display is
enabled, a small icon is always shown there as a persistent indicator that
the extension is installed and active, even before any workspace has been
prepared; the label/estimate/counter text is appended alongside it once a
workspace has one.

### Why the install script copies rather than symlinks the extension

`scripts/install-gnome-shell-extension.sh` *copies* the extension into
`~/.local/share/gnome-shell/extensions/et@seb4stien.github.com` by default
rather than symlinking it, so the installed copy keeps working even if the
checkout is later re-provisioned (e.g. a fresh clone into a re-created
workspace) on a timeline independent of the GNOME session. GNOME Shell
only scans the extensions directory once at startup, and a symlink whose
target doesn't exist yet at that exact moment gets silently skipped and
never picked up until a full restart happens *after* the target exists.
The `--dev`/`--symlink` flag exists for the opposite tradeoff: when
actively editing `extension.js`, a symlink means you don't need to re-run
the install script after every change (a Shell restart is still required
to pick up new code either way).

## Known limitations

`et jira start` applies its changes (extension counter, then config, then
GNOME workspace names) sequentially without a rollback. A failure partway
through can leave the config and live GNOME state temporarily out of sync;
re-running the command reconciles them.
