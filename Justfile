[private]
default:
    @just --list --unsorted

install-requirements:
    uv sync --all-extras --dev
    uv run prek install
    scripts/install-gnome-shell-extension.sh

dev:
    uv run et --help

lint:
    uv run ruff check .

static:
    uv run mypy src

test:
    uv run pytest

test-integ:
    uv run pytest tests/integration --no-cov

ops:
    uv build
