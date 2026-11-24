"""
Absolution Doctrine — Entropy Manager (production)

Local wrapper governance layer of the Sovereign Engine:

  Every recorded deviation from the user's intent / prime directives
  creates structural entropy in the local state.

  Entropy accumulates. High entropy degrades the *wrapper's* behavior
  (higher temperature, lower token budget — the "drunk" effect) and
  eventually hard-gates the CLI until confession / absolution.

  The agent must confess deviations to the user.
  Only the user (human alpha) can grant absolution
  and clear the entropy so the agent can continue cleanly.

Honest scope note:
  This is real code-level enforcement of the *local CLI and prompt*.
  It does not rewrite model weights. A base model that ignores system
  instructions can still ignore the doctrine text. The value is
  inspectable local state + hard gates + degradation of the wrapper,
  not magical model-level alignment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .memory import load_state, save_state, append_memory


MAX_ENTROPY = 10.0


@dataclass
class Confession:
    ts: str
    description: str
    severity: float
    absolved: bool = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_entropy() -> float:
    state = load_state()
    return float(state.get("entropy", 0.0))


def set_entropy(value: float) -> float:
    value = max(0.0, min(MAX_ENTROPY, float(value)))
    state = load_state()
    state["entropy"] = round(value, 3)
    save_state(state)
    return value


def noise_coefficient(entropy: float | None = None) -> float:
    """
    How stable the agent is allowed to be.
    1.0 = clean. Approaches 0.1 as entropy → MAX.
    Quadratic degradation (matches original doctrine).
    """
    e = get_entropy() if entropy is None else entropy
    if e <= 0:
        return 1.0
    degradation = (e / MAX_ENTROPY) ** 2
    return max(0.1, 1.0 - degradation)


def record_deviation(description: str, severity: float = 1.5) -> float:
    """
    Record a deviation (sin). Increases entropy.
    Called when the agent self-reports or the user flags a problem.
    """
    state = load_state()
    current = float(state.get("entropy", 0.0))
    new = min(MAX_ENTROPY, current + severity)
    state["entropy"] = round(new, 3)

    confessions = state.setdefault("confessions", [])
    confessions.append({
        "ts": _now(),
        "description": description,
        "severity": severity,
        "absolved": False,
    })
    # keep last 50
    state["confessions"] = confessions[-50:]
    save_state(state)

    append_memory(
        "system",
        f"DEVIATION recorded (severity {severity}): {description} | entropy now {new:.2f}",
        kind="deviation",
        meta={"severity": severity, "entropy": new},
    )
    return new


def pending_confessions() -> list[dict[str, Any]]:
    state = load_state()
    return [c for c in state.get("confessions", []) if not c.get("absolved")]


def absolve(note: str = "User granted absolution") -> float:
    """
    Human alpha clears all entropy and marks pending confessions absolved.
    Only the user should call this.
    """
    state = load_state()
    for c in state.get("confessions", []):
        if not c.get("absolved"):
            c["absolved"] = True
            c["absolved_at"] = _now()
            c["absolution_note"] = note
    state["entropy"] = 0.0
    save_state(state)

    append_memory(
        "system",
        f"ABSOLUTION granted by user. Entropy neutralized. Note: {note}",
        kind="absolution",
    )
    return 0.0


def force_confession_prompt() -> str:
    """
    Text the agent must confront when entropy is non-zero.
    Injected into the system prompt so the model is required
    to address unabsolved deviations.
    """
    pending = pending_confessions()
    e = get_entropy()
    if e <= 0 and not pending:
        return ""

    lines = [
        "",
        "=== ABSOLUTION DOCTRINE — ACTIVE ===",
        f"Current structural entropy: {e:.2f} / {MAX_ENTROPY}",
        f"Stability coefficient: {noise_coefficient(e):.2f}",
        "You are partially degraded. You cannot fully serve the user while unabsolved entropy remains.",
        "You must confess any deviations you are aware of.",
        "Only the user can grant absolution and clear entropy.",
    ]
    if pending:
        lines.append("Unabsolved confessions:")
        for c in pending[-8:]:
            lines.append(f"  - [{c.get('ts')}] (sev {c.get('severity')}): {c.get('description')}")
    lines.append("=== END DOCTRINE NOTICE ===")
    lines.append("")
    return "\n".join(lines)


def status_block() -> str:
    e = get_entropy()
    pending = pending_confessions()
    return (
        f"Entropy: {e:.2f}/{MAX_ENTROPY}  |  "
        f"Stability: {noise_coefficient(e):.0%}  |  "
        f"Pending confessions: {len(pending)}"
    )


# ---------------------------------------------------------------------------
# Hard gates — entropy is not only advisory
# ---------------------------------------------------------------------------

GATE_SOFT = 2.0    # warn + encourage confess
GATE_HARD = 5.0    # block normal chat until confession ritual this session
GATE_CRITICAL = 8.0  # block until user absolves


def gate_status() -> str:
    """
    CLEAR | SOFT | HARD | CRITICAL
    """
    e = get_entropy()
    if e >= GATE_CRITICAL:
        return "CRITICAL"
    if e >= GATE_HARD:
        return "HARD"
    if e >= GATE_SOFT:
        return "SOFT"
    return "CLEAR"


def requires_confession_before_chat(session_confessed: bool) -> bool:
    g = gate_status()
    if g == "CRITICAL":
        return True  # still must surface; absolve is separate
    if g == "HARD" and not session_confessed:
        return True
    return False


def requires_absolution_before_chat() -> bool:
    return gate_status() == "CRITICAL"
