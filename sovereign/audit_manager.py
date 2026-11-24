"""
Audit Manager — compatibility shim.

SE2's single logging path is ledger.py (chain.jsonl + ledger.csv).
This module remains only so legacy sovereign_engine.py imports do not break.
All durable events should go through memory.append_memory → ledger.seal_memory_entry.
"""

from __future__ import annotations

from .ledger import (
    append_event,
    chain_height,
    ensure_genesis,
    verify_chain,
)


class AuditManager:
    """Thin facade over the unified ledger. Prefer ledger.py / memory.py directly."""

    @staticmethod
    def ensure_data_dir():
        ensure_genesis()

    @staticmethod
    def save_state(sovereignty: float = 0.0, energy: float = 0.0):
        """Legacy numeric exhalation → sealed as a ledger event (no separate CSV)."""
        append_event(
            kind="metrics",
            content=f"sovereignty={sovereignty:.4f} energy={energy:.4f}",
            meta={"sovereignty": sovereignty, "energy": energy},
        )

    @staticmethod
    def get_last_total() -> float:
        """Best-effort: no separate numeric CSV anymore; return baseline."""
        return 100.0

    @staticmethod
    def get_entry_count() -> int:
        return max(0, chain_height() - 1)  # exclude genesis

    @staticmethod
    def check_environmental_safety() -> str:
        """Optional Termux watchdog — safe if sensors absent."""
        try:
            import json
            import subprocess
            res = subprocess.check_output(
                ["termux-battery-status"], stderr=subprocess.DEVNULL, timeout=2
            )
            info = json.loads(res)
            battery = info.get("percentage", 100)
            temp = info.get("temperature", 25.0)
            if battery < 15:
                return "HIBERNATE"
            if temp > 45.0:
                return "THROTTLE"
        except Exception:
            pass
        return "SAFE"

    @staticmethod
    def seal_sovereign_block() -> str | None:
        """Legacy name: verify chain integrity; return tip hash if OK."""
        ok, msg = verify_chain()
        if not ok:
            print(f"[ledger] verify failed: {msg}")
            return None
        from ledger import recent_blocks
        blocks = recent_blocks(1)
        return blocks[-1]["hash"] if blocks else None
