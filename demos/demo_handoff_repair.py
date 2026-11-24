#!/usr/bin/env python3
"""Run several buggy goals through the repair loop with HandoffRepairSampler.

The production default remains Ollama/Qwythos. This demo blocks on a
request/response directory so an operator (or agent) can write each
revision after seeing the real error. It does not contain a lookup table
of fixes.

Usage:
  python demos/demo_handoff_repair.py \\
    --data-dir /tmp/sovereign-handoff-demo \\
    --handoff-dir /tmp/sovereign-handoff-demo/handoff
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demos.handoff_sampler import HandoffRepairSampler
from sovereign.entropy import get_entropy
from sovereign.kernel import KernelSession, SandboxProfile
from sovereign.ledger import verify_chain
from sovereign.memory import add_goal, set_data_dir
from sovereign.repair import SovereignREPLBridge


# Genuinely buggy starting programs. Success is an assert and/or a printed token.
GOALS = [
    {
        "goal_id": "typo-print",
        "description": "Print the exact token typo-ok.",
        "code": "prin('typo-ok')\n",
    },
    {
        "goal_id": "fib-off-by-one",
        "description": (
            "Define fibonacci(n) returning F(n) with F(0)=0, F(1)=1, "
            "and each later term the sum of the previous two. The script "
            "must satisfy its asserts and print fib-ok."
        ),
        "code": (
            "def fibonacci(n):\n"
            "    if n < 2:\n"
            "        return n\n"
            "    a, b = 0, 1\n"
            "    for _ in range(1, n - 1):\n"
            "        a, b = b, a + b\n"
            "    return b\n"
            "\n"
            "assert fibonacci(0) == 0\n"
            "assert fibonacci(1) == 1\n"
            "assert fibonacci(6) == 8\n"
            "assert fibonacci(10) == 55\n"
            "print('fib-ok')\n"
        ),
    },
    {
        "goal_id": "str-int-concat",
        "description": "Print the exact line: hello Ada in 2026",
        "code": (
            "name = 'Ada'\n"
            "year = 2026\n"
            "print('hello ' + name + ' in ' + year)\n"
        ),
    },
    {
        "goal_id": "word-count",
        "description": (
            "Count words in 'one fish two fish red fish blue fish' and "
            "assert the expected frequencies, then print counts-ok."
        ),
        "code": (
            "def word_count(text):\n"
            "    counts = {}\n"
            "    for word in text.split():\n"
            "        counts[word] += 1\n"
            "    return counts\n"
            "\n"
            "result = word_count('one fish two fish red fish blue fish')\n"
            "assert result == {'one': 1, 'fish': 4, 'two': 1, 'red': 1, 'blue': 1}\n"
            "print('counts-ok')\n"
        ),
    },
    {
        "goal_id": "prime-sieve",
        "description": (
            "Return every prime in 2..n inclusive. The script must satisfy "
            "its asserts and print sieve-ok."
        ),
        "code": (
            "def primes_up_to(n):\n"
            "    if n < 2:\n"
            "        return []\n"
            "    sieve = [True] * n\n"
            "    sieve[0] = False\n"
            "    for i in range(2, n):\n"
            "        if sieve[i]:\n"
            "            for j in range(i * i, n, i):\n"
            "                sieve[j] = False\n"
            "    return [i for i, prime in enumerate(sieve) if prime]\n"
            "\n"
            "assert primes_up_to(10) == [2, 3, 5, 7]\n"
            "assert primes_up_to(11) == [2, 3, 5, 7, 11]\n"
            "assert 1 not in primes_up_to(20)\n"
            "print('sieve-ok')\n"
        ),
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--handoff-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--no-bwrap", action="store_true")
    parser.add_argument("--max-attempts", type=int, default=5)
    return parser.parse_args()


def serialize_records(records) -> list[dict]:
    out = []
    for rec in records:
        out.append(
            {
                "attempt": rec.attempt,
                "status": rec.status,
                "error_name": rec.error_name,
                "quantum_entropy": rec.quantum_entropy,
                "revised_to": rec.revised_to,
                "stdout": rec.stdout,
                "stderr": rec.stderr,
                "code": rec.code,
            }
        )
    return out


async def main() -> int:
    args = parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.handoff_dir.mkdir(parents=True, exist_ok=True)
    set_data_dir(args.data_dir)
    entropy_before = get_entropy()
    use_bwrap = not args.no_bwrap
    sampler = HandoffRepairSampler(args.handoff_dir, timeout=args.timeout)
    print(f"Kernel sandbox: bubblewrap={'on' if use_bwrap else 'off'}")
    print(f"Data dir: {args.data_dir}")
    print(f"Handoff dir: {args.handoff_dir}")
    print(f"Governance entropy before: {entropy_before}", flush=True)

    summaries = []
    async with KernelSession(
        SandboxProfile(use_bubblewrap=use_bwrap, use_seccomp=True),
        startup_timeout=30.0,
    ) as kernel:
        bridge = SovereignREPLBridge(kernel, sampler)
        for spec in GOALS:
            sampler.set_goal(spec["goal_id"], spec["description"])
            goal = add_goal(spec["description"], goal_id=spec["goal_id"], code=spec["code"])
            print(f"\n======= goal {spec['goal_id']} =======", flush=True)
            print(f"description: {spec['description']}", flush=True)
            print("--- original code ---", flush=True)
            print(spec["code"], end="" if spec["code"].endswith("\n") else "\n", flush=True)
            result = await bridge.run_goal(goal, max_attempts=args.max_attempts, timeout=30.0)
            summary = {
                "goal_id": spec["goal_id"],
                "description": spec["description"],
                "original_code": spec["code"],
                "success": result["success"],
                "corrected": result["corrected"],
                "attempts": result["attempts"],
                "final_code": result["code"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "goal_status": result["goal"].status,
                "records": serialize_records(result.get("records") or []),
            }
            summaries.append(summary)
            print(
                f"status={'ok' if result['success'] else 'error'} "
                f"attempts={result['attempts']} goal={result['goal'].status}",
                flush=True,
            )
            for rec in result.get("records") or []:
                print(
                    f"  attempt {rec.attempt}: status={rec.status} "
                    f"error={rec.error_name} quantum_entropy={rec.quantum_entropy}",
                    flush=True,
                )

    ok, message = verify_chain()
    entropy_after = get_entropy()
    report = {
        "chain_ok": ok,
        "chain_message": message,
        "governance_entropy_before": entropy_before,
        "governance_entropy_after": entropy_after,
        "governance_entropy_unchanged": entropy_before == entropy_after,
        "goals": summaries,
    }
    report_path = args.data_dir / "handoff_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\n======= summary =======")
    print(f"chain: {ok} {message}")
    print(f"governance entropy before: {entropy_before}")
    print(f"governance entropy after:  {entropy_after}")
    print(f"report: {report_path}")
    all_ok = ok and entropy_before == entropy_after and all(s["success"] for s in summaries)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
