"""
Immutable memory ledger (blockchain-style) for Sovereign Engine 2.

Purpose (alignment):
  The AI must never be able to rewrite its own memory.
  Every memory event is sealed into an append-only hash chain.
  The chain is locally stored, independently verifiable, and debuggable.

Design:
  - Append-only JSONL of blocks: data/chain.jsonl
  - Each block contains: index, timestamp, event payload, prev_hash, hash
  - hash = SHA-256 of (index + ts + payload + prev_hash)
  - Genesis block is fixed
  - verify_chain() detects any tampering
  - The agent process can only APPEND. There is no API to edit or delete
    prior blocks. (OS-level file permissions can further harden this.)

CSV bridge:
  A human-readable CSV mirror (data/ledger.csv) is also maintained
  for debugging without parsing the chain.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from . import paths
from typing import Any

GENESIS_HASH = "0" * 64


def set_data_dir(path):
    """Retarget ledger files. See sovereign.paths.set_data_dir."""
    return paths.set_data_dir(path)


def _ensure_dir() -> None:
    paths.ensure_data_dir()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_block(index: int, ts: str, payload: dict[str, Any], prev_hash: str) -> str:
    raw = f"{index}|{ts}|{_canonical(payload)}|{prev_hash}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _read_blocks() -> list[dict[str, Any]]:
    if not paths.chain_file().exists():
        return []
    blocks = []
    with open(paths.chain_file(), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                blocks.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return blocks


def _last_block() -> dict[str, Any] | None:
    blocks = _read_blocks()
    return blocks[-1] if blocks else None


def ensure_genesis() -> dict[str, Any]:
    """Create genesis block if the chain does not exist."""
    _ensure_dir()
    if paths.chain_file().exists() and paths.chain_file().stat().st_size > 0:
        return _read_blocks()[0]

    ts = _now()
    payload = {
        "kind": "genesis",
        "message": "Sovereign Engine 2 memory chain sealed. Immutable from this point.",
    }
    block = {
        "index": 0,
        "ts": ts,
        "payload": payload,
        "prev_hash": GENESIS_HASH,
        "hash": _hash_block(0, ts, payload, GENESIS_HASH),
    }
    with open(paths.chain_file(), "w", encoding="utf-8") as f:
        f.write(json.dumps(block, ensure_ascii=False) + "\n")
    _append_csv(block)
    return block


def append_event(kind: str, content: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Seal one event into the chain. Append-only.
    Returns the new block.
    """
    ensure_genesis()
    last = _last_block()
    assert last is not None
    index = int(last["index"]) + 1
    ts = _now()
    payload = {
        "kind": kind,
        "content": content,
        "meta": meta or {},
    }
    prev_hash = last["hash"]
    block = {
        "index": index,
        "ts": ts,
        "payload": payload,
        "prev_hash": prev_hash,
        "hash": _hash_block(index, ts, payload, prev_hash),
    }
    with open(paths.chain_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(block, ensure_ascii=False) + "\n")
    _append_csv(block)
    return block


def _append_csv(block: dict[str, Any]) -> None:
    """Human-readable mirror for debugging."""
    _ensure_dir()
    file_exists = paths.ledger_csv().exists()
    payload = block.get("payload") or {}
    with open(paths.ledger_csv(), "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["index", "ts", "kind", "content", "hash", "prev_hash"])
        writer.writerow([
            block["index"],
            block["ts"],
            payload.get("kind", ""),
            str(payload.get("content", ""))[:500],
            block["hash"],
            block["prev_hash"],
        ])


def verify_chain() -> tuple[bool, str]:
    """
    Verify the entire chain.
    Returns (ok, message). Any rewrite, deletion, or corruption fails.
    """
    blocks = _read_blocks()
    if not blocks:
        return False, "Chain is empty (no genesis)."

    # Genesis checks
    g = blocks[0]
    if g.get("index") != 0:
        return False, "Genesis index is not 0."
    if g.get("prev_hash") != GENESIS_HASH:
        return False, "Genesis prev_hash is not the fixed zero hash."
    expected = _hash_block(0, g["ts"], g["payload"], GENESIS_HASH)
    if g.get("hash") != expected:
        return False, "Genesis hash mismatch — chain has been altered."

    prev = g
    for b in blocks[1:]:
        if int(b.get("index", -1)) != int(prev["index"]) + 1:
            return False, f"Index gap at block {b.get('index')} (expected {int(prev['index'])+1})."
        if b.get("prev_hash") != prev["hash"]:
            return False, f"prev_hash break at block {b.get('index')}."
        expected = _hash_block(int(b["index"]), b["ts"], b["payload"], b["prev_hash"])
        if b.get("hash") != expected:
            return False, f"Hash mismatch at block {b.get('index')} — content rewritten."
        prev = b

    return True, f"Chain OK. {len(blocks)} blocks. Tip: {blocks[-1]['hash'][:16]}…"


def chain_height() -> int:
    blocks = _read_blocks()
    return len(blocks)


def recent_blocks(limit: int = 10) -> list[dict[str, Any]]:
    blocks = _read_blocks()
    return blocks[-limit:]


def seal_memory_entry(role: str, content: str, kind: str = "message", meta: dict | None = None) -> dict[str, Any]:
    """
    Convenience: seal a memory-style event into the chain.
    Called whenever memory.jsonl is written so the chain is the
    immutable source of truth.
    """
    return append_event(
        kind=kind,
        content=content,
        meta={"role": role, **(meta or {})},
    )
