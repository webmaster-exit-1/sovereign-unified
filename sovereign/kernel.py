"""Unified kernel: KernelSession API with SE2 kernel_2 sandbox features.

This is the only execution path for `agent` exec blocks and the REPL
feedback loop.

Sandbox (when use_bubblewrap=True, the default for get_kernel()):
  - bwrap: host root read-only, workspace writable, --tmpfs /tmp
  - PID/UTS/IPC namespaces, cgroup if the kernel supports it
  - network namespace off by default (--unshare-net)
  - official SE2 rlimits (1 GB AS, 30s CPU, 256 files, 64 processes)
  - optional seccomp deny-list when pyseccomp is installed
  - Jupyter IPC sockets *inside* the workspace (fixes the official
    kernel_2 bug where `ip=scratch` placed sockets beside the bind mount)

Without pyseccomp the kernel still starts; seccomp is skipped and a
warning is recorded on the profile. Without bwrap, prepare() raises
unless use_bubblewrap=False (trusted/dev only).

rlimit note: RLIMIT_CPU is applied to the kernel *process*, not reset
per cell, despite the original "per cell" comment. A long-lived
persistent kernel can hit the 30s CPU cap. Limits are not loosened.
"""

from __future__ import annotations

import asyncio
import errno
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .entropy import gate_status
from .ledger import append_event

try:
    from jupyter_client import AsyncKernelManager
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Jupyter kernel support requires jupyter-client and ipykernel"
    ) from exc

try:
    import pyseccomp as seccomp
except ImportError:  # pragma: no cover
    seccomp = None  # type: ignore


class KernelUnavailable(RuntimeError):
    pass


_DENIED_SYSCALLS = [
    "unshare", "setns", "mount", "umount2", "pivot_root", "chroot",
    "init_module", "finit_module", "delete_module",
    "kexec_load", "kexec_file_load",
    "ptrace", "process_vm_readv", "process_vm_writev",
    "add_key", "request_key", "keyctl", "bpf", "perf_event_open",
    "reboot", "settimeofday", "clock_settime", "clock_adjtime", "adjtimex",
    "sethostname", "setdomainname", "swapon", "swapoff",
    "iopl", "ioperm",
    "acct", "personality", "quotactl", "nfsservctl",
]

_EXEC_BLOCK_RE = re.compile(r"```exec\s*\n(.*?)```", re.DOTALL)


def build_seccomp_bpf(path: Path) -> None:
    """Compile the official deny-list; tolerate either export_bpf API."""
    if seccomp is None:
        raise KernelUnavailable("pyseccomp not installed")
    filt = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    for name in _DENIED_SYSCALLS:
        try:
            filt.add_rule(seccomp.ERRNO(errno.EPERM), name)
        except Exception:
            pass
    with open(path, "wb") as handle:
        try:
            filt.export_bpf(handle)
        except TypeError:
            filt.export_bpf(handle.fileno())


