"""Test-only file-handoff repair sampler.

Not the production default. Production still uses Ollama/Qwythos via
sovereign.repair.default_repair_sampler. This module lives under demos/
so a human (or another agent) can stand in as the model: when repair()
is called it writes a request JSON and blocks until a matching response
file appears, or until the timeout.

Request:  {handoff_dir}/{id}.request.json
Response: {handoff_dir}/{id}.response.json
          {"code": "<complete revised source>"}

The sampler returns a complete program (applies_full_source = True).
The REPL engine uses that opt-in only when the flag is set; region
repair remains the default for Ollama.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sovereign.repl.guidance import RepairRequest


def _complex_list(state) -> list[list[float]] | None:
    if state is None:
        return None
    try:
        return [[float(z.real), float(z.imag)] for z in state]
    except TypeError:
        return None


class HandoffRepairSampler:
    """Write a repair request to disk and wait for a human/agent response."""

    applies_full_source = True

    def __init__(
        self,
        handoff_dir: str | Path,
        *,
        timeout: float = 300.0,
        poll_interval: float = 0.2,
    ) -> None:
        self.handoff_dir = Path(handoff_dir)
        self.handoff_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.seq = 0
        self.goal_id: str | None = None
        self.goal_description: str | None = None

    def set_goal(self, goal_id: str, description: str | None = None) -> None:
        self.goal_id = goal_id
        self.goal_description = description

    def repair(self, request: RepairRequest) -> str:
        self.seq += 1
        req_id = f"{self.seq:04d}"
        payload: dict[str, Any] = {
            "id": req_id,
            "goal_id": self.goal_id,
            "goal": self.goal_description,
            "code": request.code,
            "failing_region": request.failure.failing_region,
            "error_type": request.failure.exception_type,
            "message": request.failure.message,
            "traceback": list(request.failure.traceback),
            "quantum_entropy": float(request.entropy),
            "quantum_state": _complex_list(request.guidance),
            "attempt": self.seq,
            "region_start": request.failure.region_start,
            "region_end": request.failure.region_end,
            "context_before": request.failure.context_before,
            "context_after": request.failure.context_after,
        }
        req_path = self.handoff_dir / f"{req_id}.request.json"
        tmp = req_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(req_path)
        resp_path = self.handoff_dir / f"{req_id}.response.json"
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if resp_path.exists():
                raw = resp_path.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, str):
                    code = data
                elif isinstance(data, dict):
                    code = data.get("code") or data.get("replacement") or ""
                else:
                    raise TypeError(f"unexpected response payload in {resp_path}")
                if not str(code).strip():
                    raise ValueError(f"empty revised code in {resp_path}")
                return str(code)
            time.sleep(self.poll_interval)
        raise TimeoutError(
            f"timed out after {self.timeout}s waiting for {resp_path} "
            f"(goal={self.goal_id!r} error={payload['error_type']})"
        )
