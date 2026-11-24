"""
kernel_2.py — Jupyter kernel launched inside a bubblewrap (bwrap) sandbox
with a seccomp syscall filter, for SE2's "unauthenticated code" path.

This replaces the original kernel_2.py design, which built a seccomp
filter in a multiprocessing worker that execute_turn() never actually
sent code to — the LLM's code ran through the plain, unsandboxed
jupyter_client kernel, same as kernel.py. This version applies the
sandbox to the actual kernel process that runs the code.

Honest scope (same convention as kernel.py's harden_notice()):

  Does: launches the ipykernel process inside a bwrap sandbox — new
  PID/UTS/IPC namespaces, network namespace off by default, filesystem
  read-only except a private scratch dir — plus a seccomp filter that
  DENIES a specific list of dangerous syscalls (namespace escape, kernel
  module loading, ptrace, raw hardware access, clock/hostname tampering).
  Also applies rlimits (virtual memory, CPU time, open files, processes)
  via preexec_fn before exec — this bounds runaway cells and memory
  bombs without needing an external tool. Cgroup namespace unsharing is
  attempted but gracefully skipped on kernels that don't support it
  (detected via /proc/self/ns/cgroup before bwrap is invoked).

  Does NOT: use a syscall *allowlist* — that is tight enough to be
  provably safe but also tight enough to break ipykernel/pyzmq before
  they finish starting. Does not protect against host-kernel 0-days.
  Read access to the rest of the filesystem is still allowed (bind-
  mounted read-only) — this stops writes/deletes, not reads; bind-mount
  a narrower path list instead of "/" if confidentiality of host files
  matters to you.

  rlimits note: preexec_fn is technically not safe alongside Python
  threads (fork-after-threads deadlock risk) but resource.setrlimit()
  is a thin syscall wrapper with no Python locks, so in practice it
  does not deadlock. If you hit a hang on startup, replace _apply_rlimits
  with a prlimit(1) prefix on the bwrap command instead.

  NOT TESTED against a live SE2 run — bwrap and jupyter_client aren't
  available in the environment this was written in. Before trusting it
  with model-written code, run a few probes: try open('/etc/shadow') and
  confirm you can read but not write it; try a raw socket() call and
  confirm it's blocked by the network namespace; try os.system("rm -rf
  /tmp/*") and confirm nothing outside the scratch dir is touched; try
  ctypes-based ptrace and confirm it's denied by seccomp.

Requires:
  - bubblewrap on PATH  (apt install bubblewrap / dnf install bubblewrap)
  - pip install pyseccomp
  - jupyter_client + ipykernel already in your venv (same as kernel.py)

Integration: kernel.py's get_kernel() already imports and prefers this
class. No changes to kernel.py needed — if bwrap or pyseccomp are
absent, kernel.py falls back to the unsandboxed SandboxKernel and
prints a loud warning.
"""

from __future__ import annotations

import errno
import os
import queue
import resource
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    import pyseccomp as seccomp
except ImportError:  # pragma: no cover
    seccomp = None  # type: ignore

try:
    from jupyter_client import BlockingKernelClient
    from jupyter_client.connect import write_connection_file
except ImportError:  # pragma: no cover
    BlockingKernelClient = None  # type: ignore
    write_connection_file = None  # type: ignore


# Syscalls denied regardless of what the executed code needs.
# Modelled on Docker's default seccomp profile: these are the syscalls
# that let a process escape its namespace, load kernel code, tamper with
# other processes, or touch raw hardware/clock state.
_DENIED_SYSCALLS = [
    # namespace / mount escape
    "unshare", "setns", "mount", "umount2", "pivot_root", "chroot",
    # kernel module / firmware
    "init_module", "finit_module", "delete_module",
    "kexec_load", "kexec_file_load",
    # cross-process tampering / debugging
    "ptrace", "process_vm_readv", "process_vm_writev",
    # kernel keyring, BPF, perf (info-leak / privesc surface)
    "add_key", "request_key", "keyctl", "bpf", "perf_event_open",
    # host state tampering
    "reboot", "settimeofday", "clock_settime", "clock_adjtime", "adjtimex",
    "sethostname", "setdomainname", "swapon", "swapoff",
    # raw hardware I/O
    "iopl", "ioperm",
    # misc
    "acct", "personality", "quotactl", "nfsservctl",
]

# ---------------------------------------------------------------------------
# rlimit defaults — tune these for your threat model.
# They apply to the bwrap child process (and everything it exec's).
# ---------------------------------------------------------------------------
_RLIMIT_VMEM   = 1 * 1024 ** 3   # 1 GB virtual memory
_RLIMIT_CPU_S  = 30               # 30 s CPU time per cell
_RLIMIT_NOFILE = 256              # open file descriptors
_RLIMIT_NPROC  = 64               # threads/processes


def _apply_rlimits() -> None:
    """
    Called via preexec_fn — runs in the child process before exec.

    resource.setrlimit() is a thin syscall wrapper with no Python GIL
    or lock usage, so the fork-after-threads risk is negligible here.
    Each limit is attempted independently so a missing resource constant
    on an unusual arch doesn't abort the whole set.
    """
    for res, limit in [
        (resource.RLIMIT_AS,     (_RLIMIT_VMEM,   _RLIMIT_VMEM)),
        (resource.RLIMIT_CPU,    (_RLIMIT_CPU_S,  _RLIMIT_CPU_S)),
        (resource.RLIMIT_NOFILE, (_RLIMIT_NOFILE, _RLIMIT_NOFILE)),
        (resource.RLIMIT_NPROC,  (_RLIMIT_NPROC,  _RLIMIT_NPROC)),
    ]:
        try:
            resource.setrlimit(res, limit)
        except (resource.error, ValueError, OSError):
            pass  # not supported on this arch or kernel — skip silently


