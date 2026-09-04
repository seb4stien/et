#!/usr/bin/env python3
"""Validate the bundled GNOME Shell extension source and optional archive."""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

UUID = "et@seb4stien.github.com"
SCHEMA_ID = "org.gnome.shell.extensions.et"
REQUIRED_DBUS_METHODS = {
    "GetActiveWorkspaceIndex",
    "PrepareWorkspace",
    "GetWorkspaceCounter",
    "GetWorkspaceMetadata",
    "ResetWorkspaceCounter",
    "RemoveWorkspace",
    "RemapWorkspaces",
    "SetWorkspaceMetadata",
}
REQUIRED_SOURCE_FILES = {
    "LICENSE",
    "extension.js",
    "lib/display.js",
    "lib/workspaceStore.js",
    "lib/workspaceSwitcherCompat.js",
    "metadata.json",
    "prefs.js",
    f"schemas/{SCHEMA_ID}.gschema.xml",
    "stylesheet.css",
}


def fail(message: str) -> None:
    raise ValueError(message)


def validate_source(source_dir: Path) -> None:
    missing = sorted(path for path in REQUIRED_SOURCE_FILES if not (source_dir / path).is_file())
    if missing:
        fail(f"extension source is missing required files: {', '.join(missing)}")

    metadata = json.loads((source_dir / "metadata.json").read_text())
    if metadata.get("uuid") != UUID:
        fail(f"metadata uuid must be {UUID!r}")
    if metadata.get("name") != "et Workspace Timer":
        fail("metadata name must be 'et Workspace Timer'")
    if metadata.get("shell-version") != ["46", "50"]:
        fail("metadata shell-version must contain exactly GNOME 46 and 50")
    if metadata.get("settings-schema") != SCHEMA_ID:
        fail(f"metadata settings-schema must be {SCHEMA_ID!r}")

    schema_path = source_dir / "schemas" / f"{SCHEMA_ID}.gschema.xml"
    schema_root = ET.parse(schema_path).getroot()
    schema = schema_root.find("schema")
    if schema is None:
        fail("settings schema XML contains no <schema>")
    if schema.get("id") != SCHEMA_ID:
        fail(f"settings schema id must be {SCHEMA_ID!r}")
    if schema.get("path") != "/org/gnome/shell/extensions/et/":
        fail("settings schema path must be /org/gnome/shell/extensions/et/")

    key_elements = {element.get("name"): element for element in schema.findall("key")}
    keys = set(key_elements)
    expected_keys = {
        "workspaces",
        "show-label",
        "show-estimate",
        "show-counter",
        "show-panel",
        "show-workspace-switcher",
    }
    if keys != expected_keys:
        fail(f"unexpected settings keys: {sorted(keys)}")
    for key in ("show-label", "show-estimate", "show-counter"):
        if key_elements[key].findtext("default") != "true":
            fail(f"{key} must be enabled by default")

    extension_source = (source_dir / "extension.js").read_text()
    missing_methods = sorted(
        method
        for method in REQUIRED_DBUS_METHODS
        if f'<method name="{method}">' not in extension_source
    )
    if missing_methods:
        fail(f"D-Bus interface is missing methods: {', '.join(missing_methods)}")

    for path in source_dir.rglob("*"):
        if path.is_file() and path.suffix in {".js", ".json", ".xml", ".css"}:
            if "jira" in path.read_text().lower():
                fail(f"extension source must remain ticket-system agnostic: {path}")


def validate_archive(archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        names = {name.rstrip("/") for name in archive.namelist() if not name.endswith("/")}

    missing = sorted(REQUIRED_SOURCE_FILES - names)
    if missing:
        fail(f"extension archive is missing required files: {', '.join(missing)}")

    forbidden = sorted(
        name
        for name in names
        if name.endswith("gschemas.compiled")
        or "__pycache__" in name
        or name.startswith(".")
        or "/." in name
    )
    if forbidden:
        fail(f"extension archive contains generated or hidden files: {', '.join(forbidden)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()

    try:
        validate_source(args.source_dir)
        if args.archive is not None:
            validate_archive(args.archive)
    except (OSError, ValueError, json.JSONDecodeError, ET.ParseError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