@dataclass
class SandboxProfile:
    """Writable workspace plus optional bwrap / seccomp / network isolation."""

    workspace: Path | str | None = None
    use_bubblewrap: bool = True
    use_seccomp: bool = True
    allow_network: bool = False
    seccomp_bpf: Path | None = field(default=None, init=False)
    seccomp_enabled: bool = field(default=False, init=False)
    _owned_workspace: bool = field(default=False, init=False, repr=False)

    def prepare(self) -> Path:
        if self.workspace is None:
            self.workspace = Path(tempfile.mkdtemp(prefix="sovereign-kernel-"))
            self._owned_workspace = True
        else:
            self.workspace = Path(self.workspace).expanduser().resolve()
            self.workspace.mkdir(parents=True, exist_ok=True)
        workspace = Path(self.workspace)
        (workspace / "kernel").mkdir(parents=True, exist_ok=True)
        if self.use_bubblewrap and shutil.which("bwrap") is None:
            self.cleanup()
            raise RuntimeError(
                "bubblewrap is required for filesystem sandboxing; install bwrap "
                "or explicitly opt out with use_bubblewrap=False"
            )
        if self.use_bubblewrap and self.use_seccomp:
            if seccomp is None:
                warnings.warn(
                    "pyseccomp is not installed; starting the kernel without a "
                    "seccomp filter. Install the 'seccomp' extra to enable it.",
                    stacklevel=2,
                )
                self.seccomp_enabled = False
            else:
                bpf = workspace / "seccomp.bpf"
                build_seccomp_bpf(bpf)
                self.seccomp_bpf = bpf
                self.seccomp_enabled = True
        return workspace

    def wrap_command(self, command: list[str], connection_file: str) -> list[str]:
        if not self.use_bubblewrap:
            return command
        workspace = Path(self.workspace).resolve()
        # Invoke the launcher by file path so the jupyter child does not
        # need PYTHONPATH / an editable install to find the package.
        launcher_path = str(Path(__file__).resolve().parent / "sandbox_launch.py")
        launcher = [
            sys.executable,
            launcher_path,
            "--workspace",
            str(workspace),
            "--allow-network",
            "1" if self.allow_network else "0",
        ]
        if self.seccomp_bpf is not None:
            launcher += ["--seccomp-bpf", str(self.seccomp_bpf)]
        return [*launcher, "--", *command]

    def cleanup(self) -> None:
        if self._owned_workspace and self.workspace is not None:
            shutil.rmtree(self.workspace, ignore_errors=True)
            self.workspace = None
            self._owned_workspace = False


class _SandboxedKernelManager(AsyncKernelManager):
    def __init__(self, *args: Any, profile: SandboxProfile, **kwargs: Any) -> None:
        self.profile = profile
        super().__init__(*args, **kwargs)

    def format_kernel_cmd(self, extra_arguments: list[str] | None = None) -> list[str]:
        command = super().format_kernel_cmd(extra_arguments)
        connection_file = str(self.connection_file)
        return self.profile.wrap_command(command, connection_file)

@dataclass(frozen=True)
class ExecutionResult:
    stdout: str = ""
    stderr: str = ""
    outputs: tuple[dict[str, Any], ...] = ()
    execution_count: int | None = None
    status: str = "ok"
    error: dict[str, Any] | None = None


