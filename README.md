# et

`et` is a small command-line tool for **tracking effort** and **managing your
Ubuntu/GNOME workspaces**. It renames GNOME workspaces, drives the
[Tracker](https://extensions.gnome.org/extension/3212/tracker/) GNOME Shell
extension's per-workspace timers, and can link workspaces to Jira issues to
log time against them.

## Features

- **`et`** (no subcommand) / **`et info`** — in a non-`static` workspace,
  shows the Jira issue linked to it plus its tracked time; otherwise shows
  this help.
- **`et ws rename`** — rename the active workspace (or all of them from config).
- **`et ws delete`** — delete the active (free) workspace, shifting later ones left.
- **`et jira [start|create|log-time|complete]`** — a friendlier, task-centric
  layer that creates a workspace + Tracker timer for a task (picked
  straight from your active Jira issues, optionally moving it to "In
  Progress"), interactively creates new Jira issues (optionally pre-filled
  from a GitHub issue/PR URL), and completes a task by logging its tracked
  time to Jira and optionally freeing the slot and moving the issue to
  "Done".

## Requirements

`et` shells out to standard GNOME/Ubuntu tooling, which must be available on
`PATH`:

- [`gsettings`](https://manpages.ubuntu.com/manpages/en/man1/gsettings.1.html)
  — read/write GNOME workspace names and the Tracker extension's timers.
- [`wmctrl`](https://manpages.ubuntu.com/manpages/en/man1/wmctrl.1.html)
  — detect the active workspace (`sudo apt install wmctrl`).
- [`gnome-extensions`](https://manpages.ubuntu.com/manpages/en/man1/gnome-extensions.1.html)
  — reload the Tracker extension around timer writes.
- The **Tracker** GNOME Shell extension (`tracker@aliakseiz.github.com`),
  installed and enabled, for `et jira`'s timer functionality.
- [`gh`](https://cli.github.com/) — installed and authenticated, only
  needed for `et jira create <GITHUB_URL>`'s summary/description prefill.

Python **3.12+** is required.

### Recommended: show workspace names in the switcher

Since `et` names your workspaces after tasks/Jira issues, it helps to see
those names in GNOME's workspace switcher popup. Install the
[**Workspace Switcher Manager**](https://extensions.gnome.org/extension/4788/workspace-switcher-manager/)
extension (`workspace-switcher-manager@G-dH.github.com`), then configure it
to display the workspace name (it shows only the index/app name by default):

```bash
S=org.gnome.shell.extensions.workspace-switcher-manager
gsettings set $S active-show-ws-name true       # show the name on the active workspace
gsettings set $S inactive-show-ws-name true     # ...and on the others
gsettings set $S active-show-app-name false     # drop the focused-app name
gsettings set $S inactive-show-app-name false
gsettings set $S popup-width-scale 200          # widen the popup so names fit
```

The workspace index stays visible (`active-show-ws-index`, on by default).
The rest is personal taste — you can also tweak the popup position
(`horizontal`/`vertical`), corner radius (`popup-radius-scale`), on-screen
time (`on-screen-time`), and font size (`font-scale`) from the extension's
preferences.

## Installation

This project uses [uv](https://docs.astral.sh/uv/):

```bash
uv sync            # create the virtualenv and install et + dependencies
uv run et --help   # run without installing globally
```

To install the `et` entry point onto your `PATH`:

```bash
uv tool install .
```

## Usage

```bash
et --help
```

Run bare (no subcommand), or `et info` explicitly, from a non-`static`
workspace to see its linked Jira issue and tracked time at a glance:

```bash
et         # same output as `et jira log-time` would act on, without logging anything
et info    # explicit, named equivalent of the above
```

From a `static` workspace, or one that isn't part of the managed pool,
both fall back to the usual help text.

### Workspaces

```bash
et ws rename focus          # rename the active workspace to "focus"
et ws rename --all          # rename workspaces 0..n-1 from the config's "workspaces" list
et ws delete                # delete the active workspace, shifting later ones left
et ws delete --force        # same, even if still linked to a Jira issue (tracker is lost)
```

`et ws delete` frees a workspace slot. It only works on a
"free" workspace — not `static`, and not linked to a Jira issue (run `et
jira complete` first if it still is). Every non-`static` workspace after the
deleted one (and its Tracker timer) shifts one slot to the left to close the
gap, then the now-empty trailing slot is reclaimed by decrementing GNOME's
workspace count (`num-workspaces`). The exception is when the
highest-numbered workspace is `static` — shrinking would swallow it, so the
count is left unchanged and the freed slot just becomes a bare `ET-<n>`.
Refuses to delete the last remaining workspace. `--force` bypasses the
Jira-linked check for assigned/in-progress workspaces — its Tracker timer is
discarded rather than logged, so log the time first if you need it (`--force`
never bypasses the `static` check).

### Tasks

`et jira` wraps the workspace/Tracker/Jira integrations into a single
lifecycle for one task at a time — it doesn't replace `ws`, which keeps
working exactly as before.

```bash
et info                                      # (or bare `et`) show the active task's Jira issue and time spent
et jira start                                # pick an active Jira issue and start a task from it
et jira create                               # interactively create a new Jira issue
et jira log-time                             # log the active workspace's tracked time to Jira
et jira complete                             # log time, then optionally delete the workspace and close the issue
```

`et` with no subcommand shows the same Jira issue details as before, plus
the elapsed time of the `ET-<n>` Tracker timer bound to the active
workspace (e.g. `Time spent: 1h 12m 0s`, with `(running)` appended if the
timer is currently running) — but only when the active workspace is part
of the managed (non-`static`) pool; otherwise it shows this help text.

`et jira start` allocates the first free (non-`static`, unlinked) workspace
slot from the fixed pool of GNOME workspaces. If every workspace is already
taken, it asks whether to add one more (bumping GNOME's `num-workspaces` by
one) — decline and the command cancels without changing anything. It then
creates the slot's `ET-<n>` Tracker timer, and
switches GNOME to it, best-effort moving the terminal window it was run
from along with it (via `wmctrl -r :ACTIVE:`) so it doesn't get left
behind on the old workspace. That last step needs an addressable X11
window, which native Wayland clients (e.g. many terminal emulators under
GNOME/Wayland) don't have; when it's unsupported, `et jira start` prints
a note but still succeeds. It lists your active Jira issues that aren't
already linked to a workspace, lets you pick one, and links the new
workspace to it. If the selected issue isn't already "In Progress", it
asks whether to
move it there (showing its current status) and does so via Jira's
transitions API if you confirm.

`et jira create [GITHUB_URL]` interactively creates a new Jira issue in
`jira.project_key` (required in config for this command). It prompts for:
the issue type (`Bug`/`Story`/`Task`, default `Story` — defaulting to `Bug`
when `GITHUB_URL` points at a GitHub issue labeled "bug"); the summary
(pre-filled from the GitHub issue/PR title when a URL is given); whether to
assign the issue to yourself (default yes, via your `jira.email`); priority
(`Highest`/`High`/`Medium`/`Low`/`Lowest`, default `Medium`); a component
picked from the project's component list; whether to add the issue to the
project's current sprint (default yes — the Agile board is auto-discovered
on first use and its id saved to `jira.board_id` so later runs skip that
lookup); an estimate in hours (written to the issue's time-tracking
original estimate); and an optional description (pre-filled from the
GitHub issue/PR body when a URL is given, with the URL itself always
appended as a reference). When `GITHUB_URL` is given, it's also written to
the issue's "Bug link" field, if that custom field exists on the Jira
instance (looked up by name, like the Sprint field — skipped with a
warning otherwise). `GITHUB_URL` accepts
`https://github.com/<owner>/<repo>/issues/<n>` and
`https://github.com/<owner>/<repo>/pull/<n>` links, fetched via the `gh`
CLI (which must be installed and authenticated) — if the URL can't be
parsed or fetched, `et jira create` warns and falls back to blank
defaults rather than failing outright.

`et jira log-time` reads the elapsed time from the `ET-<n>` Tracker timer
bound to the active workspace, resolves the Jira issue linked to that
workspace (its `ref`, e.g. set by `et jira start`), and logs it as a
worklog via Jira's own worklog API (no separate Tempo credential needed —
worklogs created this way still show up in Tempo timesheets when Tempo is
configured to sync native Jira worklogs). At least a minute of elapsed time
is required. On success the tracker is reset to 0, unless `--no-reset` is
given.

`et jira complete` logs the active workspace's tracked time to Jira (like
`et jira log-time`) and tells you how much it logged. It then asks whether
to delete the workspace and whether to move the linked Jira issue to
"Done" — each behind its own confirmation prompt, so both actions are
skipped unless you confirm them. When you confirm the delete, the workspace
is removed exactly like `et ws delete` — GNOME's workspace count is
decremented to reclaim the slot and every non-`static` workspace after it is
shifted one slot to the left (its Tracker timer follows it), so no gap is
left in the middle of your workspaces. If only a single workspace remains
(GNOME can't drop below one), its slot is reset to a bare `ET-<n>` instead.

## Configuration

`et` reads `~/.config/et/config.yaml` (override the directory with the
`ET_CONFIG_DIR` environment variable). Example:

```yaml
# Jira Cloud REST credentials + query. Required for `et jira start`
# (Jira-issue picking), `et jira log-time`, `et jira complete`, and
# `et jira create`.
jira:
  base_url: https://your-org.atlassian.net
  email: you@example.com
  pat: your-jira-api-token          # a Jira Cloud API token, not a password
  jql: assignee = currentUser() AND statusCategory != Done
  # Optional; controls sort order. Defaults to the list below.
  priority_order: [Highest, High, Medium, Low, Lowest]
  # Required only for `et jira create`: the project new issues are filed in.
  project_key: ISD
  # Optional; the Agile board id used by `et jira create` to find the
  # current sprint. Auto-discovered and saved here on first use if absent.
  board_id: "42"

# Ordered workspace list used by `ws rename --all` and `et jira start`.
workspaces:
  - name: mails
  - name: handson
    type: static                    # "static" workspaces are never touched by `et jira start`
  - name: isd-321
    ref: jira:ISD-321               # links a workspace to a Jira issue
    description: Fix the login flow
```

Per-entry keys: `name` (required), `type` (`dynamic` (default) or `static`),
`ref` (e.g. `jira:ISD-321`), and `description`. The config file is written
with mode `0600` because it may contain a Jira API token.

> **Note:** `et` requires a *fixed* number of GNOME workspaces
> (`org.gnome.mutter dynamic-workspaces = false`) so the `ET-<n>` slots
> always exist. If dynamic workspaces are enabled, `et` exits with
> instructions to disable them and pick a workspace count:
>
> ```bash
> gsettings set org.gnome.mutter dynamic-workspaces false
> gsettings set org.gnome.desktop.wm.preferences num-workspaces <N>
> ```

> **Known limitation:** `et jira start` applies its changes (Tracker
> timers, then config, then GNOME workspace names) sequentially without a
> rollback. A failure partway through can leave the config and live GNOME
> state temporarily out of sync; re-running the command reconciles them.


## Development

Common tasks are exposed through a [`Justfile`](./Justfile):

```bash
just install-requirements   # uv sync + install pre-commit hooks
just lint                   # ruff
just static                 # mypy --strict
just test                   # pytest + coverage
just test-integ             # end-to-end integration tests
just ops                    # build the distribution artifacts
```

`prek` (pre-commit) runs ruff, mypy, and pytest on every commit.

## License

Licensed under the [Apache License 2.0](./LICENSE).
