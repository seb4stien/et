# et

`et` is a small command-line tool for **tracking effort** and **managing your
Ubuntu/GNOME workspaces**. It renames GNOME workspaces, automatically tracks
time spent on managed workspaces through its own GNOME Shell extension, and
can link workspaces to Jira issues to log time against them.

## Features

- **`et jira [start|create|log-time|complete|comment|status]`**: interact and align your workspaces with Jira from the command line:
  - `create`: wrapper to create Jira issue from the command line (can take GitHub PR as input)
  - `start`: let you select and assigned task, create a workspace associated to it, and track the time you spend on it.
  - `log-time`: log your current progress (typically to do it on a daily-basis)
  - `complete`: log the time spent, and move the issue to Done.
  - `comment`: add a comment to the ticket.
  - `status`: show and let you update the status.
- **`et`** (no subcommand) / **`et info`**: show the Jira issue linked to the workspace (if any).
- **`et git create-branch`** (alias **`et git cb`**) — create (and switch
  to) a git branch named after the current task's Jira issue, following
  Canonical's `type/scope-short-description-jirakey` branch naming
  convention.
- **`et ws rename`** — rename the active workspace (or all of them from config).
- **`et ws delete`** — delete the active (free) workspace, shifting later ones left.

## Getting started

New to `et`? Here's the fastest path to a working setup on Ubuntu 24.04+
(adapt package names if you're on a different distribution):

1. **Install the system packages `et` needs:**

   ```bash
   sudo apt update
   sudo apt install libglib2.0-bin gnome-shell
   ```

   - `libglib2.0-bin` provides `gsettings`/`gdbus` (workspace renaming and
     talking to the GNOME Shell extension).
   - `gnome-shell` provides `gnome-extensions` (install/enable the
     extension) — already present on any GNOME desktop.
   - Optionally, [`gh`](https://cli.github.com/) (`sudo apt install gh`,
     then `gh auth login`) if you want `et jira create <GITHUB_URL>` to
     pre-fill from a GitHub issue/PR.

   Python **3.12+** is required — Ubuntu 24.04+ ships it by default
   (`python3 --version` to check).
2. **Install [uv](https://docs.astral.sh/uv/):**

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. **Clone the repo and install the GNOME Shell extension:**

   ```bash
   git clone <this-repo-url> && cd et
   scripts/install-gnome-shell-extension.sh
   ```
4. **Log out and back in** so GNOME Shell picks up the newly installed
   extension.
5. **Create your config file interactively:**

   ```bash
   uv run et config
   ```

   Walks you through the `jira` block and your `workspaces` list — see
   [Configuration](#configuration) for the file format it writes.
6. **Try it out:**

   ```bash
   uv run et --help
   uv run et ws rename focus
   uv run et jira start
   ```
7. **Install globally**, so you can run `et` directly instead of
   `uv run et`:

   ```bash
   uv tool install .
   ```

Want to contribute code instead? See [CONTRIBUTING.md](./CONTRIBUTING.md)
for the development workflow (`just install-requirements` additionally
pulls in dev/test tooling — `just` itself, plus GJS/ShellCheck/`prek` — on
top of everything above), and [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
for how `et` and its GNOME Shell extension are designed internally.

## Requirements

`et` shells out to standard GNOME/Ubuntu tooling, which must be available
on `PATH`:

- [`gsettings`](https://manpages.ubuntu.com/manpages/en/man1/gsettings.1.html)
  — read/write GNOME workspace names and settings.
- [`gdbus`](https://manpages.ubuntu.com/manpages/en/man1/gdbus.1.html)
  — communicate with the `et` GNOME Shell extension.
- [`gnome-extensions`](https://manpages.ubuntu.com/manpages/en/man1/gnome-extensions.1.html)
  — install, enable, and configure the bundled extension.
- The bundled **et Workspace Timer** GNOME Shell extension
  (`et@seb4stien.github.com`), installed and enabled. It supports the GNOME
  Shell releases used by Ubuntu 24.04 and Ubuntu 26.04 (GNOME 46 and 50).
- [`gh`](https://cli.github.com/) — installed and authenticated, only
  needed for `et jira create <GITHUB_URL>`'s summary/description prefill.

Python **3.12+** is required. [`just`](https://github.com/casey/just) is
only needed for the `Justfile`-based development workflow — see
[CONTRIBUTING.md](./CONTRIBUTING.md).

### The `et` GNOME Shell extension

Since GNOME Shell doesn't expose the active workspace over D-Bus by
default, this repo ships a companion extension,
[`gnome-extension/et@seb4stien.github.com`](gnome-extension/et@seb4stien.github.com),
that reports it directly. It also owns the per-workspace counters: when an
`et`-managed `dynamic` workspace is active, its counter runs automatically;
switching away or locking the session pauses it, and returning or
unlocking resumes it. Ordinary keyboard/mouse inactivity still counts. See
[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for how the extension and
its display preferences work internally.

Install and enable it with:

```bash
scripts/install-gnome-shell-extension.sh
```

(this also runs automatically as part of `just install-requirements`). By
default this *copies* the extension into
`~/.local/share/gnome-shell/extensions/et@seb4stien.github.com` rather than
symlinking it (see [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for why).
If you're actively editing `extension.js`, pass `--dev` (or `--symlink`) to
symlink instead so you don't need to re-run the script after every change
(a Shell restart is still required to pick up new code either way).

`gnome-extensions enable` may report the extension "does not exist" the
first time it's installed — this is expected, since GNOME Shell only scans
`~/.local/share/gnome-shell/extensions` for new UUIDs at startup; the script
detects this, registers it as enabled directly via `gsettings`, and checks
the running Shell over D-Bus to tell you whether it has no record of the
extension yet (needs a full restart) or already scanned it but hit an
error. Either way, log out and back in afterwards so GNOME Shell picks it
up.

Open its preferences with:

```bash
gnome-extensions prefs et@seb4stien.github.com
```

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
et ws delete --force        # same, even if still linked to a Jira issue (counter is lost)
```

`et ws delete` frees a workspace slot. It only works on a
"free" workspace — not `static`, and not linked to a Jira issue (run `et
jira complete` first if it still is). Every non-`static` workspace after the
deleted one (and its counter) shifts one slot to the left to close the
gap, then the now-empty trailing slot is reclaimed by decrementing GNOME's
workspace count (`num-workspaces`). The exception is when the
highest-numbered workspace is `static` — shrinking would swallow it, so the
count is left unchanged and the freed slot just becomes a bare `ET-<n>`.
Refuses to delete the last remaining workspace. `--force` bypasses the
Jira-linked check for assigned/in-progress workspaces — its counter is
discarded rather than logged, so log the time first if you need it (`--force`
never bypasses the `static` check).

### Tasks

`et jira` wraps the workspace timer and Jira integrations into a single
lifecycle for one task at a time — it doesn't replace `ws`, which keeps
working exactly as before.

```bash
et info                                      # (or bare `et`) show the active task's Jira issue and time spent
et jira start                                # pick an active Jira issue and start a task from it
et jira start ISD-123                        # start a task for a specific issue key directly
et jira create                               # interactively create a new Jira issue
et jira log-time                             # log the active workspace's tracked time to Jira
et jira log-time 2h                          # log a manually-specified 2h duration instead
et jira log-time --all                       # log every workspace with a linked Jira issue, not just the active one
et jira comment "Looks good"                 # add a comment to the linked Jira issue
et jira status in-progress                   # move the linked issue to "In Progress"
et jira status                               # show current status, pick a new one from a numbered list
et jira complete                             # log time, then optionally delete the workspace and close the issue
```

If Jira accepts a worklog but the local extension counter cannot be reset,
`et` reports the worklog as successful and prints a counter-only recovery
command. Do not rerun `log-time` for that counter, since that would duplicate
the Jira worklog.

`et` with no subcommand shows the same Jira issue details as before, plus
the elapsed time of the counter bound to the active
workspace (e.g. `Time spent: 1h 12m 0s`, with `(running)` appended if the
timer is currently running) — but only when the active workspace is part
of the managed (non-`static`) pool; otherwise it shows this help text.

`et jira start` allocates the first free (non-`static`, unlinked) workspace
slot from the fixed pool of GNOME workspaces. If every workspace is already
taken, it asks whether to add one more (bumping GNOME's `num-workspaces` by
one) — decline and the command cancels without changing anything. It then
prepares the slot's automatic counter and display metadata, and
switches GNOME to it, best-effort moving the terminal window it was run
from along with it so it doesn't get left behind on the old workspace.
Not every terminal emulator can be moved this way; when it's unsupported,
`et jira start` prints a note but still succeeds. It lists your active
Jira issues that aren't already linked to a workspace, lets you pick one,
and links the new workspace to it. If the selected issue isn't already
"In Progress", it asks whether to
move it there (showing its current status) and does so via Jira's
transitions API if you confirm.

`et jira start KEY` starts a task for a specific Jira
issue directly instead of picking one from the active-issues list — it
fails if `KEY` is already linked to an existing workspace. It follows the
same steps as above (offering to move the issue to "In Progress" if
needed), plus one more: if the issue isn't already in one of its
project's current active sprints, it asks whether to add it to one (using
the same Agile board auto-discovery/caching as `et jira create --sprint`,
and requiring `jira.project_key` to be set) — if the board has more than
one concurrently active sprint, it prompts you to pick which one; if no
board or active sprint can be resolved, it prints a warning and continues
without touching the sprint rather than failing the command.

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
lookup; if the board has more than one concurrently active sprint, you're
prompted to pick which one); an estimate in hours (written to the issue's
time-tracking original estimate); and an optional description (pre-filled
from the GitHub issue/PR body when a URL is given, with the URL itself
always appended as a reference). When `GITHUB_URL` is given, it's also
written to the issue's "Bug link" field, if that custom field exists on
the Jira instance (looked up by name, like the Sprint field — skipped with a
warning otherwise). `GITHUB_URL` accepts
`https://github.com/<owner>/<repo>/issues/<n>` and
`https://github.com/<owner>/<repo>/pull/<n>` links, fetched via the `gh`
CLI (which must be installed and authenticated) — if the URL can't be
parsed or fetched, `et jira create` warns and falls back to blank
defaults rather than failing outright.

`et jira log-time` reads the elapsed time from the active workspace's counter
bound to the active workspace, resolves the Jira issue linked to that
workspace (its `ref`, e.g. set by `et jira start`), and logs it as a
worklog via Jira's own worklog API (no separate Tempo credential needed —
worklogs created this way still show up in Tempo timesheets when Tempo is
configured to sync native Jira worklogs). At least a minute of elapsed time
is required. On success the counter is reset to 0, unless `--no-reset` is
given. Given an `Xh` duration instead (e.g. `et jira log-time 2h` or `et
jira log-time 1.5h`), that duration is logged manually rather than the
workspace counter's elapsed time — the counter isn't read or reset in
that case (so `--no-reset` doesn't apply).

`et jira log-time --all` logs every workspace in the `workspaces` config
list that has a linked Jira issue, instead of only the active one —
useful for logging a whole day's tracked time across every task at once
without switching between workspaces. Each linked workspace's own counter is
logged to its own issue and reset immediately on success (or left
untouched otherwise); a workspace with less than a minute of elapsed time,
no prepared counter, or a failing Jira call is skipped (reported at the
end) rather than stopping the rest from being logged. `--no-reset` still
applies (to every workspace logged in that run), but `--all` can't be
combined with an `Xh` duration, `--comment/-m`, or `-j/--jira`, since those
only make sense for a single workspace/issue.

`et jira comment [MESSAGE]` adds a comment to the Jira issue linked to the
active workspace (or a different issue via `-j/--jira KEY`). Prompts for
the message if not given as an argument.

`et jira status [in-progress|blocked]` moves the linked issue directly to
"In Progress" or "Blocked" (applied immediately, no confirmation). With no
argument, it shows the linked issue's current status and a numbered list of
the team's workflow statuses (`Untriaged`, `Triaged`, `In Progress`,
`Blocked`, `In Review`, `To Be Deployed`, `Done`, `Rejected`) to pick a new
one from interactively; leave the prompt blank to cancel.

`et jira log-time`, `et jira complete`, `et jira comment`, and `et jira
status` all accept a `-j`/`--jira KEY` option to act on a specific Jira
issue instead of the one linked to the active workspace — e.g. `et jira
comment "Looks good" -j ISD-123` or `et jira status blocked --jira
ISD-123`. For `comment` and `status`, this also skips workspace resolution
entirely, so those two work even outside a managed workspace.

`et jira complete` logs the active workspace's tracked time to Jira (like
`et jira log-time`) and tells you how much it logged. It then asks whether
to delete the workspace and whether to move the linked Jira issue to
"Done" — each behind its own confirmation prompt, so both actions are
skipped unless you confirm them. When you confirm the delete, the workspace
is removed exactly like `et ws delete` — GNOME's workspace count is
decremented to reclaim the slot and every non-`static` workspace after it is
shifted one slot to the left (its counter follows it), so no gap is
left in the middle of your workspaces. If only a single workspace remains
(GNOME can't drop below one), its slot is reset to a bare `ET-<n>` instead.

### Git

```bash
et git create-branch                         # branch off the active workspace's linked issue
et git cb                                     # alias for the above
et git create-branch -j ISD-1234              # branch off a specific issue instead
```

`et git create-branch` (aliased `et git cb`) creates a git branch for the
current task's Jira issue, following Canonical's
[PR branch naming convention](https://github.com/canonical/platform-engineering-docs/blob/main/docs/delivery-workflows/github/pull-requests/index.rst):
`type/scope-short-description-jirakey` (e.g.
`feat/tcp-wildcard-sni-support-isd-1234`). It defaults to the Jira issue
linked to the active workspace; pass `-j`/`--jira KEY` to target a
different issue instead (works outside a managed workspace too, like the
`et jira` commands' own `-j`/`--jira`).

It first prints the issue's clickable link and summary, then:

- Proposes a **branch type** (one of `feat`/`fix`/`docs`/`chore`/`test`/`ci`)
  computed from the issue: `Bug` → `fix`, `Story` → `feat`, `Task` →
  `chore`; a `documentation` label always wins and defaults to `docs`
  regardless of issue type. Prompts to accept the default or pick a
  different one from the list — this becomes the branch's `type/` prefix,
  which can't otherwise be typed in freely.
- Proposes the `scope-short-description` segment as a slug of the issue
  summary, editable at the prompt.
- Appends the resolved Jira key (lowercased) as the branch's trailing
  identifier — always the actual issue key, not editable.

Fails with a clear error if not run inside a git repository, or if a local
branch with the computed name already exists. On success, creates the
branch from the current `HEAD` and switches to it (`git checkout -b`).

## Configuration

`et` reads `~/.config/et/config.yaml` (override the directory with the
`ET_CONFIG_DIR` environment variable). Run `et config` to create or update
it interactively — it walks through the `jira` block (testing the
credentials against Jira's API once entered) and the `workspaces` list
(add/edit/remove entries), pre-filling every field from the existing file
when one is already present. Example of the resulting file:

```yaml
# Jira Cloud REST credentials + query. Required for `et jira start`
# (Jira-issue picking), `et jira log-time`, `et jira complete`,
# `et jira comment`, `et jira status`, and `et jira create`.
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

The `jql` value is a plain YAML scalar, so quoting it is optional — quote
it only if it starts with a character YAML reserves (`{`, `[`, `*`, `&`,
`!`, `%`, `@`) or contains `#` or `:`.

When `et jira start` reports no issues but the same JQL finds some in the
Jira web UI, run `et --debug jira start`: it logs each search request (URL,
the exact JQL sent, the account it authenticates as) and the issue keys
each page returns, which distinguishes "Jira returned nothing" from "the
issues were filtered out as already linked to a workspace".

An expired or truncated `pat` is reported as such rather than as an empty
issue list: Jira serves an unauthenticated search anonymously (HTTP 200,
no issues) instead of refusing it, so `et` re-checks the credentials
against `/rest/api/3/myself` whenever a search comes back empty.

> **Note:** `et` requires a *fixed* number of GNOME workspaces
> (`org.gnome.mutter dynamic-workspaces = false`) so the `ET-<n>` slots
> always exist. If dynamic workspaces are enabled, `et` exits with
> instructions to disable them and pick a workspace count:
>
> ```bash
> gsettings set org.gnome.mutter dynamic-workspaces false
> gsettings set org.gnome.desktop.wm.preferences num-workspaces <N>
> ```

> **Known limitation:** `et jira start` applies its changes sequentially
> without a rollback, which can briefly leave config and live GNOME state
> out of sync on failure — see
> [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md#known-limitations) for
> details.

## License

The Python project is licensed under the [Apache License 2.0](./LICENSE).
The GNOME Shell extension is licensed separately under
[GPL-3.0-or-later](./gnome-extension/et@seb4stien.github.com/LICENSE), as
required for GNOME Shell extensions.
