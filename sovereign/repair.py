"""REPL feedback / repair loop, recording quantum entropy as ledger metadata.

Governance entropy (state['entropy']) is never written here.
"""

from __future__ import annotations

import logging
import socket
import sys
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from .kernel import ExecutionResult, KernelSession
from .ledger import append_event, verify_chain
from .llm import LLMConfig, LLMError, chat, config_from_env, repair_config
from .memory import append_memory
from .repl.canvas import ContinuousCanvas
from .repl.engine import REPLFeedbackLoop
from .repl.errors import format_execution_error
from .repl.guidance import ErrorEntropyGuidance, RepairRequest
from .repl.quantum import QuantumTwinEvaluator
from .repl.samplers import DiscreteDiffusionSampler


class MappingRepairSampler:
    """Deterministic region repairs keyed by failing statement or full source."""

    def __init__(self, repairs: dict[str, str] | None = None) -> None:
        self.repairs = dict(repairs or {})

    def repair(self, request: RepairRequest) -> str:
        region = request.failure.failing_region
        if region in self.repairs:
            return self.repairs[region]
        if request.code in self.repairs:
            return self.repairs[request.code]
        return region


_LOG = logging.getLogger("sovereign.repair")


class LLMRepairSampler:
    """Ask llm.chat (Ollama by default) to replace a failing statement.

    The offline MappingRepairSampler is used only when the endpoint is
    unreachable or the call fails, and that fallback is logged clearly.
    """

    def __init__(
        self,
        cfg: LLMConfig | None = None,
        fallback: MappingRepairSampler | None = None,
    ) -> None:
        self.cfg = repair_config(cfg)
        self.fallback = fallback or MappingRepairSampler()

    def _fallback(self, request: RepairRequest, reason: str) -> str:
        msg = (
            "Ollama repair sampler unavailable "
            f"({reason}); falling back to the offline sampler. "
            f"Tried model={self.cfg.model!r} at {self.cfg.base_url!r}."
        )
        _LOG.warning(msg)
        print(msg, file=sys.stderr)
        return self.fallback.repair(request)

    def repair(self, request: RepairRequest) -> str:
        if not llm_reachable(self.cfg):
            return self._fallback(
                request,
                f"could not reach {self.cfg.base_url} (is Ollama running?)",
            )
        prompt = (
            "You are repairing a single failing Python statement in a REPL.\n"
            "Return ONLY the replacement statement, no markdown fences, no commentary.\n"
            f"Exception: {request.failure.exception_type}: {request.failure.message}\n"
            f"Failing statement:\n{request.failure.failing_region}\n"
            f"Code before:\n{request.failure.context_before}\n"
            f"Code after:\n{request.failure.context_after}\n"
        )
        try:
            text = chat(
                [
                    {"role": "system", "content": "Return only valid Python for the failing statement."},
                    {"role": "user", "content": prompt},
                ],
                self.cfg,
            )
        except LLMError as exc:
            return self._fallback(request, str(exc))
        stripped = _strip_fences(text)
        if not stripped:
            return self._fallback(request, "model returned an empty repair")
        return stripped


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def llm_reachable(cfg: LLMConfig, timeout: float = 2.0) -> bool:
    """Cheap TCP probe so offline runs do not wait on a 120s HTTP timeout."""
    parsed = urlparse(cfg.base_url if "://" in cfg.base_url else "http://" + cfg.base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def default_repair_sampler(cfg: LLMConfig | None = None) -> LLMRepairSampler:
    """Default sampler is Ollama-backed via llm.chat; offline is fallback-only."""
    return LLMRepairSampler(repair_config(cfg), fallback=MappingRepairSampler())


def _complex_list(state) -> list[list[float]]:
    return [[float(z.real), float(z.imag)] for z in state]


@dataclass
class AttemptRecord:
    attempt: int
    code: str
    status: str
    stdout: str
    stderr: str
    error_name: str | None = None
    quantum_entropy: float | None = None
    quantum_state: list[list[float]] | None = None
    revised_to: str | None = None


class _RecordingKernel:
    def __init__(self, kernel, records: list[AttemptRecord], guidance: ErrorEntropyGuidance) -> None:
        self.kernel = kernel
        self.records = records
        self.guidance = guidance

    async def execute(self, code: str, *, timeout: float = 60.0) -> ExecutionResult:
        execution = await self.kernel.execute(code, timeout=timeout)
        record = AttemptRecord(
            attempt=len(self.records) + 1,
            code=code,
            status=execution.status,
            stdout=execution.stdout,
            stderr=execution.stderr,
        )
        if execution.status != "ok":
            from .repl.errors import extract_error_context

            failure = extract_error_context(code, execution.error, execution.stderr)
            encoded = self.guidance.from_error(failure)
            if isinstance(execution.error, dict):
                record.error_name = str(execution.error.get("ename") or "") or None
            record.quantum_entropy = float(encoded.entropy)
            record.quantum_state = _complex_list(encoded.state)
        self.records.append(record)
        return execution


class _RecordingGuideSampler:
    def __init__(self, sampler: Any, records: list[AttemptRecord]) -> None:
        self.sampler = sampler
        self.records = records
        # Opt-in full-program replacements must survive this wrapper.
        self.applies_full_source = bool(getattr(sampler, "applies_full_source", False))

    def guide(self, canvas: str, guidance, entropy: float) -> str:
        replacement = self.sampler.guide(canvas, guidance, entropy)
        if self.records:
            self.records[-1].revised_to = replacement
            if self.records[-1].quantum_entropy is None:
                self.records[-1].quantum_entropy = float(entropy)
                self.records[-1].quantum_state = _complex_list(guidance)
        return replacement


class _RecordingRepairSampler(_RecordingGuideSampler):
    def repair(self, request: RepairRequest) -> Any:
        replacement = self.sampler.repair(request)
        if self.records:
            self.records[-1].revised_to = replacement if isinstance(replacement, str) else None
        return replacement


def _wrap_sampler(sampler: Any, records: list[AttemptRecord]) -> Any:
    if hasattr(sampler, "repair"):
        return _RecordingRepairSampler(sampler, records)
    if hasattr(sampler, "guide"):
        return _RecordingGuideSampler(sampler, records)
    return _RecordingRepairSampler(MappingRepairSampler(), records)


def _persist_attempts(records: list[AttemptRecord], *, goal_id: str | None = None) -> None:
    for record in records:
        if record.status == "ok":
            continue
        meta: dict[str, Any] = {
            "attempt": record.attempt,
            "success": False,
            "error_name": record.error_name,
            "quantum_entropy": record.quantum_entropy,
            "quantum_state": record.quantum_state,
        }
        if goal_id:
            meta["goal_id"] = goal_id
        append_memory(
            "system",
            (
                f"Execution failed (attempt {record.attempt}).\n"
                f"code:\n{record.code}\n"
                f"error={record.error_name} quantum_entropy={record.quantum_entropy}"
            ),
            kind="code_execution",
            meta=meta,
        )
        if record.revised_to:
            append_memory(
                "system",
                f"Sampler revised attempt {record.attempt} to:\n{record.revised_to}",
                kind="code_revision",
                meta={
                    "attempt": record.attempt,
                    "quantum_entropy": record.quantum_entropy,
                    "revised_to": record.revised_to,
                    **({"goal_id": goal_id} if goal_id else {}),
                },
            )


@dataclass
class RepairLoopResult:
    records: list[AttemptRecord] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)


