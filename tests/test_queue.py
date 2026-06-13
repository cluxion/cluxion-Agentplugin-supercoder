from __future__ import annotations

import pytest

from cluxion_agentplugin_supercoder.core.queue import WorkStatus, plan_coding_task


def test_plan_creates_dependency_chain() -> None:
    queue = plan_coding_task("t1", "fix the parser")
    assert [unit.id for unit in queue.units] == ["map", "edit", "verify", "brief"]
    assert queue.units[1].dependencies == ("map",)


def test_next_unit_respects_dependencies() -> None:
    queue = plan_coding_task("t1", "fix the parser")
    first = queue.next_unit()
    assert first is not None and first.id == "map"
    assert first.status == WorkStatus.RUNNING
    # map is running, not complete: nothing else is dispatchable.
    assert queue.next_unit() is None
    queue.record("map", success=True)
    second = queue.next_unit()
    assert second is not None and second.id == "edit"


def test_record_failure_blocks_dependents() -> None:
    queue = plan_coding_task("t1", "fix the parser")
    queue.next_unit()
    result = queue.record("map", success=False)
    assert result["status"] == "failed"
    assert queue.next_unit() is None  # edit depends on map, which never completed
    assert result["remaining"] == 3  # edit/verify/brief still pending


def test_record_unknown_unit_raises() -> None:
    queue = plan_coding_task("t1", "fix the parser")
    with pytest.raises(KeyError):
        queue.record("nope", success=True)


def test_full_lifecycle_drains_queue() -> None:
    queue = plan_coding_task("t1", "fix the parser")
    order: list[str] = []
    while (unit := queue.next_unit()) is not None:
        order.append(unit.id)
        queue.record(unit.id, success=True, evidence=(f"{unit.id}_done",))
    assert order == ["map", "edit", "verify", "brief"]
    assert all(unit.status == WorkStatus.COMPLETE for unit in queue.units)
