# Changelog

All notable changes to `et` are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- `et jira create [GITHUB_URL]`: interactively create a Jira issue —
  prompts for type (Bug/Story/Task), summary, self-assignment, priority,
  component (3-column picker), current sprint, estimate hours, and
  description, then shows a confirmation summary before creating.
  Optionally pre-fills summary/description (and defaults type to Bug) from
  a GitHub issue/PR URL via the `gh` CLI, and populates Jira's "Bug link"
  custom field with that URL when the field exists.
- Auto-discovers and caches the project's Agile board id
  (`jira.board_id`), preferring Scrum boards since Kanban boards don't
  support sprints; falls back to a fresh Scrum-board lookup (and persists
  the correction) if a cached board turns out not to support sprints.
- `et ws organize`: reorder dynamic workspaces by editing their order in
  `$EDITOR` (static workspaces stay pinned). Shows a before/after summary
  (old/new index, name, linked Jira issue, Tracker time) and asks for
  confirmation before applying. Tracker timers follow their workspace to
  its new slot.

### Changed
- Config gains `jira.project_key` and `jira.board_id`.
- Jira issue references (`et jira log-time`, `et jira complete`) are now
  clickable OSC 8 terminal hyperlinks, like `et jira start` already was.
- `et jira start`'s "move to In Progress?" and "add another workspace?"
  prompts now default to yes.
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