async def run_repair_loop(
    code: str,
    sampler: Any,
    *,
    kernel: KernelSession,
    max_attempts: int = 3,
    timeout: float = 60.0,
    goal_id: str | None = None,
    canvas: ContinuousCanvas | None = None,
) -> dict[str, Any]:
    """Run the feedback loop and seal attempts into memory/ledger."""
    records: list[AttemptRecord] = []
    guidance = ErrorEntropyGuidance(QuantumTwinEvaluator())
    loop = REPLFeedbackLoop(
        _RecordingKernel(kernel, records, guidance),
        _wrap_sampler(sampler if sampler is not None else DiscreteDiffusionSampler(), records),
        canvas=canvas or ContinuousCanvas(),
        guidance=guidance,
    )
    feedback = await loop.run(code, max_attempts=max_attempts, timeout=timeout)
    _persist_attempts(records, goal_id=goal_id)

    last = records[-1] if records else None
    if feedback.execution.status == "ok":
        append_memory(
            "system",
            (
                f"REPL execution succeeded after {feedback.attempts} attempt(s). "
                f"corrected={feedback.corrected}\nfinal code:\n{feedback.code}\n"
                f"stdout:\n{feedback.execution.stdout}"
            ),
            kind="code_execution",
            meta={
                "success": True,
                "attempts": feedback.attempts,
                "corrected": feedback.corrected,
                "stdout": feedback.execution.stdout[:2000],
                **({"goal_id": goal_id} if goal_id else {}),
            },
        )
    else:
        append_memory(
            "system",
            f"REPL execution failed after {feedback.attempts} attempt(s).",
            kind="code_execution",
            meta={
                "success": False,
                "attempts": feedback.attempts,
                "quantum_entropy": None if last is None else last.quantum_entropy,
                **({"goal_id": goal_id} if goal_id else {}),
            },
        )

    result_text = None
    for output in feedback.execution.outputs:
        if output.get("output_type") == "execute_result":
            result_text = output.get("data", {}).get("text/plain", "")
    return {
        "success": feedback.execution.status == "ok",
        "stdout": (feedback.execution.stdout or "").strip(),
        "stderr": (feedback.execution.stderr or "").strip(),
        "result": result_text,
        "code": feedback.code,
        "corrected": feedback.corrected,
        "attempts": feedback.attempts,
        "records": records,
        "execution": feedback.execution,
    }


