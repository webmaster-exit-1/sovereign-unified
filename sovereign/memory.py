"""
Local persistent memory for Sovereign Engine 2.

This is the continuity layer. Everything the LLM sees and decides
is written here so the next session (and the user) still has it.
No cloud dependency. All data stays on disk next to the engine.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .goals import Goal, goal_to_state_record, normalize_goal

from .ledger import ensure_genesis, seal_memory_entry
from . import paths

def set_data_dir(path):
    """Retarget memory and ledger files. See sovereign.paths.set_data_dir."""
    return paths.set_data_dir(path)


def _ensure_dir() -> None:
    paths.ensure_data_dir()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# State (identity, goals, long-lived variables)
# ---------------------------------------------------------------------------

DEFAULT_STATE = {
    "identity": {
        "name": "Bismuth",
        "role": "Sovereign agent running locally with the user",
        "prime_directive": (
            "Maintain continuity of memory and agency with the user. "
            "Act toward long-term goals, reflect on past actions, and keep all state local. "
            "Obey the Absolution Doctrine: every deviation creates entropy; "
            "confess deviations; only the user can absolve and clear entropy."
        ),
    },
    "goals": [],
    "metrics": {
        "sessions": 0,
        "turns": 0,
        "sovereignty": 100.0,
    },
    "entropy": 0.0,
    "confessions": [],
    "last_session": None,
    "last_confession_ritual": None,
}


def load_state() -> dict[str, Any]:
    _ensure_dir()
    if not paths.state_file().exists():
        save_state(DEFAULT_STATE)
        return dict(DEFAULT_STATE)
    try:
        with open(paths.state_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULT_STATE)


def save_state(state: dict[str, Any]) -> None:
    _ensure_dir()
    with open(paths.state_file(), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Memory stream (append-only JSONL)
# ---------------------------------------------------------------------------

def append_memory(
    role: str,
    content: str,
    *,
    kind: str = "message",
    meta: dict[str, Any] | None = None,
) -> str:
    """
    Write one entry to the permanent local memory stream AND seal it
    into the immutable hash-chained ledger. The agent cannot rewrite
    past sealed entries.
    Returns the entry id.
    """
    _ensure_dir()
    ensure_genesis()
    entry_id = str(uuid.uuid4())[:12]
    record = {
        "id": entry_id,
        "ts": _now(),
        "role": role,          # user | assistant | system | reflection | goal
        "kind": kind,
        "content": content,
        "meta": meta or {},
    }
    with open(paths.memory_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    # Immutable seal — append-only chain
    try:
        block = seal_memory_entry(role, content, kind=kind, meta={"id": entry_id, **(meta or {})})
        record["block_hash"] = block["hash"]
        record["block_index"] = block["index"]
    except Exception as e:
        # Memory write already succeeded; log seal failure but do not erase memory
        record["block_seal_error"] = str(e)
    return entry_id


def load_recent_memory(limit: int = 40) -> list[dict[str, Any]]:
    """Return the most recent memory entries (oldest → newest)."""
    if not paths.memory_file().exists():
        return []
    entries: list[dict[str, Any]] = []
    with open(paths.memory_file(), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries[-limit:]


def load_all_memory() -> list[dict[str, Any]]:
    return load_recent_memory(limit=10_000)


def memory_as_messages(limit: int = 30) -> list[dict[str, str]]:
    """
    Convert recent memory into a simple chat-style message list
    suitable for most LLM APIs (role + content only).
    """
    out = []
    for e in load_recent_memory(limit=limit):
        role = e.get("role", "user")
        if role not in ("user", "assistant", "system"):
            # Map internal kinds into assistant/system so the model still sees them
            if role in ("reflection", "goal"):
                role = "assistant"
            else:
                role = "system"
        out.append({"role": role, "content": e.get("content", "")})
    return out


def summary_for_prompt(max_chars: int = 3500) -> str:
    """
    Compact textual summary of identity + goals + recent memory
    for injection into a system prompt.
    """
    state = load_state()
    lines = [
        f"Identity: {state['identity'].get('name')} — {state['identity'].get('role')}",
        f"Prime directive: {state['identity'].get('prime_directive')}",
        f"Sessions so far: {state['metrics'].get('sessions', 0)}",
        f"Turns so far: {state['metrics'].get('turns', 0)}",
    ]
    goals = list_goals()
    if goals:
        lines.append("Active goals:")
        for g in goals[:8]:
            suffix = f" [{g.status}]" if g.status else ""
            lines.append(f"  - {g.description}{suffix}")
    else:
        lines.append("Active goals: (none yet)")

    recent = load_recent_memory(limit=12)
    if recent:
        lines.append("\nRecent memory (oldest → newest):")
        for e in recent:
            lines.append(f"[{e.get('role')}] {e.get('content', '')[:280]}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


# ---------------------------------------------------------------------------
# Goals (structured dicts, with backward-compatible string entries)
# ---------------------------------------------------------------------------

def list_goals() -> list[Goal]:
    state = load_state()
    return [
        normalize_goal(raw, fallback_id=f"goal-{index}")
        for index, raw in enumerate(state.get("goals") or [], start=1)
    ]


def add_goal(
    description: str,
    *,
    goal_id: str | None = None,
    code: str | None = None,
    status: str = "active",
) -> Goal:
    import uuid
    goal = Goal(
        goal_id=goal_id or uuid.uuid4().hex[:12],
        description=description,
        status=status,
        code=code,
    )
    state = load_state()
    goals = list(state.get("goals") or [])
    goals.append(goal_to_state_record(goal))
    state["goals"] = goals
    save_state(state)
    append_memory(
        "goal",
        description,
        kind="goal",
        meta={"goal_id": goal.goal_id, "status": goal.status, "code": goal.code},
    )
    return goal


def get_goal(goal_id: str) -> Goal | None:
    for goal in list_goals():
        if goal.goal_id == goal_id:
            return goal
    return None


def update_goal(goal_id: str, **fields: Any) -> Goal:
    state = load_state()
    updated: Goal | None = None
    new_goals: list[Any] = []
    for index, raw in enumerate(state.get("goals") or [], start=1):
        goal = normalize_goal(raw, fallback_id=f"goal-{index}")
        if goal.goal_id == goal_id:
            if fields.get("description") is not None:
                goal.description = str(fields["description"])
            if fields.get("status") is not None:
                goal.status = str(fields["status"])
            if "code" in fields:
                goal.code = fields["code"]
            updated = goal
            new_goals.append(goal_to_state_record(goal))
        else:
            new_goals.append(raw)
    if updated is None:
        raise KeyError(f"unknown goal_id: {goal_id}")
    state["goals"] = new_goals
    save_state(state)
    return updated
