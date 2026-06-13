"""Coding work unit queue — deterministic, no model calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class WorkStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class WorkUnit:
    id: str
    goal: str
    priority: int = 2
    allowed_paths: tuple[str, ...] = ()
    line_budget: int = 250
    status: WorkStatus = WorkStatus.PENDING
    expected_evidence: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()


@dataclass
class TaskQueue:
    task_id: str
    units: list[WorkUnit] = field(default_factory=list)

    def enqueue(self, unit: WorkUnit) -> None:
        self.units.append(unit)

    def next_unit(self) -> WorkUnit | None:
        completed = {unit.id for unit in self.units if unit.status == WorkStatus.COMPLETE}
        for unit in sorted(self.units, key=lambda item: (item.priority, item.id)):
            if unit.status != WorkStatus.PENDING:
                continue
            if all(dep in completed for dep in unit.dependencies):
                unit.status = WorkStatus.RUNNING
                return unit
        return None

    def record(self, unit_id: str, *, success: bool, evidence: tuple[str, ...] = ()) -> dict[str, object]:
        for unit in self.units:
            if unit.id != unit_id:
                continue
            unit.status = WorkStatus.COMPLETE if success else WorkStatus.FAILED
            unit.expected_evidence = evidence or unit.expected_evidence
            return {
                "task_id": self.task_id,
                "unit_id": unit_id,
                "status": unit.status.value,
                "remaining": sum(1 for item in self.units if item.status == WorkStatus.PENDING),
            }
        raise KeyError(unit_id)


def plan_coding_task(task_id: str, prompt: str) -> TaskQueue:
    queue = TaskQueue(task_id=task_id)
    queue.enqueue(WorkUnit("map", "Map repo and identify target files", priority=0, expected_evidence=("cursor_map",)))
    queue.enqueue(
        WorkUnit(
            "edit",
            f"Apply focused changes for: {prompt[:240]}",
            priority=1,
            dependencies=("map",),
            expected_evidence=("files_changed",),
        )
    )
    queue.enqueue(
        WorkUnit(
            "verify",
            "Run targeted tests or lint for changed files",
            priority=2,
            dependencies=("edit",),
            expected_evidence=("tests_run",),
        )
    )
    queue.enqueue(
        WorkUnit(
            "brief",
            "Summarize changes, verification, and remaining risks",
            priority=3,
            dependencies=("verify",),
            expected_evidence=("brief",),
        )
    )
    return queue


__all__ = ["TaskQueue", "WorkStatus", "WorkUnit", "plan_coding_task"]