class SovereignREPLBridge:
    """Goal-driven entry point around run_repair_loop."""

    def __init__(self, kernel: KernelSession, sampler: Any, canvas: ContinuousCanvas | None = None) -> None:
        self.kernel = kernel
        self.sampler = sampler
        self.canvas = canvas or ContinuousCanvas()

    def inject_goal(self, goal, code: str) -> str:
        from .goals import Goal

        if not isinstance(goal, Goal):
            from .goals import normalize_goal
            goal = normalize_goal(goal)
        self.canvas.append(
            f"[SOVEREIGN GOAL {goal.goal_id} status={goal.status}]\n"
            f"{goal.description}\n[CANDIDATE CODE]\n{code}\n"
        )
        return self.canvas.text

    async def run_goal(self, goal, *, code: str | None = None, max_attempts: int = 3, timeout: float = 60.0):
        from .memory import get_goal, update_goal

        if isinstance(goal, str):
            resolved = get_goal(goal)
            if resolved is None:
                raise KeyError(f"unknown goal_id: {goal}")
            goal = resolved
        candidate = code if code is not None else goal.code
        if not candidate:
            raise ValueError("goal has no candidate code; pass code=...")
        self.inject_goal(goal, candidate)
        append_memory(
            "goal",
            f"REPL run started for goal {goal.goal_id}: {goal.description}",
            kind="goal",
            meta={"goal_id": goal.goal_id, "code": candidate},
        )
        payload = await run_repair_loop(
            candidate,
            self.sampler,
            kernel=self.kernel,
            max_attempts=max_attempts,
            timeout=timeout,
            goal_id=goal.goal_id,
            canvas=self.canvas,
        )
        try:
            update_goal(goal.goal_id, status="completed" if payload["success"] else "failed")
            goal.status = "completed" if payload["success"] else "failed"
        except KeyError:
            pass
        ok, message = verify_chain()
        payload["goal"] = goal
        payload["chain_ok"] = ok
        payload["chain_message"] = message
        payload["canvas_text"] = self.canvas.text
        return payload
