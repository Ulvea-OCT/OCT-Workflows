#!/usr/bin/env python3
"""Schedule a parsed roadmap while respecting dependencies and a concurrency cap.

The Markdown roadmap remains the source of truth. The scheduler preserves each
issue's declared duration and declared earliest start, then shifts tasks later
when dependencies or the configured maximum number of parallel tasks require it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

PRIORITY_RANK = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def fmt(value: dt.date) -> str:
    return value.isoformat()


def duration_days(task: dict) -> int:
    start = parse_date(task["start"])
    target = parse_date(task["target"])
    days = (target - start).days + 1
    if days < 1:
        raise ValueError(f"{task['id']}: target must be on/after start")
    return days


def build_graph(tasks: list[dict]) -> tuple[dict[str, dict], dict[str, set[str]], dict[str, set[str]]]:
    by_id = {task["id"]: task for task in tasks}
    predecessors = {task["id"]: set(task.get("blocked_by") or []) for task in tasks}
    successors = {task["id"]: set() for task in tasks}

    for task in tasks:
        for dep in predecessors[task["id"]]:
            if dep not in by_id:
                raise ValueError(f"{task['id']}: blocked_by references unknown task {dep}")
            if dep == task["id"]:
                raise ValueError(f"{task['id']}: task cannot block itself")
            successors[dep].add(task["id"])

    # Detect cycles with Kahn's algorithm.
    indegree = {task_id: len(deps) for task_id, deps in predecessors.items()}
    ready = sorted([task_id for task_id, degree in indegree.items() if degree == 0])
    seen = []
    while ready:
        current = ready.pop(0)
        seen.append(current)
        for child in sorted(successors[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(seen) != len(tasks):
        remaining = sorted(set(by_id) - set(seen))
        raise ValueError("Dependency cycle detected involving: " + ", ".join(remaining))

    return by_id, predecessors, successors


def priority_key(task: dict) -> tuple:
    return (
        task["start"],
        PRIORITY_RANK.get(str(task.get("priority", "p9")).lower(), 9),
        task["target"],
        task["id"],
    )


def overlaps(start: dt.date, end: dt.date, scheduled: dict[str, dict]) -> int:
    count = 0
    for item in scheduled.values():
        other_start = parse_date(item["scheduled_start"])
        other_end = parse_date(item["scheduled_target"])
        if start <= other_end and end >= other_start:
            count += 1
    return count


def schedule(payload: dict) -> dict:
    config = payload.get("scheduler") or {}
    max_parallel = config.get("max_parallel_tasks", 4)
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or max_parallel < 1:
        raise ValueError("scheduler.max_parallel_tasks must be an integer >= 1")

    tasks = payload["issues"]
    by_id, predecessors, _ = build_graph(tasks)

    # Topological scheduling with a deterministic ready queue. We prioritize
    # declared start date, then priority, then target date. This preserves the
    # roadmap's intent while allowing dependency-driven shifts and the global
    # concurrency cap.
    remaining = {task["id"] for task in tasks}
    scheduled: dict[str, dict] = {}

    while remaining:
        ready = [
            by_id[task_id]
            for task_id in remaining
            if all(dep in scheduled for dep in predecessors[task_id])
        ]
        if not ready:
            raise ValueError("Unable to schedule roadmap: dependency graph is not acyclic")
        ready.sort(key=priority_key)

        task = ready[0]
        task_id = task["id"]
        declared_start = parse_date(task["start"])
        duration = duration_days(task)

        earliest = declared_start
        dependency_reasons = []
        for dep in sorted(predecessors[task_id]):
            dep_end = parse_date(scheduled[dep]["scheduled_target"])
            candidate = dep_end + dt.timedelta(days=1)
            if candidate > earliest:
                earliest = candidate
                dependency_reasons.append(dep)

        start = earliest
        while True:
            end = start + dt.timedelta(days=duration - 1)
            if overlaps(start, end, scheduled) < max_parallel:
                break
            start += dt.timedelta(days=1)

        item = dict(task)
        item["declared_start"] = task["start"]
        item["declared_target"] = task["target"]
        item["duration_days"] = duration
        item["scheduled_start"] = fmt(start)
        item["scheduled_target"] = fmt(end)
        item["schedule_shift_days"] = (start - declared_start).days
        item["schedule_shift_reason"] = (
            "dependency: " + ", ".join(dependency_reasons)
            if dependency_reasons
            else ("parallelism-cap" if start > declared_start else "")
        )
        scheduled[task_id] = item
        remaining.remove(task_id)

    scheduled_tasks = [scheduled[task["id"]] for task in tasks]
    delayed = [task for task in scheduled_tasks if task["schedule_shift_days"] > 0]
    max_active = 0
    if scheduled_tasks:
        first = min(parse_date(t["scheduled_start"]) for t in scheduled_tasks)
        last = max(parse_date(t["scheduled_target"]) for t in scheduled_tasks)
        cursor = first
        while cursor <= last:
            active = sum(
                parse_date(t["scheduled_start"]) <= cursor <= parse_date(t["scheduled_target"])
                for t in scheduled_tasks
            )
            max_active = max(max_active, active)
            cursor += dt.timedelta(days=1)

    result = dict(payload)
    result["scheduler"] = {
        **config,
        "max_parallel_tasks": max_parallel,
        "algorithm": "dependency-aware-list-scheduling",
        "scheduled_task_count": len(scheduled_tasks),
        "delayed_task_count": len(delayed),
        "max_observed_parallel_tasks": max_active,
    }
    result["issues"] = scheduled_tasks
    result["schedule"] = {
        "max_parallel_tasks": max_parallel,
        "delayed_tasks": [
            {
                "id": task["id"],
                "declared_start": task["declared_start"],
                "declared_target": task["declared_target"],
                "scheduled_start": task["scheduled_start"],
                "scheduled_target": task["scheduled_target"],
                "shift_days": task["schedule_shift_days"],
                "reason": task["schedule_shift_reason"],
            }
            for task in delayed
        ],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = schedule(payload)
        Path(args.output).write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Project: {result['project']}")
        print(f"Max parallel tasks: {result['scheduler']['max_parallel_tasks']}")
        print(f"Tasks: {len(result['issues'])}")
        print(f"Delayed by scheduler: {result['scheduler']['delayed_task_count']}")
        print(f"Max observed parallel tasks: {result['scheduler']['max_observed_parallel_tasks']}")
        for task in result["schedule"]["delayed_tasks"]:
            print(
                f"  ⚠ {task['id']}: {task['declared_start']} → {task['scheduled_start']} "
                f"(+{task['shift_days']}d; {task['reason']})"
            )
        print(f"Output: {args.output}")
        return 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
