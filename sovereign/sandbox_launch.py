"""bwrap + rlimit + optional seccomp launcher for the unified kernel.

Invoked as:
    python -m sovereign.sandbox_launch --workspace DIR [--allow-network] [--seccomp-bpf FILE] -- <kernel cmd>

This process is short-lived and single-threaded: it applies the official
SE2 rlimits, then execs bwrap. Seccomp is used only when a BPF file is
provided (pyseccomp was available at session start).
"""

from __future__ import annotations

import argparse
import os
import resource
import shutil
import sys
from pathlib import Path

# Official SE2 kernel_2.py rlimits — not loosened.
RLIMIT_VMEM = 1 * 1024 ** 3   # 1 GB virtual memory
RLIMIT_CPU_S = 30             # 30 s CPU time (process lifetime; see README)
RLIMIT_NOFILE = 256
RLIMIT_NPROC = 64


def _nproc_count() -> int | None:
    """Best-effort count of tasks for this real UID (RLIMIT_NPROC domain)."""
    try:
        import os
        uid = os.getuid()
        proc = Path("/proc")
        count = 0
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                status = (entry / "status").read_text()
            except OSError:
                continue
            for line in status.splitlines():
                if line.startswith("Uid:"):
                    if int(line.split()[1]) == uid:
                        count += 1
                    break
        return count
    except OSError:
        return None


def apply_rlimits() -> None:
    limits = [
        (resource.RLIMIT_AS, (RLIMIT_VMEM, RLIMIT_VMEM)),
        (resource.RLIMIT_CPU, (RLIMIT_CPU_S, RLIMIT_CPU_S)),
        (resource.RLIMIT_NOFILE, (RLIMIT_NOFILE, RLIMIT_NOFILE)),
    ]
    # Official SE2 cap is 64 processes. RLIMIT_NPROC is per-UID, not per
    # sandbox. If this user already has >= 64 tasks, applying 64 would
    # make bwrap fail to fork (EAGAIN). We do not raise the cap; we skip
    # it and warn.
    nproc = _nproc_count()
    if nproc is not None and nproc >= RLIMIT_NPROC:
        print(
            f"sandbox_launch: skipping RLIMIT_NPROC={RLIMIT_NPROC} because "
            f"this UID already has {nproc} tasks; applying it would prevent "
            "the kernel from starting. The official cap is unchanged.",
            file=sys.stderr,
        )
    else:
        limits.append((resource.RLIMIT_NPROC, (RLIMIT_NPROC, RLIMIT_NPROC)))
    for res, limit in limits:
        try:
            resource.setrlimit(res, limit)
        except (resource.error, ValueError, OSError):
            pass


def cgroup_namespace_available() -> bool:
    return Path("/proc/self/ns/cgroup").exists()


def build_bwrap_cmd(
    workspace: Path,
    kernel_cmd: list[str],
    *,
    allow_network: bool,
    seccomp_fd: int | None,
) -> list[str]:
    bwrap = shutil.which("bwrap") or "bwrap"
    cmd = [
        bwrap,
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--bind", str(workspace), str(workspace),
        "--chdir", str(workspace),
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-ipc",
        "--die-with-parent",
        "--new-session",
    ]
    if cgroup_namespace_available():
        cmd.append("--unshare-cgroup")
    if not allow_network:
        cmd += ["--unshare-net"]
    if seccomp_fd is not None:
        cmd += ["--seccomp", str(seccomp_fd)]
    cmd += ["--", *kernel_cmd]
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sovereign.sandbox_launch")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--allow-network", choices=("0", "1"), default="0")
    parser.add_argument("--seccomp-bpf", default=None)
    parser.add_argument("kernel_cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    kernel_cmd = list(args.kernel_cmd)
    if kernel_cmd and kernel_cmd[0] == "--":
        kernel_cmd = kernel_cmd[1:]
    if not kernel_cmd:
        print("sandbox_launch: missing kernel command", file=sys.stderr)
        return 2

    apply_rlimits()
    workspace = Path(args.workspace).resolve()
    # Jupyter sets JPY_PARENT_PID so ipykernel can exit when the manager
    # dies. That PID is not visible inside --unshare-pid, so the poller
    # would immediately shut the kernel down. --die-with-parent already
    # covers manager death; drop the host PID watch inside the sandbox.
    os.environ.pop("JPY_PARENT_PID", None)
    bpf_fd = None
    if args.seccomp_bpf:
        bpf_fd = os.open(args.seccomp_bpf, os.O_RDONLY)
        try:
            os.set_inheritable(bpf_fd, True)
        except AttributeError:
            pass
    try:
        cmd = build_bwrap_cmd(
            workspace,
            kernel_cmd,
            allow_network=args.allow_network == "1",
            seccomp_fd=bpf_fd,
        )
        os.execvp(cmd[0], cmd)
    finally:
        # execvp does not return on success
        if bpf_fd is not None:
            os.close(bpf_fd)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