def _cgroup_namespace_available() -> bool:
    """
    Return True if the kernel exposes a cgroup namespace for this process.
    On older kernels (< 4.6) or hardened configs, /proc/self/ns/cgroup
    does not exist and bwrap --unshare-cgroup would fail with EINVAL.
    Checking before invoking bwrap avoids a hard crash at startup.
    """
    return Path("/proc/self/ns/cgroup").exists()


class KernelUnavailable(RuntimeError):
    pass


def _build_seccomp_bpf(path: Path) -> None:
    """Compile the deny-list into a BPF program bwrap can load via --seccomp."""
    if seccomp is None:
        raise KernelUnavailable("pyseccomp not installed. pip install pyseccomp")
    f = seccomp.SyscallFilter(defaction=seccomp.ALLOW)
    for name in _DENIED_SYSCALLS:
        try:
            f.add_rule(seccomp.ERRNO(errno.EPERM), name)
        except Exception:
            pass  # syscall not defined on this arch/libc — nothing to deny
    with open(path, "wb") as fh:
        f.export_bpf(fh.fileno())


class SandboxedJupyterKernel:
    """
    Drop-in replacement for kernel.py's SandboxKernel. Same execute_code()
    return shape; the kernel process runs inside a bwrap sandbox with a
    seccomp filter and rlimits instead of directly as your user.
    """

    def __init__(self, allow_network: bool = False, start_timeout: int = 15):
        if BlockingKernelClient is None:
            raise KernelUnavailable(
                "jupyter_client not installed. pip install ipykernel jupyter_client"
            )
        if shutil.which("bwrap") is None:
            raise KernelUnavailable(
                "bubblewrap not found on PATH. apt install bubblewrap / dnf install bubblewrap"
            )
        if seccomp is None:
            raise KernelUnavailable(
                "pyseccomp not installed. pip install pyseccomp"
            )
        self.allow_network = allow_network
        self.start_timeout = start_timeout
        self.scratch = Path(tempfile.mkdtemp(prefix="se2-kernel-"))
        self._bpf_path = self.scratch / "seccomp.bpf"
        self.conn_file = self.scratch / "connection.json"
        self.proc: subprocess.Popen | None = None
        self.client: BlockingKernelClient | None = None
        self._launch()

    def _launch(self) -> None:
        _build_seccomp_bpf(self._bpf_path)

        # IPC transport: unix-domain sockets under self.scratch.
        # Required because --unshare-net gives the kernel its own network
        # namespace, breaking TCP loopback. Unix sockets on the bind-mounted
        # scratch dir work across the boundary; TCP wouldn't.
        #
        # jupyter_client API note: write_connection_file moved across versions.
        # If this raises TypeError, check your jupyter_client version and
        # adjust the call signature accordingly.
        write_connection_file(
            str(self.conn_file),
            ip=str(self.scratch),
            transport="ipc",
        )

        # Open the BPF file as an inheritable fd so bwrap can read it via
        # --seccomp <fd>. pass_fds ensures it survives the exec; we close
        # our copy in the parent immediately after Popen returns.
        bpf_fd = os.open(self._bpf_path, os.O_RDONLY)
        cmd = self._bwrap_cmd(bpf_fd)
        try:
            self.proc = subprocess.Popen(
                cmd,
                pass_fds=(bpf_fd,),
                preexec_fn=_apply_rlimits,
            )
        finally:
            os.close(bpf_fd)  # parent's copy; child (bwrap) keeps its own

        self.client = BlockingKernelClient(connection_file=str(self.conn_file))
        self.client.load_connection_file()
        self.client.start_channels()
        self.client.wait_for_ready(timeout=self.start_timeout)

    def _bwrap_cmd(self, bpf_fd: int) -> list[str]:
        cmd = [
            "bwrap",
            "--ro-bind", "/", "/",       # host FS read-only
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--bind", str(self.scratch), str(self.scratch),  # scratch writable
            "--chdir", str(self.scratch),
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-ipc",
            "--die-with-parent",
            "--new-session",
        ]
        # Cgroup namespace unsharing requires kernel >= 4.6 and the
        # appropriate config. Skip it rather than crash on unsupported kernels.
        if _cgroup_namespace_available():
            cmd.append("--unshare-cgroup")

        if not self.allow_network:
            cmd += ["--unshare-net"]

        # --seccomp takes the fd number as a decimal string; bwrap reads
        # the BPF filter from it before setting up the final process.
        cmd += ["--seccomp", str(bpf_fd)]
        cmd += ["--", sys.executable, "-m", "ipykernel_launcher", "-f", str(self.conn_file)]
        return cmd

    def execute_code(self, code: str, timeout: int = 10) -> dict[str, Any]:
        assert self.client is not None
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
        """No in-place restart across a namespace boundary — tear down and relaunch."""
        self.shutdown()
        self._launch()

    def shutdown(self) -> None:
        if self.client is not None:
            try:
                self.client.stop_channels()
            except Exception:
                pass
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.scratch, ignore_errors=True)


def sandbox_notice() -> str:
    return __doc__ or ""
