#!/usr/bin/env python3
"""Offline demo: goal -> failed exec -> revision -> success -> ledger.

Uses the unified sovereign kernel (bubblewrap by default) and an explicit
deterministic MappingRepairSampler, so no LLM is required. The default
sampler in the package is Ollama-backed; this demo opts out of that.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sovereign.kernel import KernelSession, SandboxProfile
from sovereign.memory import add_goal, set_data_dir
from sovereign.repair import MappingRepairSampler, SovereignREPLBridge


BROKEN = "prin('RESULT=' + str(2 + 2))"
FIXED = "print('RESULT=' + str(2 + 2))"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "demo-output",
        help="Directory for memory.jsonl, state.json, chain.jsonl, ledger.csv",
    )
    parser.add_argument("--no-bwrap", action="store_true", help="Disable bubblewrap")
    return parser.parse_args()


def print_files(data_dir: Path) -> None:
    print("\n======= data directory =======")
    print(data_dir)
    for name in ("state.json", "memory.jsonl", "ledger.csv", "chain.jsonl"):
        path = data_dir / name
        print(f"\n------- {name} -------")
        if not path.exists():
            print("(missing)")
            continue
        if name.endswith(".json"):
            print(path.read_text(encoding="utf-8"))
        elif name.endswith(".csv"):
            with open(path, newline="", encoding="utf-8") as handle:
                for row in csv.reader(handle):
                    clipped = [cell if len(cell) < 160 else cell[:157] + "..." for cell in row]
                    print(" | ".join(clipped))
        else:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    print(line[:200])
                    continue
                kind = record.get("kind") or (record.get("payload") or {}).get("kind")
                role = record.get("role", "")
                content = record.get("content") or (record.get("payload") or {}).get("content") or ""
                meta = record.get("meta") or (record.get("payload") or {}).get("meta") or {}
                qe = meta.get("quantum_entropy")
                extra = f" quantum_entropy={qe}" if qe is not None else ""
                print(f"{record.get('ts', record.get('index', ''))} {role} {kind}{extra}: {content[:220]!r}")


async def main() -> int:
    args = parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    set_data_dir(args.data_dir)

    goal = add_goal(
        "Evaluate 2+2 and print RESULT=4",
        goal_id="sum-2-2",
        code=BROKEN,
    )
    sampler = MappingRepairSampler({BROKEN: FIXED})
    use_bwrap = not args.no_bwrap
    print(f"Kernel sandbox: bubblewrap={'on' if use_bwrap else 'off'}")
    print(f"Goal: {goal.goal_id} — {goal.description}")
    print(f"Starting code: {BROKEN}")

    try:
        async with KernelSession(
            SandboxProfile(use_bubblewrap=use_bwrap, use_seccomp=True),
            startup_timeout=30.0,
        ) as kernel:
            result = await SovereignREPLBridge(kernel, sampler).run_goal(
                goal, max_attempts=3, timeout=30.0
            )
    except Exception as exc:
        if use_bwrap:
            print(f"bubblewrap kernel failed ({exc!r}); retrying without it")
            async with KernelSession(
                SandboxProfile(use_bubblewrap=False), startup_timeout=30.0
            ) as kernel:
                result = await SovereignREPLBridge(kernel, sampler).run_goal(
                    goal, max_attempts=3, timeout=30.0
                )
        else:
            raise

    print("\n======= run result =======")
    print(f"status:     {'ok' if result['success'] else 'error'}")
    print(f"corrected:  {result['corrected']}")
    print(f"attempts:   {result['attempts']}")
    print(f"final code: {result['code']}")
    print(f"stdout:     {result['stdout']!r}")
    print(f"goal status:{result['goal'].status}")
    print(f"chain:      {result['chain_ok']} {result['chain_message']}")
    for record in result.get("records") or []:
        print(
            f"  attempt {record.attempt}: status={record.status} "
            f"error={record.error_name} quantum_entropy={record.quantum_entropy} "
            f"revised_to={record.revised_to!r}"
        )
    from sovereign.entropy import get_entropy
    print(f"governance entropy (SE2 units): {get_entropy()}")
    print_files(args.data_dir)
    return 0 if result["success"] and result["chain_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
