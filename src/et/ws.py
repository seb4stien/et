"""Orchestrates non-trivial `et ws` commands (`et ws delete` and `et ws organize`).

`shift_workspaces_left` is also reused by `et.task` (`et jira complete`'s
"shift everything after the freed slot left, instead of leaving a gap"
logic), which is why it lives here rather than directly in `et.workspaces`
(which has no config/tracker dependency). Has no Typer/CLI dependency.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

from et import tracker, workspaces
from et.config import ConfigError, EtConfig, WorkspaceConfigEntry, load_config, save_config
from et.jira_ref import default_entry, jira_key_from_ref
from et.tracker import TimerEntry, TrackerError, find_timer_for_workspace
from et.workspaces import WorkspaceError


class WsDeleteError(RuntimeError):
    """Raised when the active workspace cannot be deleted."""


class WsOrganizeError(RuntimeError):
    """Raised when the dynamic workspaces cannot be reorganized."""


@dataclass(frozen=True)
class WsDeleteResult:
    """Summary of what `delete_active_workspace` did."""

    workspace_index: int
    remaining_workspaces: int


@dataclass(frozen=True)
class OrganizeCandidate:
    """One reorderable (non-`static`) slot, as shown to the user for `ws organize`."""

    slot: int
    entry: WorkspaceConfigEntry
    timer: TimerEntry | None


@dataclass(frozen=True)
class OrganizePlanRow:
    """One row of a computed `ws organize` plan: what ends up in `new_slot`.

    `entry` and `timer` are the *post-move* values (timer already has its
    `workspaceId`/`name` updated to `new_slot`, if it moved).
    """

    old_slot: int
    new_slot: int
    entry: WorkspaceConfigEntry
    timer: TimerEntry | None


def shift_workspaces_left(
    workspaces_list: list[WorkspaceConfigEntry], entries: list[TimerEntry], freed_index: int
) -> bool:
    """Shift every non-`static` slot after `freed_index` one slot to the left.

    `freed_index` (already reset to a bare "ET-<n>" entry by the caller) is
    filled with whatever was in the next non-static slot, that slot is
    filled with the one after it, and so on, leaving a single bare slot at
    the *end* of the non-static range instead of a gap in the middle. Each
    moved workspace's bound Tracker timer (if any) follows it — its
    `workspaceId`/`name` are updated in place — and a stale timer left
    behind at `freed_index` (e.g. the just-reset-to-zero timer of a task
    that was just completed, or an already-deleted workspace's leftover
    timer) is discarded rather than duplicated.

    Mutates `workspaces_list` and `entries` in place. Returns whether
    `entries` changed (so the caller knows whether to save it). No-op if
    `freed_index` isn't a non-static slot, or is already the last one.
    """
    non_static_slots = [i for i, entry in enumerate(workspaces_list) if entry.type != "static"]
    if freed_index not in non_static_slots:
        return False

    freed_position = non_static_slots.index(freed_index)
    timers_changed = False

    for position in range(freed_position, len(non_static_slots) - 1):
        dst = non_static_slots[position]
        src = non_static_slots[position + 1]

        source_entry = workspaces_list[src]
        if source_entry == default_entry(src, source_entry.type):
            # Bare placeholder slot (e.g. one padded in by `et ws delete` for
            # an implicit slot): moving it verbatim would carry its stale
            # "ET-<src+1>" name into `dst`, so give `dst` a fresh, correctly
            # numbered placeholder instead of copying the source's content.
            workspaces_list[dst] = default_entry(dst, workspaces_list[dst].type)
        else:
            workspaces_list[dst] = WorkspaceConfigEntry(
                name=source_entry.name,
                type=workspaces_list[dst].type,
                ref=source_entry.ref,
                description=source_entry.description,
            )
        workspaces_list[src] = default_entry(src, workspaces_list[src].type)

        existing_at_dst = find_timer_for_workspace(entries, dst)
        if existing_at_dst is not None:
            entries.remove(existing_at_dst)
            timers_changed = True

        moved_timer = find_timer_for_workspace(entries, src)
        if moved_timer is not None:
            moved_timer["workspaceId"] = dst
            moved_timer["name"] = f"ET-{dst + 1}"
            timers_changed = True

    return timers_changed


def _pad_workspaces_list(
    config_workspaces: list[WorkspaceConfigEntry], min_length: int
) -> list[WorkspaceConfigEntry]:
    """Return `config_workspaces` padded with bare "ET-<n>" entries up to `min_length`.

    Slots beyond the configured `workspaces` list (e.g. because GNOME's
    workspace count is higher than the config lists, or because the active
    workspace index is) are implicit "dynamic" slots — this makes them
    explicit so callers can index into a uniform list.
    """
    padded_len = max(len(config_workspaces), min_length)
    return list(config_workspaces) + [
        default_entry(slot, "dynamic") for slot in range(len(config_workspaces), padded_len)
    ]


def _trim_trailing_default_entries(workspaces_list: list[WorkspaceConfigEntry]) -> None:
    """Drop trailing bare "ET-<n>" entries (mutating in place).

    A trailing bare, unlinked, dynamic entry is indistinguishable from a
    slot that was never listed in the config at all (every other command
    already treats a missing entry the same as a bare one), so trimming
    them back off keeps the saved config from growing forever just because
    it was padded out for a shift/delete.
    """
    while workspaces_list and workspaces_list[-1] == default_entry(
        len(workspaces_list) - 1, workspaces_list[-1].type
    ):
        workspaces_list.pop()


def delete_active_workspace(*, force: bool = False) -> WsDeleteResult:
    """Delete the active workspace's slot, shifting later ones left to fill the gap.

    Only works on a "free" workspace — non-`static`, and with no Jira `ref`
    linked (the same definition `et jira start` uses to find an empty
    slot to reuse). Raises `WsDeleteError` if the active workspace is
    `static`; use `et jira complete` (or `et jira log-time`) first to free
    a Jira-linked workspace, or pass `force=True` to delete it anyway
    (its Tracker timer, if any, is discarded rather than logged).

    Every non-static workspace after the active one (and its Tracker
    timer) is shifted one slot to the left, same as `et jira complete`, so
    the freed bare slot ends up at the end of the non-static range. That
    now-empty trailing slot is then removed: GNOME's workspace count
    (`num-workspaces`) is decremented by one. The one exception is when the
    highest-numbered workspace is `static` (so shrinking would swallow it) —
    then the count is left untouched and the freed slot simply becomes an
    empty "ET-<n>" workspace instead. Refuses to delete the last remaining
    workspace.

    Raises `ConfigError` if the config file is missing/malformed,
    `WorkspaceError` (unwrapped) if the active workspace can't be
    determined, and `WsDeleteError` if the checks above fail or the
    underlying GNOME/Tracker operations fail.
    """
    config: EtConfig = load_config()
    index = workspaces.get_active_workspace_index()

    try:
        count = workspaces.get_workspace_count()
    except WorkspaceError as exc:
        raise WsDeleteError(str(exc)) from exc

    if count <= 1:
        raise WsDeleteError("cannot delete the last remaining workspace")

    workspaces_list = _pad_workspaces_list(config.workspaces, max(count, index + 1))

    entry = workspaces_list[index]
    if entry.type == "static":
        raise WsDeleteError(f"workspace {index + 1} is a static workspace and can't be deleted")
    if entry.ref is not None and not force:
        key = jira_key_from_ref(entry.ref) or entry.ref
        raise WsDeleteError(
            f"workspace {index + 1} is linked to {key}; complete or unlink it first "
            "(or pass --force to delete it anyway)"
        )

    try:
        entries = tracker.load_timers()
    except TrackerError as exc:
        raise WsDeleteError(str(exc)) from exc

    workspaces_list[index] = default_entry(index, entry.type)

    # Discard the deleted workspace's own Tracker timer up front. The shift
    # below only removes it as a side effect of moving a later timer into
    # this slot, so it would be left orphaned when there's nothing to shift
    # (e.g. deleting the last non-static workspace).
    deleted_timer = find_timer_for_workspace(entries, index)
    if deleted_timer is not None:
        entries.remove(deleted_timer)

    timers_changed = shift_workspaces_left(workspaces_list, entries, index)
    timers_changed = timers_changed or deleted_timer is not None

    # Reclaim the freed slot by shrinking GNOME's workspace count, unless the
    # highest-numbered workspace is static (shrinking removes the last GNOME
    # workspace, so we mustn't when that's a static one we never touch).
    shrink = workspaces_list[count - 1].type != "static"
    new_count = count - 1 if shrink else count
    rename_source = workspaces_list[:new_count] if shrink else workspaces_list
    rename_names = [item.name for item in rename_source]

    saved_list = list(workspaces_list)
    _trim_trailing_default_entries(saved_list)

    try:
        if timers_changed:
            tracker.save_timers_with_reload(entries, "shifting timers after deleting a workspace")
        if shrink:
            workspaces.set_workspace_count(new_count)
        save_config(replace(config, workspaces=saved_list))
        # Rename after any shrink so the workspace-names array matches the
        # (possibly reduced) set of live GNOME workspaces.
        workspaces.rename_all_workspaces(rename_names)
        workspaces.switch_to_workspace(min(index, new_count - 1))
    except (ConfigError, WorkspaceError, TrackerError) as exc:
        raise WsDeleteError(str(exc)) from exc

    return WsDeleteResult(workspace_index=index, remaining_workspaces=new_count)


def prepare_organize(
    config: EtConfig, workspace_count: int
) -> tuple[list[WorkspaceConfigEntry], list[int]]:
    """Return the padded workspaces list and the ascending list of non-`static` slots.

    Pads `config.workspaces` out to `workspace_count` (same as
    `delete_active_workspace` does) so implicit trailing "dynamic" slots
    are visible, then returns which of those slots are reorderable (every
    slot whose `type` isn't `"static"`).
    """
    workspaces_list = _pad_workspaces_list(config.workspaces, workspace_count)
    slots = [i for i, entry in enumerate(workspaces_list) if entry.type != "static"]
    return workspaces_list, slots


def list_organize_candidates(
    workspaces_list: list[WorkspaceConfigEntry], slots: list[int], entries: list[TimerEntry]
) -> list[OrganizeCandidate]:
    """Return one `OrganizeCandidate` per slot in `slots`, in the given order."""
    return [
        OrganizeCandidate(
            slot=slot, entry=workspaces_list[slot], timer=find_timer_for_workspace(entries, slot)
        )
        for slot in slots
    ]


def format_organize_candidate_line(candidate: OrganizeCandidate) -> str:
    """Format one `OrganizeCandidate` as a single tab-separated editor line."""
    key = jira_key_from_ref(candidate.entry.ref) or "-"
    if candidate.timer is not None:
        elapsed = candidate.timer.get("timeElapsed", 0)
        seconds = elapsed if isinstance(elapsed, (int, float)) else 0
        running = " (running)" if candidate.timer.get("running") else ""
        timer_desc = f"{tracker.format_duration(seconds)}{running}"
    else:
        timer_desc = "no timer"
    return f"{candidate.slot + 1}\t{candidate.entry.name}\t{key}\t{timer_desc}"


def build_organize_editor_content(candidates: list[OrganizeCandidate]) -> str:
    """Build the git-rebase-todo-style text shown to the user in `$EDITOR`.

    Each non-comment line starts with the workspace's original 1-based slot
    number; reordering the lines (without adding/removing any) is how the
    user expresses the desired new order. Parsed back by
    `parse_organize_order`.
    """
    header = [
        "# Reorder the lines below to change the order of your dynamic workspaces.",
        "# Do not add, remove, or duplicate lines -- only reorder them.",
        "# Lines starting with '#' (and blank lines) are ignored.",
        "#",
        "# slot\tname\tjira\ttimer",
    ]
    lines = [format_organize_candidate_line(candidate) for candidate in candidates]
    return "\n".join(header + lines) + "\n"


def parse_organize_order(lines: list[str], valid_slots: list[int]) -> list[int]:
    """Parse the edited listing back into a 0-based slot order.

    Reads the leading integer token off each non-blank, non-comment ("#")
    line and converts it from the 1-based slot number shown to the user
    back to a 0-based slot index. Raises `WsOrganizeError` unless the
    result is exactly a permutation of `valid_slots` (nothing added,
    removed, or duplicated).
    """
    order: list[int] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        token = stripped.split(maxsplit=1)[0]
        try:
            slot_number = int(token)
        except ValueError as exc:
            raise WsOrganizeError(f"could not parse workspace slot from line: {line!r}") from exc
        order.append(slot_number - 1)

    if sorted(order) != sorted(valid_slots):
        expected = ", ".join(str(slot + 1) for slot in sorted(valid_slots))
        raise WsOrganizeError(
            "the edited list must contain each of the original workspace slots exactly "
            f"once (expected: {expected})"
        )

    return order


def build_organize_plan(
    workspaces_list: list[WorkspaceConfigEntry],
    entries: list[TimerEntry],
    slots: list[int],
    new_order: list[int],
) -> list[OrganizePlanRow]:
    """Compute the result of permuting `slots`' contents according to `new_order`.

    `new_order[i]` is the *original* slot whose entry/timer ends up at
    `slots[i]`. Since this is a closed permutation over the same slot set
    (unlike `shift_workspaces_left`'s partial shift), every slot is both a
    source and a destination exactly once, so no entry or Tracker timer is
    ever dropped or orphaned — each row just describes where its old
    slot's content is going. A moved timer (its slot actually changes) has
    its `workspaceId`/`name` updated to match its new slot, matching the
    "ET-<n>" convention `shift_workspaces_left` already uses; an unmoved
    timer is returned unchanged.
    """
    if sorted(new_order) != sorted(slots):
        raise WsOrganizeError("new_order must be a permutation of slots")

    old_entries_by_slot = {slot: workspaces_list[slot] for slot in slots}
    old_timers_by_slot = {slot: find_timer_for_workspace(entries, slot) for slot in slots}

    rows = []
    for position, src_slot in enumerate(new_order):
        dst_slot = slots[position]
        source_entry = old_entries_by_slot[src_slot]
        moved_entry = WorkspaceConfigEntry(
            name=source_entry.name,
            type=workspaces_list[dst_slot].type,
            ref=source_entry.ref,
            description=source_entry.description,
        )

        source_timer = old_timers_by_slot[src_slot]
        moved_timer: TimerEntry | None = None
        if source_timer is not None:
            if src_slot == dst_slot:
                moved_timer = source_timer
            else:
                moved_timer = dict(source_timer)
                moved_timer["workspaceId"] = dst_slot
                moved_timer["name"] = f"ET-{dst_slot + 1}"

        rows.append(
            OrganizePlanRow(
                old_slot=src_slot, new_slot=dst_slot, entry=moved_entry, timer=moved_timer
            )
        )

    return rows


def apply_organize_plan(
    config: EtConfig,
    workspaces_list: list[WorkspaceConfigEntry],
    entries: list[TimerEntry],
    plan: list[OrganizePlanRow],
) -> None:
    """Persist a `build_organize_plan` result: config, Tracker timers, GNOME names.

    Rebuilds `workspaces_list` slot-by-slot from `plan`, and rebuilds
    `entries` by dropping every timer bound to an affected slot and
    re-adding each row's (possibly re-indexed) timer — safe because `plan`
    is a closed permutation of the same slot set, so every affected slot
    gets exactly one timer back. Raises `WsOrganizeError` wrapping any
    `ConfigError`/`WorkspaceError`/`TrackerError`.
    """
    new_workspaces_list = list(workspaces_list)
    for row in plan:
        new_workspaces_list[row.new_slot] = row.entry

    affected_slots = {row.new_slot for row in plan}
    new_entries = [entry for entry in entries if entry.get("workspaceId") not in affected_slots]
    for row in plan:
        if row.timer is not None:
            new_entries.append(row.timer)

    timers_changed = any(row.old_slot != row.new_slot and row.timer is not None for row in plan)

    saved_list = list(new_workspaces_list)
    _trim_trailing_default_entries(saved_list)

    rename_names = [item.name for item in new_workspaces_list]

    try:
        if timers_changed:
            tracker.save_timers_with_reload(new_entries, "reorganizing workspaces")
        save_config(replace(config, workspaces=saved_list))
        workspaces.rename_all_workspaces(rename_names)
    except (ConfigError, WorkspaceError, TrackerError) as exc:
        raise WsOrganizeError(str(exc)) from exc


def open_in_editor(content: str) -> str:
    """Write `content` to a temp file, open it in `$EDITOR`, and return its final contents.

    Falls back to "vi" if `$EDITOR` is unset. Raises `WsOrganizeError` if
    the configured editor can't be found, or if it exits non-zero.
    """
    editor = os.environ.get("EDITOR") or "vi"
    editor_command = editor.split()
    if not editor_command:
        raise WsOrganizeError("$EDITOR is set to an empty command")
    if shutil.which(editor_command[0]) is None:
        raise WsOrganizeError(
            f"editor {editor_command[0]!r} not found; set $EDITOR to a valid command"
        )

    fd, path_str = tempfile.mkstemp(prefix="et-ws-organize-", suffix=".txt")
    path = Path(path_str)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)

        result = subprocess.run([*editor_command, str(path)], check=False)
        if result.returncode != 0:
            raise WsOrganizeError(f"editor exited with status {result.returncode}")

        return path.read_text()
    finally:
        path.unlink(missing_ok=True)


__all__ = [
    "WsDeleteError",
    "WsDeleteResult",
    "WsOrganizeError",
    "OrganizeCandidate",
    "OrganizePlanRow",
    "shift_workspaces_left",
    "delete_active_workspace",
    "prepare_organize",
    "list_organize_candidates",
    "format_organize_candidate_line",
    "build_organize_editor_content",
    "parse_organize_order",
    "build_organize_plan",
    "apply_organize_plan",
    "open_in_editor",
]
