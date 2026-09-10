# et

`et` is a small command-line tool for **tracking effort** and **managing your
Ubuntu/GNOME workspaces**. It renames GNOME workspaces, automatically tracks
time spent on managed workspaces through its own GNOME Shell extension, and
can link workspaces to Jira issues to log time against them.

## Features

- **`et jira [start|create|log-time|complete|comment|status]`**: interact and align your workspaces with Jira from the command line:
  - `create`: wrapper to create Jira issue from the command line (can take GitHub PR as input)
  - `start`: let you select and assigned task, create a workspace associated to it, and track the time you spend on it (prompts to move it to "In Progress" and to assign it to yourself if it isn't already).
  - `log-time`: log your current progress (typically to do it on a daily-basis)
  - `complete`: log the time spent, and move the issue to Done. If less than 60 seconds
    have elapsed, skip logging and continue with the completion prompts.
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

Want to contribute code instead? See [CONTRIBUTING.md](./CONTRIBUTING.md).

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

## CLI features

### Workspaces features

```bash
et ws rename focus          # rename the active workspace to "focus"
et ws rename --all          # rename workspaces 0..n-1 from the config's "workspaces" list
et ws delete                # delete the active workspace, shifting later ones left
et ws delete --force        # same, even if still linked to a Jira issue (counter is lost)
```

### Jira features

```bash
et info                                      # (or bare `et`) show the active task's Jira issue and time spent
et jira start                                # pick an active Jira issue and start a task from it
et jira start ISD-123                        # start a task for a specific issue key directly
et jira create                               # interactively create a new Jira issue
et jira create <GITHUB_URL>                  # pre-fill from a GitHub issue/PR
et jira log-time                             # log the active workspace's tracked time to Jira
et jira log-time 2h                          # log a manually-specified 2h duration instead
et jira log-time --all                       # log every workspace with a linked Jira issue, not just the active one
et jira comment "Looks good"                 # add a comment to the linked Jira issue
et jira status in-progress                   # move the linked issue to "In Progress"
et jira status                               # show current status, pick a new one from a numbered list
et jira complete                             # log time, then optionally delete the workspace and close the issue
et jira log-time -j ISD-123                  # act on a specific issue instead of the active workspace's one
```

### Git features

```bash
et git create-branch                         # branch off the active workspace's linked issue
et git cb                                     # alias for the above
et git create-branch -j ISD-1234              # branch off a specific issue instead
```

Follows Canonical's
[PR branch naming convention](https://github.com/canonical/platform-engineering-docs/blob/main/docs/delivery-workflows/github/pull-requests/index.rst)
(`type/scope-short-description-jirakey`), proposing a branch type and
description slug from the issue that you can accept or edit. Run
`et git create-branch --help` for details.

## Configuration

`et` reads `~/.config/et/config.yaml`.

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
