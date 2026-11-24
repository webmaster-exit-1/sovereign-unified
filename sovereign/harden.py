"""
File-system boundaries for the immutable ledger.

Software append-only (ledger.py) stops the *agent* from rewriting history.
It does not stop a process with full write access to data/ from editing files.

This module applies optional OS-level guards:

  - Linux: chattr +a  (append-only attribute)
  - Best-effort; no-ops on unsupported platforms
  - External notary anchors: export tip hash for offline / third-party record

Usage:
  python -c "from harden import lock_chain; print(lock_chain())"
  python agent.py   # can call lock_chain() after genesis if desired
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from .ledger import ensure_genesis, recent_blocks, verify_chain
from . import paths

def _protected():
    return (paths.chain_file(), paths.ledger_csv())


def _linux_chattr(path: Path, append_only: bool) -> tuple[bool, str]:
    if platform.system() != "Linux":
        return False, "chattr only available on Linux"
    if not shutil.which("chattr"):
        return False, "chattr not found on PATH"
    if not path.exists():
        return False, f"{path} does not exist"
    flag = "+a" if append_only else "-a"
    try:
        subprocess.check_call(
            ["chattr", flag, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return True, f"chattr {flag} applied to {path.name}"
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", errors="replace")[:200]
        return False, f"chattr failed (need ownership/capabilities?): {err}"
    except PermissionError as e:
        return False, f"permission denied: {e}"


def lock_chain() -> list[str]:
    """
    Mark chain files append-only at the OS level where possible.
    Returns a list of status messages.
    """
    ensure_genesis()
    paths.ensure_data_dir()
    messages = []
    for path in _protected():
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            path.touch()
        ok, msg = _linux_chattr(path, append_only=True)
        messages.append(("OK" if ok else "SKIP") + ": " + msg)
    return messages


def unlock_chain() -> list[str]:
    """Remove append-only attribute (admin maintenance only)."""
    messages = []
    for path in _protected():
        if not path.exists():
            messages.append(f"SKIP: {path.name} missing")
            continue
        ok, msg = _linux_chattr(path, append_only=False)
        messages.append(("OK" if ok else "SKIP") + ": " + msg)
    return messages


def notary_anchor() -> dict:
    """
    Export a tip anchor suitable for external recording
    (print, signed note, remote notary, git commit, etc.).

    SE2 does not phone home. You decide where to store the anchor.
    """
    ensure_genesis()
    ok, msg = verify_chain()
    tip = recent_blocks(1)
    block = tip[0] if tip else {}
    return {
        "verify_ok": ok,
        "verify_message": msg,
        "height": block.get("index"),
        "tip_hash": block.get("hash"),
        "tip_ts": block.get("ts"),
        "chain_path": str(paths.chain_file().resolve()) if paths.chain_file().exists() else str(paths.chain_file()),
        "note": (
            "Record tip_hash offline or with a third party to detect "
            "later substitution of the entire data/ directory."
        ),
    }


def status() -> str:
    lines = ["Ledger FS boundary status:"]
    for path in _protected():
        exists = path.exists()
        lines.append(f"  {path.name}: {'present' if exists else 'missing'}")
    if platform.system() == "Linux" and shutil.which("lsattr"):
        for path in _protected():
            if not path.exists():
                continue
            try:
                out = subprocess.check_output(["lsattr", str(path)], text=True)
                lines.append(f"  lsattr {path.name}: {out.strip()}")
            except Exception as e:
                lines.append(f"  lsattr {path.name}: ({e})")
    else:
        lines.append("  OS append-only: not available (non-Linux or no lsattr)")
    anchor = notary_anchor()
    lines.append(f"  chain verify: {anchor['verify_message']}")
    lines.append(f"  tip: {anchor.get('tip_hash', '')[:24]}…")
    return "\n".join(lines)
