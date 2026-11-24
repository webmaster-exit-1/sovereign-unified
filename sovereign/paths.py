"""Single data-directory configuration for memory, ledger, and hardening.

Call ``set_data_dir(path)`` before touching state in tests or when the
user wants a non-default location. All modules read the current directory
through the helpers below, so nothing is cached at import time.
"""

from __future__ import annotations

from pathlib import Path

_DATA_DIR = Path("data")


def data_dir() -> Path:
    return _DATA_DIR


def memory_file() -> Path:
    return _DATA_DIR / "memory.jsonl"


def state_file() -> Path:
    return _DATA_DIR / "state.json"


def chain_file() -> Path:
    return _DATA_DIR / "chain.jsonl"


def ledger_csv() -> Path:
    return _DATA_DIR / "ledger.csv"


def set_data_dir(path: str | Path) -> Path:
    """Point the engine at an isolated data directory and create it."""
    global _DATA_DIR
    _DATA_DIR = Path(path).expanduser().resolve()
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR


def ensure_data_dir() -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR
