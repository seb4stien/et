"""Tests for et.ws, mocking et.config/et.workspaces/et.tracker."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from et.config import ConfigError, EtConfig, WorkspaceConfigEntry
from et.tracker import TrackerError
from et.workspaces import WorkspaceError
from et.ws import (
    WsDeleteError,
    WsOrganizeError,
    _trim_trailing_default_entries,
    apply_organize_plan,
    build_organize_plan,
    delete_active_workspace,
    list_organize_candidates,
    parse_organize_order,
    prepare_organize,
    shift_workspaces_left,
)


def _config(
    workspaces: list[WorkspaceConfigEntry] | None = None,
) -> EtConfig:
    return EtConfig(jira=None, workspaces=workspaces or [])


def _timer(workspace_id: int, name: str, elapsed: int = 0, running: bool = False) -> dict:
    return {
        "id": f"timer-{workspace_id}",
        "name": name,
        "timeElapsed": elapsed,
        "running": running,
        "selected": False,
        "workspaceId": workspace_id,
        "autoResume": True,
    }


# --- shift_workspaces_left ---------------------------------------------------


def test_shift_workspaces_left_moves_later_slots_and_timers():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ET-2"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C", description="stuff"),
    ]
    stale_timer = _timer(1, "ET-2", elapsed=0)
    c_timer = _timer(2, "ET-3", elapsed=42, running=True)
    entries = [stale_timer, c_timer]

    changed = shift_workspaces_left(workspaces_list, entries, 1)

    assert changed is True
    assert workspaces_list == [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C", description="stuff"),
        WorkspaceConfigEntry(name="ET-3"),
    ]
    assert stale_timer not in entries
    assert c_timer in entries
    assert c_timer["workspaceId"] == 1
    assert c_timer["name"] == "ET-2"


def test_shift_workspaces_left_noop_when_freed_is_static():
    workspaces_list = [WorkspaceConfigEntry(name="mails", type="static")]
    changed = shift_workspaces_left(workspaces_list, [], 0)
    assert changed is False


def test_shift_workspaces_left_noop_when_last_slot():
    workspaces_list = [WorkspaceConfigEntry(name="ET-1")]
    changed = shift_workspaces_left(workspaces_list, [], 0)
    assert changed is False


def test_shift_workspaces_left_preserves_destination_type():
    workspaces_list = [
        WorkspaceConfigEntry(name="mails", type="static"),
        WorkspaceConfigEntry(name="ET-2"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
    ]
    changed = shift_workspaces_left(workspaces_list, [], 1)
    assert changed is False  # no timers to move
    assert workspaces_list == [
        WorkspaceConfigEntry(name="mails", type="static"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
        WorkspaceConfigEntry(name="ET-3"),
    ]


# --- _trim_trailing_default_entries ------------------------------------------


def test_trim_trailing_default_entries_removes_bare_tail():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ET-2"),
        WorkspaceConfigEntry(name="ET-3"),
    ]
    _trim_trailing_default_entries(workspaces_list)
    assert workspaces_list == [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")]


def test_trim_trailing_default_entries_keeps_linked_entries():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
    ]
    _trim_trailing_default_entries(workspaces_list)
    assert workspaces_list == [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
    ]


def test_trim_trailing_default_entries_stops_at_static():
    workspaces_list = [
        WorkspaceConfigEntry(name="mails", type="static"),
        WorkspaceConfigEntry(name="ET-2"),
    ]
    _trim_trailing_default_entries(workspaces_list)
    assert workspaces_list == [WorkspaceConfigEntry(name="mails", type="static")]


# --- delete_active_workspace --------------------------------------------------


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers")
@patch("et.ws.workspaces.get_workspace_count", return_value=3)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_shifts_and_shrinks(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ET-2"),
            WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
        ]
    )
    c_timer = _timer(2, "ET-3", elapsed=99)
    mock_load_timers.return_value = [c_timer]

    result = delete_active_workspace()

    assert result.workspace_index == 1
    assert result.remaining_workspaces == 2

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
    ]
    # The now-empty trailing slot is reclaimed by shrinking GNOME's count.
    mock_set_count.assert_called_once_with(2)
    mock_rename_all.assert_called_once_with(["ISD-A", "ISD-C"])
    mock_switch.assert_called_once_with(1)

    saved_timers = mock_save_timers.call_args[0][0]
    assert c_timer in saved_timers
    assert c_timer["workspaceId"] == 1
    assert c_timer["name"] == "ET-2"


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers", return_value=[])
@patch("et.ws.workspaces.get_workspace_count", return_value=5)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=2)
@patch("et.ws.load_config")
def test_delete_active_workspace_pads_implicit_slots(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    _mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    # Only one explicit entry configured; deleting slot index 2 (implicit,
    # beyond the explicit list) should be treated as a bare dynamic slot.
    mock_load_config.return_value = _config(
        [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")]
    )

    result = delete_active_workspace()

    assert result.workspace_index == 2
    assert result.remaining_workspaces == 4
    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")]
    mock_set_count.assert_called_once_with(4)
    mock_rename_all.assert_called_once_with(["ISD-A", "ET-2", "ET-3", "ET-4"])
    mock_switch.assert_called_once_with(2)
    mock_save_timers.assert_not_called()


@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_rejects_static(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="mails", type="static"),
        ]
    )
    with pytest.raises(WsDeleteError, match="static"):
        delete_active_workspace()


@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_rejects_linked_ref(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )
    with pytest.raises(WsDeleteError, match="ISD-B"):
        delete_active_workspace()


@patch("et.ws.workspaces.get_workspace_count", return_value=1)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_rejects_last_remaining(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])
    with pytest.raises(WsDeleteError, match="last remaining"):
        delete_active_workspace()


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers")
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_force_discards_linked_ref_and_timer(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )
    a_timer = _timer(0, "ET-1", elapsed=500, running=True)
    b_timer = _timer(1, "ET-2", elapsed=42)
    mock_load_timers.return_value = [a_timer, b_timer]

    result = delete_active_workspace(force=True)

    assert result.workspace_index == 0
    assert result.remaining_workspaces == 1

    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B")]
    mock_set_count.assert_called_once_with(1)
    mock_rename_all.assert_called_once_with(["ISD-B"])
    mock_switch.assert_called_once_with(0)

    saved_timers = mock_save_timers.call_args[0][0]
    assert a_timer not in saved_timers  # discarded, not logged
    assert b_timer in saved_timers
    assert b_timer["workspaceId"] == 0
    assert b_timer["name"] == "ET-1"


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers")
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_force_last_slot_discards_timer(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )
    b_timer = _timer(1, "ET-2", elapsed=500)
    mock_load_timers.return_value = [b_timer]

    result = delete_active_workspace(force=True)

    assert result.workspace_index == 1
    assert result.remaining_workspaces == 1
    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A")]
    mock_set_count.assert_called_once_with(1)
    mock_rename_all.assert_called_once_with(["ISD-A"])
    mock_switch.assert_called_once_with(0)
    # Deleting the last non-static slot has nothing to shift, but the deleted
    # workspace's own timer must still be discarded rather than orphaned.
    mock_save_timers.assert_called_once()
    saved_timers = mock_save_timers.call_args[0][0]
    assert b_timer not in saved_timers
    assert saved_timers == []


@patch("et.ws.workspaces.switch_to_workspace")
@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.tracker.load_timers", return_value=[])
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_keeps_count_when_last_is_static(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    _mock_load_timers,
    mock_save_timers,
    mock_save_config,
    mock_set_count,
    mock_rename_all,
    mock_switch,
):
    # Deleting slot 0 when the highest-numbered workspace is static: shrinking
    # would swallow the static one, so the count is left untouched and the
    # freed slot just becomes a bare "ET-<n>".
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="scratch-task"),
            WorkspaceConfigEntry(name="mails", type="static"),
        ]
    )

    result = delete_active_workspace()

    assert result.workspace_index == 0
    assert result.remaining_workspaces == 2
    mock_set_count.assert_not_called()
    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces == [
        WorkspaceConfigEntry(name="ET-1"),
        WorkspaceConfigEntry(name="mails", type="static"),
    ]
    mock_rename_all.assert_called_once_with(["ET-1", "mails"])
    mock_switch.assert_called_once_with(0)


@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=1)
@patch("et.ws.load_config")
def test_delete_active_workspace_force_still_rejects_static(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="mails", type="static"),
        ]
    )
    with pytest.raises(WsDeleteError, match="static"):
        delete_active_workspace(force=True)


@patch("et.ws.workspaces.get_workspace_count", return_value=5)
@patch("et.ws.workspaces.get_active_workspace_index", side_effect=WorkspaceError("no active ws"))
@patch("et.ws.load_config")
def test_delete_active_workspace_propagates_workspace_error(
    mock_load_config, _mock_active_index, _mock_get_count
):
    mock_load_config.return_value = _config([])
    with pytest.raises(WorkspaceError, match="no active ws"):
        delete_active_workspace()


@patch("et.ws.load_config", side_effect=ConfigError("bad config"))
def test_delete_active_workspace_propagates_config_error(_mock_load_config):
    with pytest.raises(ConfigError, match="bad config"):
        delete_active_workspace()


@patch("et.ws.tracker.load_timers", side_effect=TrackerError("tracker down"))
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_wraps_tracker_error(
    mock_load_config, _mock_active_index, _mock_get_count, _mock_load_timers
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])
    with pytest.raises(WsDeleteError, match="tracker down"):
        delete_active_workspace()


@patch("et.ws.workspaces.rename_all_workspaces", side_effect=WorkspaceError("rename boom"))
@patch("et.ws.workspaces.set_workspace_count")
@patch("et.ws.save_config")
@patch("et.ws.tracker.load_timers", return_value=[])
@patch("et.ws.workspaces.get_workspace_count", return_value=2)
@patch("et.ws.workspaces.get_active_workspace_index", return_value=0)
@patch("et.ws.load_config")
def test_delete_active_workspace_wraps_workspace_error_during_apply(
    mock_load_config,
    _mock_active_index,
    _mock_get_count,
    _mock_load_timers,
    _mock_save_config,
    _mock_set_count,
    _mock_rename_all,
):
    mock_load_config.return_value = _config([WorkspaceConfigEntry(name="ET-1")])
    with pytest.raises(WsDeleteError, match="rename boom"):
        delete_active_workspace()


# --- prepare_organize / list_organize_candidates -----------------------------


def test_prepare_organize_pads_and_filters_static():
    config = _config(
        [
            WorkspaceConfigEntry(name="mails", type="static"),
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        ]
    )
    workspaces_list, slots = prepare_organize(config, 4)
    assert len(workspaces_list) == 4
    assert slots == [1, 2, 3]


def test_list_organize_candidates_pairs_entries_and_timers():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ET-2"),
    ]
    entries = [_timer(0, "ET-1", elapsed=99)]
    candidates = list_organize_candidates(workspaces_list, [0, 1], entries)
    assert candidates[0].slot == 0
    assert candidates[0].entry.name == "ISD-A"
    assert candidates[0].timer is entries[0]
    assert candidates[1].slot == 1
    assert candidates[1].timer is None


# --- parse_organize_order -----------------------------------------------------


def test_parse_organize_order_happy_path():
    lines = ["# comment", "", "2", "1\tISD-A\tISD-A\tno timer", "3"]
    assert parse_organize_order(lines, [0, 1, 2]) == [1, 0, 2]


def test_parse_organize_order_ignores_comments_and_blanks():
    lines = ["# header", "  ", "1", "# mid comment", "2"]
    assert parse_organize_order(lines, [0, 1]) == [0, 1]


def test_parse_organize_order_rejects_duplicate_slot():
    lines = ["1", "1"]
    with pytest.raises(WsOrganizeError, match="exactly once"):
        parse_organize_order(lines, [0, 1])


def test_parse_organize_order_rejects_missing_slot():
    lines = ["1"]
    with pytest.raises(WsOrganizeError, match="exactly once"):
        parse_organize_order(lines, [0, 1])


def test_parse_organize_order_rejects_foreign_slot():
    lines = ["1", "5"]
    with pytest.raises(WsOrganizeError, match="exactly once"):
        parse_organize_order(lines, [0, 1])


def test_parse_organize_order_rejects_unparseable_line():
    lines = ["not-a-number"]
    with pytest.raises(WsOrganizeError, match="could not parse"):
        parse_organize_order(lines, [0])


# --- build_organize_plan ------------------------------------------------------


def test_build_organize_plan_swap_moves_timers():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
    ]
    timer_a = _timer(0, "ET-1", elapsed=10)
    timer_b = _timer(1, "ET-2", elapsed=20)
    entries = [timer_a, timer_b]

    plan = build_organize_plan(workspaces_list, entries, [0, 1], [1, 0])

    by_new_slot = {row.new_slot: row for row in plan}
    assert by_new_slot[0].entry.name == "ISD-B"
    assert by_new_slot[0].old_slot == 1
    assert by_new_slot[0].timer is not None
    assert by_new_slot[0].timer["workspaceId"] == 0
    assert by_new_slot[0].timer["name"] == "ET-1"
    assert by_new_slot[0].timer["timeElapsed"] == 20

    assert by_new_slot[1].entry.name == "ISD-A"
    assert by_new_slot[1].timer is not None
    assert by_new_slot[1].timer["workspaceId"] == 1
    assert by_new_slot[1].timer["name"] == "ET-2"
    assert by_new_slot[1].timer["timeElapsed"] == 10

    # Original timers must be untouched (build_organize_plan is pure).
    assert timer_a["workspaceId"] == 0
    assert timer_b["workspaceId"] == 1


def test_build_organize_plan_three_way_rotation():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        WorkspaceConfigEntry(name="ISD-C", ref="jira:ISD-C"),
    ]
    entries = [_timer(0, "ET-1"), _timer(1, "ET-2"), _timer(2, "ET-3")]

    # new_order[i] = source slot landing at slots[i]: rotate right by one.
    plan = build_organize_plan(workspaces_list, entries, [0, 1, 2], [2, 0, 1])

    by_new_slot = {row.new_slot: row for row in plan}
    assert by_new_slot[0].entry.name == "ISD-C"
    assert by_new_slot[1].entry.name == "ISD-A"
    assert by_new_slot[2].entry.name == "ISD-B"


def test_build_organize_plan_noop_order_keeps_timer_identity():
    workspaces_list = [
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
    ]
    timer_a = _timer(0, "ET-1")
    entries = [timer_a]

    plan = build_organize_plan(workspaces_list, entries, [0, 1], [0, 1])

    by_new_slot = {row.new_slot: row for row in plan}
    assert by_new_slot[0].timer is timer_a
    assert by_new_slot[1].timer is None


def test_build_organize_plan_static_slots_excluded_from_slots_arg():
    workspaces_list = [
        WorkspaceConfigEntry(name="mails", type="static"),
        WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
        WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
    ]
    entries: list[dict] = []
    plan = build_organize_plan(workspaces_list, entries, [1, 2], [2, 1])
    assert {row.old_slot for row in plan} == {1, 2}
    assert {row.new_slot for row in plan} == {1, 2}
    assert all(row.entry.type == "dynamic" for row in plan)


def test_build_organize_plan_rejects_non_permutation():
    workspaces_list = [WorkspaceConfigEntry(name="ISD-A"), WorkspaceConfigEntry(name="ISD-B")]
    with pytest.raises(WsOrganizeError):
        build_organize_plan(workspaces_list, [], [0, 1], [0, 0])


def test_build_organize_plan_entry_without_timer_produces_none_row():
    workspaces_list = [WorkspaceConfigEntry(name="ISD-A"), WorkspaceConfigEntry(name="ISD-B")]
    plan = build_organize_plan(workspaces_list, [], [0, 1], [1, 0])
    assert all(row.timer is None for row in plan)


# --- apply_organize_plan -------------------------------------------------------


@patch("et.ws.workspaces.rename_all_workspaces")
@patch("et.ws.save_config")
@patch("et.ws.tracker.save_timers_with_reload")
def test_apply_organize_plan_saves_config_timers_and_names(
    mock_save_timers, mock_save_config, mock_rename_all
):
    config = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )
    workspaces_list = list(config.workspaces)
    timer_a = _timer(0, "ET-1", elapsed=5)
    timer_b = _timer(1, "ET-2", elapsed=15)
    entries = [timer_a, timer_b]

    plan = build_organize_plan(workspaces_list, entries, [0, 1], [1, 0])
    apply_organize_plan(config, workspaces_list, entries, plan)

    mock_save_timers.assert_called_once()
    saved_entries = mock_save_timers.call_args[0][0]
    saved_by_workspace = {entry["workspaceId"]: entry for entry in saved_entries}
    assert saved_by_workspace[0]["timeElapsed"] == 15
    assert saved_by_workspace[1]["timeElapsed"] == 5

    mock_save_config.assert_called_once()
    saved_config = mock_save_config.call_args[0][0]
    assert saved_config.workspaces[0].name == "ISD-B"
    assert saved_config.workspaces[1].name == "ISD-A"

    mock_rename_all.assert_called_once_with(["ISD-B", "ISD-A"])


@patch("et.ws.tracker.save_timers_with_reload")
@patch("et.ws.save_config")
def test_apply_organize_plan_skips_timer_save_when_noop(mock_save_config, mock_save_timers):
    config = _config(
        [
            WorkspaceConfigEntry(name="ISD-A", ref="jira:ISD-A"),
            WorkspaceConfigEntry(name="ISD-B", ref="jira:ISD-B"),
        ]
    )
    workspaces_list = list(config.workspaces)
    entries: list[dict] = []
    plan = build_organize_plan(workspaces_list, entries, [0, 1], [0, 1])

    with patch("et.ws.workspaces.rename_all_workspaces"):
        apply_organize_plan(config, workspaces_list, entries, plan)

    mock_save_timers.assert_not_called()
    mock_save_config.assert_called_once()


@patch("et.ws.workspaces.rename_all_workspaces", side_effect=WorkspaceError("rename boom"))
@patch("et.ws.save_config")
def test_apply_organize_plan_wraps_workspace_error(_mock_save_config, _mock_rename_all):
    config = _config([WorkspaceConfigEntry(name="ISD-A"), WorkspaceConfigEntry(name="ISD-B")])
    workspaces_list = list(config.workspaces)
    plan = build_organize_plan(workspaces_list, [], [0, 1], [1, 0])
    with pytest.raises(WsOrganizeError, match="rename boom"):
        apply_organize_plan(config, workspaces_list, [], plan)


@patch("et.ws.save_config", side_effect=ConfigError("bad write"))
def test_apply_organize_plan_wraps_config_error(_mock_save_config):
    config = _config([WorkspaceConfigEntry(name="ISD-A"), WorkspaceConfigEntry(name="ISD-B")])
    workspaces_list = list(config.workspaces)
    plan = build_organize_plan(workspaces_list, [], [0, 1], [1, 0])
    with pytest.raises(WsOrganizeError, match="bad write"):
        apply_organize_plan(config, workspaces_list, [], plan)
