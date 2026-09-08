# Contributing

This document covers the development workflow for `et`: setting up a dev
environment, running tests/lint/static analysis, and testing the bundled
GNOME Shell extension. For user-facing setup and usage, see the
[README](./README.md); for internal design rationale, see
[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).

## Setting up a development environment

This project uses [uv](https://docs.astral.sh/uv/) and exposes common tasks
through a [`Justfile`](./Justfile):

```bash
just install-requirements   # system tools + uv sync + git hooks + GNOME extension
```

This installs the GNOME test tooling, runs `uv sync --all-extras --dev`,
installs the [`prek`](https://github.com/j178/prek) (pre-commit) git hook,
and installs/enables the bundled GNOME Shell extension (see
[The `et` GNOME Shell extension](./README.md#the-et-gnome-shell-extension)
in the README).

`prek` runs linting, static validation, Python tests, and the fast GJS unit
tests on every commit. It does not launch a real GNOME Shell.

## Common tasks

```bash
just install-requirements   # system tools + uv sync + hooks + GNOME extension
just dev                    # uv run et --help
just lint                   # ruff + ShellCheck
just static                 # mypy + extension metadata/schema validation
just test                   # pytest + coverage + deterministic GJS tests
just test-integ             # Python integration + real headless Shell test
just test-extension         # isolated interactive nested Shell
just test-extension-live    # isolated automated real-Shell lifecycle test
just package-extension      # build and validate only the extension ZIP
just ops                    # build the Python package and extension ZIP
```

Before opening a PR, make sure `just lint`, `just static`, and `just test`
all pass (this is exactly what `prek`/CI check).

`just ops` writes the extensions.gnome.org-ready archive to
`dist/et@seb4stien.github.com.shell-extension.zip`. The package contains
only the extension runtime, preferences, schema source, stylesheet,
metadata, and license; generated schemas and repository tooling are
excluded.

## Testing the GNOME Shell extension

The extension has three test layers:

- `just test` runs the Python suite and deterministic GJS unit tests for
  the extension's persisted counter store and display formatting. These
  tests do not start GNOME Shell or access the host's GNOME settings.
- `just test-extension-live` starts a real headless GNOME Shell and
  exercises the complete D-Bus lifecycle, including timing, remapping,
  persistence, and disable/re-enable cleanup. It uses a disposable HOME
  and XDG tree.
- `just test-extension` starts an interactive nested Wayland Shell for
  visual checks. The extension, dconf database, caches, runtime directory,
  and enabled extension list all live under a temporary directory that is
  deleted on exit; nothing is installed into the host user's extension
  directory. A working user systemd session is required so every helper
  daemon is reaped.

GitHub Actions runs the real-Shell lifecycle test on Ubuntu 24.04/GNOME 46
and Ubuntu 26.04/GNOME 50, matching `metadata.json`.

For full-desktop visual compatibility checks, use disposable GNOME Boxes or
QEMU snapshots for both Ubuntu releases:

1. Build the test artifact with `just package-extension`.
2. Restore a clean VM snapshot and copy
   `dist/et@seb4stien.github.com.shell-extension.zip` into the VM.
3. Install the ZIP with `gnome-extensions install --force <zip>`, log out
   and back in, then enable the extension.
4. Check preferences, the top-panel display, workspace-switcher labels,
   workspace changes, lock/unlock counter pausing, and disable/re-enable.
5. Revert the VM snapshot after the test.
