[private]
default:
    @just --list --unsorted

install-requirements:
    scripts/install-gnome-test-requirements.sh
    uv sync --all-extras --dev
    uv run prek install
    scripts/install-gnome-shell-extension.sh

dev:
    uv run et --help

lint:
    uv run ruff check .
    shellcheck scripts/*.sh scripts/lib/*.sh

static:
    uv run mypy src
    python3 scripts/check-gnome-shell-extension.py gnome-extension/et@seb4stien.github.com
    glib-compile-schemas --strict --dry-run gnome-extension/et@seb4stien.github.com/schemas

test:
    uv run pytest
    gjs -m tests/gnome-extension/test-extension.js

test-integ:
    uv run pytest tests/integration --no-cov
    scripts/check-gnome-shell-extension-live.sh

test-extension:
    scripts/test-gnome-shell-extension.sh

test-extension-live *ARGS:
    scripts/check-gnome-shell-extension-live.sh {{ARGS}}

package-extension:
    scripts/package-gnome-shell-extension.sh

ops:
    uv build
    just package-extension
