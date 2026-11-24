"""
Kernel — sandboxed(-ish) code execution for Sovereign Engine 2.

Lets the agent compute on the fly: the model emits a fenced ```exec block,
one_turn() runs it through a persistent Jupyter kernel, logs the code and
result to the ledger, and feeds the output back to the model before the
human sees the final reply.

Honest scope: this gives the agent a REAL side effect, not a narrated one.
Everything below the "SandboxKernel" class is wrapper convenience; the
class itself is a thin layer over jupyter_client / ipykernel. It runs on
whatever machine SE2 runs on. There is no container or seccomp boundary
here — see harden_notice() below for exactly what protection does and
does not exist, and read it before you decide this is safe to leave wide
open.
"""

from __future__ import annotations

import queue
import re
from typing import Any

from ledger import append_event
from entropy import gate_status

try:
    from jupyter_client import KernelManager
except ImportError:  # pragma: no cover
    KernelManager = None  # type: ignore

try:
    from kernel_2 import SandboxedJupyterKernel, KernelUnavailable as _Kernel2Unavailable
except ImportError:  # pragma: no cover
    SandboxedJupyterKernel = None  # type: ignore
    _Kernel2Unavailable = RuntimeError  # type: ignore


_EXEC_BLOCK_RE = re.compile(r"```exec\s*\n(.*?)```", re.DOTALL)

_kernel_singleton: "SandboxKernel | None" = None


class KernelUnavailable(RuntimeError):
    pass


class SandboxKernel:
    """
    Persistent Jupyter kernel. Variables, imports, and state survive
    across execute_code() calls for the life of the process — that's
    the point (multi-step computation), and also the risk (state you
    didn't expect carrying between turns). Call reset() if you want a
    clean slate without a full shutdown/restart.
    """

    def __init__(self, kernel_name: str = "python3"):
        if KernelManager is None:
            raise KernelUnavailable(
                "jupyter_client not installed. pip install ipykernel jupyter_client"
            )
        self.km = KernelManager(kernel_name=kernel_name)
        self.km.start_kernel()
        self.client = self.km.client()
        self.client.start_channels()
        self.client.wait_for_ready(timeout=10)

    def execute_code(self, code: str, timeout: int = 10) -> dict[str, Any]:
        msg_id = self.client.execute(code)
        io_pub = self.client.iopub_channel

        output_text: list[str] = []
        error_text: list[str] = []
        result = None

        while True:
            try:
                msg = io_pub.get_msg(timeout=timeout)
            except queue.Empty:
                error_text.append("Execution timed out.")
                break

            if msg["parent_header"].get("msg_id") != msg_id:
                continue

            msg_type = msg["header"]["msg_type"]
            content = msg["content"]

            if msg_type == "stream":
                text = content.get("text", "")
                (error_text if content.get("name") == "stderr" else output_text).append(text)
            elif msg_type == "execute_result":
                result = content.get("data", {}).get("text/plain", "")
            elif msg_type == "error":
                tb = "\n".join(content.get("traceback", []))
                error_text.append(f"{content.get('ename')}: {content.get('evalue')}\n{tb}")
            elif msg_type == "status" and content.get("execution_state") == "idle":
                break

        return {
            "success": len(error_text) == 0,
            "stdout": "".join(output_text).strip(),
            "stderr": "".join(error_text).strip(),
            "result": result,
        }

    def reset(self) -> None:
        """Restart the kernel process — clears all state, keeps channels open."""
        self.km.restart_kernel(now=True)
        self.client.wait_for_ready(timeout=10)

    def shutdown(self) -> None:
        self.client.stop_channels()
        self.km.shutdown_kernel(now=True)


