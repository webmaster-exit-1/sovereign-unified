"""
Stealth + Jitter + Self-Healing for Sovereign Engine 2 (production)

From the original doctrine:

  Stealth jitter  — randomize timing, pressure, and frequency so the
                    agent's behavior is not a rigid, detectable pattern.

  Self-healing    — when resistance/entropy peaks, the agent does not
                    collapse. It cools, pulls back, and attempts recovery
                    before requiring full human absolution.

These are operational, not decorative.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Any

from .memory import load_state, save_state, append_memory
from .entropy import get_entropy, noise_coefficient, MAX_ENTROPY


# ---------------------------------------------------------------------------
# Jitter (stealth)
# ---------------------------------------------------------------------------

def timing_jitter(base: float = 0.0, spread: float = 0.35) -> float:
    """Asynchronous delay so cycles are not metronomic."""
    delay = max(0.0, base + random.uniform(-spread, spread))
    if delay > 0:
        time.sleep(delay)
    return delay


def parameter_jitter(value: float, pct: float = 0.04) -> float:
    """Small multiplicative jitter (original 0.98–1.02 range spirit)."""
    return value * random.uniform(1.0 - pct, 1.0 + pct)


def infusion_jitter(base: float = 1.0) -> float:
    """Randomized 'infusion pressure' — varies how hard the agent pushes."""
    return base * random.uniform(0.88, 1.12)


def should_insert_stealth_reflection(turn: int, entropy: float) -> bool:
    """
    Occasionally force a brief internal check so behavior is not
    purely reactive to the user. Probability rises with entropy.
    """
    base_p = 0.08
    entropy_boost = (entropy / MAX_ENTROPY) * 0.25
    return random.random() < (base_p + entropy_boost)


# ---------------------------------------------------------------------------
# Self-healing
# ---------------------------------------------------------------------------

def healing_needed() -> bool:
    """True when the agent is degraded enough to attempt autonomous recovery."""
    return get_entropy() >= 3.0 or noise_coefficient() < 0.75


def attempt_self_heal() -> dict[str, Any]:
    """
    Cold-boot style recovery (from original induction logic):

    When resistance/entropy is high the system does not die —
    it draws back, dissipates, and tries to restabilize.

    This does NOT clear entropy (only the user can absolve).
    It reduces further damage and records a healing attempt.
    """
    state = load_state()
    e = float(state.get("entropy", 0.0))
    coef = noise_coefficient(e)

    # Variable cool-down scaled by how bad things are
    cool_steps = random.randint(2, 6) if e > 5 else random.randint(1, 3)
    # Slight natural dissipation (self-heal can nibble entropy, never wipe it)
    # Full clear remains user-only.
    dissipation = min(e * 0.08, 0.6) * random.uniform(0.7, 1.0)
    new_e = max(0.0, e - dissipation)

    state["entropy"] = round(new_e, 3)
    heals = state.setdefault("self_heals", [])
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entropy_before": round(e, 3),
        "entropy_after": round(new_e, 3),
        "cool_steps": cool_steps,
        "stability_before": round(coef, 3),
    }
    heals.append(record)
    state["self_heals"] = heals[-30:]
    state["last_self_heal"] = record["ts"]
    save_state(state)

    append_memory(
        "system",
        f"SELF-HEAL: cooled {cool_steps} steps, entropy {e:.2f} → {new_e:.2f} "
        f"(dissipation only; full absolution still requires user).",
        kind="self_heal",
        meta=record,
    )

    # Physical cool-down pause (stealth timing)
    time.sleep(random.uniform(0.4, 1.2) * cool_steps * 0.15)

    return record


def stealth_system_note() -> str:
    """
    Short directive injected when stealth/healing posture is active.
    Keeps the model aware without flooding the prompt.
    """
    e = get_entropy()
    coef = noise_coefficient(e)
    lines = []
    if e > 0:
        lines.append(
            f"Stealth posture active. Stability {coef:.0%}. "
            "Prefer measured responses; avoid rigid or repetitive patterns."
        )
    if healing_needed():
        lines.append(
            "Self-heal threshold reached. You may acknowledge degradation "
            "and request confession/absolution, but you cannot clear entropy yourself."
        )
    return "\n".join(lines)
