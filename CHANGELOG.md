# Changelog

All notable changes to `et` are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- `et ws organize`: reorder dynamic workspaces by editing their order in
  `$EDITOR` (static workspaces stay pinned). Shows a before/after summary
  (old/new index, name, linked Jira issue, Tracker time) and asks for
  confirmation before applying. Tracker timers follow their workspace to
  its new slot.

### Changed
- `et jira complete` now actually deletes the freed GNOME workspace instead
  of leaving an empty slot, and prompts for confirmation before freeing the
  workspace and closing the linked Jira issue.
- `et jira start` (task creation) now cleans up stale Tracker timers left
  behind by previously deleted workspaces.
- `et ws delete` shrinks GNOME's workspace count instead of leaving a bare
  trailing slot, and no longer orphans the Tracker timer when deleting the
  last remaining workspace.
- Workspace names can now be up to 30 characters (up from a shorter limit).
- Dropped the `max_workspaces` config option; `et` now requires a fixed
  (non-dynamic) set of GNOME workspaces and manages within it directly.
- README now recommends the Workspace Switcher Manager GNOME extension for
  showing workspace names on-screen.

### Fixed
- Removed dead code, stale docs, and assorted inconsistencies uncovered
  during cleanup.

## [0.1.0] - 2026-07-17

Initial tracked release: `et jira`/`et ws` command groups, Jira Cloud
integration (`et jira get`/`create`/`start`/`log-time`/`complete`),
GNOME workspace renaming and Tracker timer management.