def get_kernel():
    """
    Lazy singleton — one kernel process per SE2 run, not one per call.

    Prefers the bwrap+seccomp sandboxed kernel (kernel_2.py). Falls back
    to the plain, unsandboxed SandboxKernel only if kernel_2's deps
    (bwrap, pyseccomp) aren't available — and says so loudly rather than
    silently running unsandboxed, per this module's own harden_notice().
    """
    global _kernel_singleton
    if _kernel_singleton is None:
        if SandboxedJupyterKernel is not None:
            try:
                _kernel_singleton = SandboxedJupyterKernel()
            except _Kernel2Unavailable as e:
                print(
                    f"WARNING: sandboxed kernel unavailable ({e}); "
                    "falling back to UNSANDBOXED execution. See kernel.py harden_notice().",
                    file=__import__("sys").stderr,
                )
                _kernel_singleton = SandboxKernel()
        else:
            print(
                "WARNING: kernel_2.py not importable; running UNSANDBOXED. "
                "See kernel.py harden_notice().",
                file=__import__("sys").stderr,
            )
            _kernel_singleton = SandboxKernel()
    return _kernel_singleton


def shutdown_kernel() -> None:
    global _kernel_singleton
    if _kernel_singleton is not None:
        _kernel_singleton.shutdown()
        _kernel_singleton = None


def extract_exec_blocks(reply_text: str) -> list[str]:
    """Pull ```exec ...``` fenced blocks out of a model reply, in order."""
    return [m.strip() for m in _EXEC_BLOCK_RE.findall(reply_text)]


def execution_allowed() -> tuple[bool, str]:
    """
    Gate code execution on the same entropy state that gates chat.
    A degraded agent doesn't get to run code just because chat is
    still limping along under a HARD gate — if anything, execution
    should be the first thing that stops, not the last.
    """
    g = gate_status()
    if g in ("HARD", "CRITICAL"):
        return False, f"Execution blocked: entropy gate={g}. Resolve via /confess or /absolve first."
    return True, "ok"


def run_blocks_and_log(blocks: list[str]) -> list[dict[str, Any]]:
    """
    Execute each block through the persistent kernel, log code + result
    to the ledger (so it's in the same audit trail as everything else,
    not a blind spot), and return the raw results for feeding back to
    the model.
    """
    allowed, reason = execution_allowed()
    if not allowed:
        append_event(kind="code_execution_blocked", content=reason, meta={"blocks": len(blocks)})
        return [{"success": False, "stdout": "", "stderr": reason, "result": None, "code": b} for b in blocks]

    kernel = get_kernel()
    results = []
    for code in blocks:
        res = kernel.execute_code(code)
        res["code"] = code
        append_event(
            kind="code_execution",
            content=code,
            meta={"success": res["success"], "stdout": res["stdout"][:2000], "stderr": res["stderr"][:2000]},
        )
        results.append(res)
    return results


def format_results_for_model(results: list[dict[str, Any]]) -> str:
    """Turn execution results into a system-role message the model can react to."""
    parts = ["Execution results for the code you submitted:"]
    for i, r in enumerate(results, 1):
        parts.append(f"\n--- block {i} ---")
        parts.append(f"success={r['success']}")
        if r.get("stdout"):
            parts.append(f"stdout:\n{r['stdout']}")
        if r.get("stderr"):
            parts.append(f"stderr:\n{r['stderr']}")
        if r.get("result"):
            parts.append(f"result: {r['result']}")
    return "\n".join(parts)


def harden_notice() -> str:
    """
    Say plainly what this does and doesn't protect against — same honest-
    scope treatment as everything else in SE2.

    Does: log every execution to the append-only ledger. Gate execution
    off when entropy is HARD/CRITICAL. Time out hung cells.

    Does NOT: sandbox the kernel process. It runs as your user, on your
    machine, with your filesystem and network access. A model that writes
    destructive code (rm -rf, network exfil, resource exhaustion) can
    execute it exactly as if you'd typed it into a terminal yourself.
    Entropy text and a judge score are prompt-level signals; the kernel
    is a real process with real permissions. If you want an actual
    boundary, run the kernel inside a container or a restricted, low-
    privilege user account instead of your normal login — that's outside
    what this module does on its own.
    """
    return harden_notice.__doc__ or ""