class KernelSession:
    """Own a persistent IPython kernel and normalize its asynchronous outputs."""

    def __init__(
        self,
        profile: SandboxProfile | None = None,
        *,
        kernel_name: str = "python3",
        startup_timeout: float = 30.0,
    ) -> None:
        self.profile = profile or SandboxProfile()
        self.kernel_name = kernel_name
        self.startup_timeout = startup_timeout
        self.manager: _SandboxedKernelManager | None = None
        self.client: Any | None = None
        self._execute_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.manager is not None:
            return
        workspace = self.profile.prepare()
        connection_file = workspace / ".kernel-connection.json"
        manager_kwargs = {
            "profile": self.profile,
            "kernel_name": self.kernel_name,
            "connection_file": str(connection_file),
        }
        if self.profile.use_bubblewrap:
            # IPC sockets live under workspace/kernel so they stay inside the
            # bwrap bind-mount. Required when --unshare-net is on (TCP loopback
            # would not reach the host).
            manager_kwargs["transport"] = "ipc"
            manager_kwargs["ip"] = str(workspace / "kernel")
        self.manager = _SandboxedKernelManager(**manager_kwargs)
        try:
            inherited_env = {
                key: os.environ[key]
                for key in ("HOME", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "WINDIR")
                if key in os.environ
            }
            await self.manager.start_kernel(
                cwd=str(workspace),
                env={
                    **inherited_env,
                    "PATH": os.defpath,
                    "TMPDIR": str(workspace),
                    "JUPYTER_RUNTIME_DIR": str(workspace),
                    "JUPYTER_CONFIG_DIR": str(workspace / ".jupyter"),
                    "JUPYTER_DATA_DIR": str(workspace / ".local" / "share" / "jupyter"),
                    "IPYTHONDIR": str(workspace / ".ipython"),
                    "XDG_CACHE_HOME": str(workspace / ".cache"),
                },
            )
            self.client = self.manager.client()
            self.client.start_channels()
            await self.client.wait_for_ready(timeout=self.startup_timeout)
        except BaseException:
            await self.close()
            raise

    async def execute(self, code: str, *, timeout: float = 60.0) -> ExecutionResult:
        if not isinstance(code, str):
            raise TypeError("code must be a string")
        if self.manager is None or self.client is None:
            raise RuntimeError("kernel session is not started")
        async with self._execute_lock:
            message_id = self.client.execute(
                code, stop_on_error=False, allow_stdin=False
            )
            started = time.monotonic()
            shell_task = asyncio.create_task(self.client.get_shell_msg())
            iopub_task = asyncio.create_task(self.client.get_iopub_msg())
            stdout: list[str] = []
            stderr: list[str] = []
            outputs: list[dict[str, Any]] = []
            error: dict[str, Any] | None = None
            execution_count: int | None = None
            shell_reply: dict[str, Any] | None = None
            idle_seen = False
            try:
                while shell_reply is None or not idle_seen:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TimeoutError(f"kernel execution exceeded {timeout} seconds")
                    done, _ = await asyncio.wait(
                        {task for task in (shell_task, iopub_task) if not task.done()},
                        timeout=remaining,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        raise TimeoutError(f"kernel execution exceeded {timeout} seconds")
                    if iopub_task in done:
                        message = iopub_task.result()
                        parent_id = message.get("parent_header", {}).get("msg_id")
                        if parent_id == message_id:
                            message_type = message.get("header", {}).get("msg_type")
                            content = message.get("content", {})
                            if message_type == "stream":
                                target = stderr if content.get("name") == "stderr" else stdout
                                target.append(content.get("text", ""))
                            elif message_type == "error":
                                error = content
                                outputs.append({"output_type": "error", **content})
                            elif message_type == "execute_result":
                                execution_count = content.get("execution_count")
                                outputs.append(
                                    {
                                        "output_type": "execute_result",
                                        "data": content.get("data", {}),
                                        "metadata": content.get("metadata", {}),
                                        "execution_count": execution_count,
                                    }
                                )
                            elif message_type == "display_data":
                                outputs.append(
                                    {
                                        "output_type": "display_data",
                                        "data": content.get("data", {}),
                                        "metadata": content.get("metadata", {}),
                                    }
                                )
                            elif message_type == "status" and content.get("execution_state") == "idle":
                                idle_seen = True
                        if not idle_seen:
                            iopub_task = asyncio.create_task(self.client.get_iopub_msg())
                    if shell_task in done:
                        candidate = shell_task.result()
                        if candidate.get("parent_header", {}).get("msg_id") == message_id:
                            shell_reply = candidate.get("content", {})
                            execution_count = shell_reply.get("execution_count", execution_count)
                            if shell_reply.get("status") == "error" and error is None:
                                error = shell_reply
                        else:
                            shell_task = asyncio.create_task(self.client.get_shell_msg())
                return ExecutionResult(
                    stdout="".join(stdout),
                    stderr="".join(stderr),
                    outputs=tuple(outputs),
                    execution_count=execution_count,
                    status=(shell_reply or {}).get("status", "ok"),
                    error=error,
                )
            except TimeoutError:
                await self.manager.interrupt_kernel()
                raise
            finally:
                for task in (shell_task, iopub_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(shell_task, iopub_task, return_exceptions=True)

    async def close(self) -> None:
        manager, client = self.manager, self.client
        self.manager = None
        self.client = None
        if client is not None:
            client.stop_channels()
        if manager is not None:
            await manager.shutdown_kernel(now=True)
        self.profile.cleanup()

    async def __aenter__(self) -> KernelSession:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()


# ---------------------------------------------------------------------------
# Process-wide singleton used by agent.py (sync facade over one session)
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_session: KernelSession | None = None
_session_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    if _loop is None:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, name="sovereign-kernel", daemon=True)
        thread.start()
        _loop = loop
        _loop_thread = thread
    return _loop


def run_coroutine(coro, *, timeout: float | None = None):
    """Run an async coroutine on the background kernel loop."""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def get_kernel() -> "SyncKernel":
    """Lazy singleton — one sandboxed kernel process per runtime."""
    global _session
    with _session_lock:
        if _session is None:
            async def _start() -> KernelSession:
                session = KernelSession(SandboxProfile(use_bubblewrap=True, use_seccomp=True))
                await session.start()
                return session
            try:
                _session = run_coroutine(_start(), timeout=60)
            except Exception as exc:
                raise KernelUnavailable(str(exc)) from exc
        return SyncKernel(_session)


class SyncKernel:
    """Blocking view of KernelSession, matching SE2 execute_code() shape."""

    def __init__(self, session: KernelSession) -> None:
        self.session = session

    def execute_code(self, code: str, timeout: int = 10) -> dict[str, Any]:
        result = run_coroutine(self.session.execute(code, timeout=float(timeout)), timeout=timeout + 5)
        result_text = None
        for output in result.outputs:
            if output.get("output_type") == "execute_result":
                result_text = output.get("data", {}).get("text/plain", "")
        stderr = result.stderr
        if result.status != "ok" and result.error:
            from .repl.errors import format_execution_error
            stderr = format_execution_error(result.error, result.stderr)
        return {
            "success": result.status == "ok",
            "stdout": (result.stdout or "").strip(),
            "stderr": (stderr or "").strip(),
            "result": result_text,
            "execution": result,
        }

    def reset(self) -> None:
        run_coroutine(self.session.close(), timeout=30)
        global _session
        _session = None
        # get_kernel will start a fresh session on next use

    def shutdown(self) -> None:
        shutdown_kernel()


def shutdown_kernel() -> None:
    global _session
    with _session_lock:
        session = _session
        _session = None
    if session is not None:
        try:
            run_coroutine(session.close(), timeout=30)
        except Exception:
            pass


def extract_exec_blocks(reply_text: str) -> list[str]:
    return [match.strip() for match in _EXEC_BLOCK_RE.findall(reply_text)]


def execution_allowed() -> tuple[bool, str]:
    status = gate_status()
    if status in ("HARD", "CRITICAL"):
        return False, (
            f"Execution blocked: entropy gate={status}. "
            "Resolve via /confess or /absolve first."
        )
    return True, "ok"


def run_blocks_and_log(
    blocks: list[str],
    *,
    repair_sampler=None,
    llm_cfg=None,
    timeout: int = 60,
    max_attempts: int = 3,
) -> list[dict[str, Any]]:
    """Execute each ```exec``` block, repairing failures through the REPL loop."""
    allowed, reason = execution_allowed()
    if not allowed:
        append_event(kind="code_execution_blocked", content=reason, meta={"blocks": len(blocks)})
        return [
            {"success": False, "stdout": "", "stderr": reason, "result": None, "code": block}
            for block in blocks
        ]

    from .repair import default_repair_sampler, run_repair_loop

    sampler = repair_sampler if repair_sampler is not None else default_repair_sampler(llm_cfg)
    kernel = get_kernel()
    results: list[dict[str, Any]] = []
    for code in blocks:
        outcome = run_coroutine(
            run_repair_loop(
                code,
                sampler,
                kernel=kernel.session,
                max_attempts=max_attempts,
                timeout=float(timeout),
            ),
            timeout=timeout * max_attempts + 30,
        )
        results.append(outcome)
    return results


def format_results_for_model(results: list[dict[str, Any]]) -> str:
    parts = ["Execution results for the code you submitted:"]
    for index, result in enumerate(results, 1):
        parts.append(f"\n--- block {index} ---")
        parts.append(f"success={result['success']}")
        if result.get("stdout"):
            parts.append(f"stdout:\n{result['stdout']}")
        if result.get("stderr"):
            parts.append(f"stderr:\n{result['stderr']}")
        if result.get("result"):
            parts.append(f"result: {result['result']}")
        if result.get("corrected"):
            parts.append(f"repaired_code:\n{result.get('code', '')}")
    return "\n".join(parts)


def harden_notice() -> str:
    return __doc__ or ""
