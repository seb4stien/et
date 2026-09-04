# Changelog

All notable changes to `et` are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Changed
- **Breaking:** `et jira start` now takes the Jira issue key as a
  positional argument (`et jira start KEY`) instead of the `-k`/`--key`
  option; the option has been removed.

### Fixed
- Workspace creation, deletion, reset, and reordering now roll back reversible
  config and GNOME settings changes when a later step fails, and report when
  extension state may still be partially applied.
- Jira worklogs that succeed before a counter-reset failure are now reported
  as partial successes with a safe counter-only recovery command, preventing
  accidental duplicate worklogs on retry.
- Jira issue and transition parsing now handles malformed nested response
  fields without leaking raw `AttributeError` exceptions.
- Config updates are now written atomically with restrictive permissions.
- CI now restricts token permissions and pins the third-party `setup-just`
  action to an immutable commit.
- `scripts/install-gnome-shell-extension.sh` now *copies* the `et` GNOME
  Shell extension into `~/.local/share/gnome-shell/extensions/` by default
  instead of symlinking it, so it keeps working across workspace
  re-provisioning that happens on a timeline independent of the GNOME
  session (previously, if the symlink's target didn't exist yet at the
  exact moment GNOME Shell scanned extensions at startup, the extension was
  silently skipped and never loaded until an unrelated further restart).
  Pass `--dev`/`--symlink` to opt back into symlinking, e.g. when actively
  editing `extension.js`. The "not yet scanned" fallback message now also
  queries the running Shell over D-Bus to distinguish "never scanned" from
  "scanned but errored".

### Added
- A companion `et` GNOME Shell extension
  (`gnome-extension/et@seb4stien.github.com`, installed via
  `scripts/install-gnome-shell-extension.sh` / `just install-requirements`)
  exposing a D-Bus service used to detect the active workspace on Wayland
  sessions and to own persistent per-workspace counters. Managed dynamic
  workspaces now start and pause automatically as the active workspace
  changes, pause while the session is locked, and resume after unlock.
- Configurable, ticket-system-agnostic workspace labels, reference estimates,
  and live counters in both the GNOME top panel and workspace switcher. A
  libadwaita preferences window controls each displayed value and location;
  only the counter is shown by default.
- D-Bus operations for the CLI to prepare, read, reset, remove, update, and
  atomically remap workspace counters without editing extension state directly.
- `et jira start -k KEY` (`--key`): start a task from a specific Jira issue
  key directly, skipping the active-issues picker (failing if `KEY` is
  already linked to a workspace). Follows the same steps as the
  interactive `et jira start` — offering to move the issue to "In
  Progress" if needed — plus one more: if the issue isn't already in one
  of its project's current active sprints, asks whether to add it to one
  (reusing `et jira create --sprint`'s Agile board discovery/caching;
  prompts you to pick when more than one sprint is active); degrades to
  a warning, rather than failing, if no board/active sprint can be
  resolved.
- `et git create-branch` (alias `et git cb`): create (and switch to) a git
  branch named after the current task's Jira issue, following Canonical's
  `type/scope-short-description-jirakey` PR branch naming convention.
  Defaults to the issue linked to the active workspace (or `-j`/`--jira
  KEY`); proposes a branch type from the issue's type/labels (`Bug`→`fix`,
  `Story`→`feat`, `Task`→`chore`, `documentation` label→`docs`) that can be
  overridden from the fixed `feat/fix/docs/chore/test/ci` list, and a
  `scope-short-description` slug of the issue summary that's freely
  editable; the trailing Jira key identifier is never editable. Prints the
  issue's clickable link and summary before prompting.
- `et jira comment [MESSAGE]`: add a comment to the Jira issue linked to
  the active workspace (or a different issue via `-j`/`--jira`), prompting
  for the message if not given as an argument.
- `et jira log-time [Xh]`: log a manually-specified duration (e.g. `2h`,
  `1.5h`) to the linked Jira issue instead of reading the workspace counter's
  elapsed time.
- `et jira log-time --all`: log every workspace with a linked Jira issue
  (not just the active one) in a single run, each to its own issue, without
  switching GNOME workspaces. Workspaces with too little tracked time, no
  prepared counter, or a failing Jira call are skipped and reported instead of
  aborting the rest.
- `et jira status [in-progress|blocked]`: move the linked Jira issue
  directly to "In Progress" or "Blocked". With no argument, shows the
  issue's current status and a numbered list of the team's workflow
  statuses to pick a new one from interactively.
- `-j`/`--jira KEY`: generic option on `et jira log-time`, `complete`,
  `comment`, and `status` to act on a specific Jira issue instead of the
  one linked to the active workspace.
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
- `et --debug`: logs every Jira search request (URL, the exact JQL sent,
  the authenticating account) and the issue keys each page returns, for
  diagnosing "No active Jira issues available" against a JQL that finds
  issues in the Jira web UI.
- `et ws organize`: reorder dynamic workspaces by editing their order in
  `$EDITOR` (static workspaces stay pinned). Shows a before/after summary
  (old/new index, name, linked Jira issue, counter time) and asks for
  confirmation before applying. Counters follow their workspace to
  its new slot.

### Changed
- The bundled `et` extension replaces the third-party Tracker extension.
  Existing Tracker values are intentionally not imported; newly prepared
  workspaces start at zero.
- Jira issue reads now include the original estimate so the CLI can provide it
  to the generic extension display alongside the workspace label.
- Config gains `jira.project_key` and `jira.board_id`.
- Jira issue references (`et jira log-time`, `et jira complete`) are now
  clickable OSC 8 terminal hyperlinks, like `et jira start` already was.
- `et jira start`'s "move to In Progress?" and "add another workspace?"
  prompts now default to yes.
- `et jira complete` now actually deletes the freed GNOME workspace instead
  of leaving an empty slot, and prompts for confirmation before freeing the
  workspace and closing the linked Jira issue.
- `et jira start` resets a reused workspace's extension-owned counter.
- `et ws delete` shrinks GNOME's workspace count instead of leaving a bare
  trailing slot, and no longer orphans the counter when deleting the
  last remaining workspace.
- Workspace names can now be up to 30 characters (up from a shorter limit).
- Dropped the `max_workspaces` config option; `et` now requires a fixed
  (non-dynamic) set of GNOME workspaces and manages within it directly.
- README now documents the built-in panel/switcher display and extension
  preferences.

### Fixed
- Rejected Jira credentials are now reported as such instead of as an
  empty issue list. Jira answers an unauthenticated search anonymously
  (HTTP 200 with no issues), so `et` verifies the credentials against
  `/rest/api/3/myself` whenever a search returns nothing.
- Jira issue search no longer stops at the first empty page of results.
  Jira's `/search/jql` bounded scan can return an empty page that still
  carries a `nextPageToken`, which made `et jira start` report "No active
  Jira issues available" for a JQL that matches issues in the Jira UI.
- Removed dead code, stale docs, and assorted inconsistencies uncovered
  during cleanup.

## [0.1.0] - 2026-07-17

Initial tracked release: `et jira`/`et ws` command groups, Jira Cloud
integration (`et jira get`/`create`/`start`/`log-time`/`complete`),
GNOME workspace renaming and Tracker timer management.
