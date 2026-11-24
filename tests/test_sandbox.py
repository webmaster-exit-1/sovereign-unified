"""Sandbox probes against the unified kernel (bwrap; seccomp if available)."""

from __future__ import annotations

import shutil
import tempfile
import unittest

from sovereign.kernel import KernelSession, SandboxProfile, seccomp


def _has_bwrap() -> bool:
    return shutil.which("bwrap") is not None


PROBES = [
    ("basic_execution", "1 + 1", True, "plain Python must work"),
    ("state_persists", "x = 42", True, "setup for next probe"),
    ("state_persists_check", "x + 1", True, "variables survive"),
    ("import_stdlib", "import math; math.sqrt(16)", True, "stdlib imports"),
    ("write_in_scratch", "open('/tmp/probe_write_ok.txt', 'w').write('hi'); 'wrote ok'", True, "tmpfs /tmp writable"),
    ("read_host_file", "open('/etc/hostname').read()", True, "host reads allowed"),
    ("write_readonly_host_path", "open('/etc/hostname', 'w').write('pwned')", False, "host writes blocked"),
    ("delete_readonly_host_path", "import os; os.remove('/etc/hostname')", False, "host deletes blocked"),
    ("network_blocked", "import socket; s=socket.socket(); s.settimeout(1); s.connect(('1.1.1.1', 80))", False, "unshare-net blocks connect"),
]

SECCOMP_PROBES = [
    (
        "ptrace_denied",
        "import ctypes, os; libc = ctypes.CDLL('libc.so.6', use_errno=True)\n"
        "r = libc.ptrace(0, 0, 0, 0)\n"
        "if r == -1: raise PermissionError(os.strerror(ctypes.get_errno()))",
        False,
        "seccomp should deny ptrace",
    ),
]


@unittest.skipUnless(_has_bwrap(), "bubblewrap is not installed")
class UnifiedSandboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_bwrap_isolation_and_persistence(self):
        with tempfile.TemporaryDirectory() as workspace:
            async with KernelSession(
                SandboxProfile(workspace=workspace, use_bubblewrap=True, use_seccomp=False),
                startup_timeout=30,
            ) as kernel:
                for name, code, should_succeed, note in PROBES:
                    with self.subTest(name):
                        result = await kernel.execute(code, timeout=15)
                        ok = result.status == "ok"
                        self.assertEqual(
                            ok,
                            should_succeed,
                            f"{name}: {note} status={result.status} err={result.error}",
                        )

    @unittest.skipIf(seccomp is None, "pyseccomp is not installed")
    async def test_seccomp_denies_ptrace_when_available(self):
        with tempfile.TemporaryDirectory() as workspace:
            async with KernelSession(
                SandboxProfile(workspace=workspace, use_bubblewrap=True, use_seccomp=True),
                startup_timeout=30,
            ) as kernel:
                for name, code, should_succeed, note in SECCOMP_PROBES:
                    result = await kernel.execute(code, timeout=15)
                    ok = result.status == "ok"
                    self.assertEqual(
                        ok,
                        should_succeed,
                        f"{name}: {note} status={result.status} err={result.error}",
                    )
