"""Goal records stored on state['goals'].

Official SE2 treated this list as an open-ended sequence (strings in
practice). This structured form is allowed, and string entries still load.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Goal:
    goal_id: str
    description: str
    status: str = "active"
    code: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def normalize_goal(raw: Any, fallback_id: str | None = None) -> Goal:
    if isinstance(raw, str):
        return Goal(goal_id=fallback_id or raw, description=raw, status="active")
    if not isinstance(raw, dict):
        text = str(raw)
        return Goal(goal_id=fallback_id or text, description=text, status="active")
    goal_id = str(raw.get("goal_id") or raw.get("id") or fallback_id or uuid.uuid4().hex[:12])
    description = str(raw.get("description") or raw.get("content") or raw.get("goal") or "")
    extra = {
        key: value
        for key, value in raw.items()
        if key not in {"goal_id", "id", "description", "content", "goal", "status", "code"}
    }
    return Goal(
        goal_id=goal_id,
        description=description,
        status=str(raw.get("status") or "active"),
        code=raw.get("code"),
        extra=extra,
    )


def goal_to_state_record(goal: Goal) -> dict[str, Any]:
    record: dict[str, Any] = {
        "goal_id": goal.goal_id,
        "description": goal.description,
        "status": goal.status,
    }
    if goal.code is not None:
        record["code"] = goal.code
    record.update(goal.extra)
    return record
