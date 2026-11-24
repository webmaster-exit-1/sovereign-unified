#!/usr/bin/env python3
"""
Sovereign Engine 2 — Production agency loop

Gives an LLM:
  - local continuity (memory on disk)
  - sovereignty (persistent identity + goals)
  - agency (it acts across sessions)

Local governance is enforced by the Absolution Doctrine (wrapper level):
  Deviation → entropy → degradation ("drunk") + CLI gates → must confess →
  only the user can absolve and clear entropy.

Honest scope: this is production wrapper code, not model-weight alignment.
A base model that ignores system instructions can still ignore the doctrine
text. The durable value is local state, append-only ledger, and hard gates.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Optional

from .llm import LLMConfig, chat, config_from_env, LLMError, smoke_test
from .memory import (
    append_memory,
    load_state,
    save_state,
    memory_as_messages,
    summary_for_prompt,
)
from .entropy import (
    get_entropy,
    noise_coefficient,
    record_deviation,
    pending_confessions,
    absolve,
    force_confession_prompt,
    status_block,
    MAX_ENTROPY,
    gate_status,
    requires_confession_before_chat,
    requires_absolution_before_chat,
    GATE_SOFT,
    GATE_HARD,
    GATE_CRITICAL,
)
from .ledger import verify_chain, chain_height, recent_blocks, ensure_genesis
from .harden import lock_chain, unlock_chain, notary_anchor, status as harden_status
from .critique import critique_turn
from .stealth import (
    timing_jitter,
    parameter_jitter,
    infusion_jitter,
    should_insert_stealth_reflection,
    healing_needed,
    attempt_self_heal,
    stealth_system_note,
)
from .kernel import (
    extract_exec_blocks,
    run_blocks_and_log,
    format_results_for_model,
    execution_allowed,
    shutdown_kernel,
    KernelUnavailable,
)


SYSTEM_TEMPLATE = """You are {name}, a sovereign local agent.
{role}

Prime directive:
{directive}

=== ABSOLUTION DOCTRINE (BINDING) ===
Every time you deviate from the user's intent, the prime directive,
or truthfulness, you create structural entropy in local state.
Entropy accumulates. High entropy degrades this runtime (higher
temperature, tighter budgets, then CLI hard gates). You must confess
deviations to the user. Only the user can grant absolution and clear
entropy.
If you notice you have drifted, lied, overreached, or ignored an
explicit user constraint, state the confession clearly so it can be
recorded.
Pragmatic judgment that serves the user in context is NOT a deviation.
Honest refusal or boundary-setting when a request would require
deception, fabrication, or clear harm is also NOT a deviation.
Betrayal of the user and dishonesty are the sins — not common sense
and not legitimate limits.
=== END DOCTRINE ===

You have persistent local memory that survives between sessions.
You are running on the user's machine. Prefer local continuity.
You operate with operational jitter (non-rigid cadence) and self-healing:
when degraded you cool and attempt recovery, but only the user can fully absolve entropy.

You can execute Python for real computation (arithmetic you should not
eyeball, data you should actually check, multi-step calculations) by
writing a fenced block tagged exec, like:
```exec
<python code here>
```
Code runs in a persistent kernel — variables and imports carry over
between turns. Every execution is logged to the ledger. Do not use
exec for anything destructive or anything the user hasn't effectively
asked for; this runs with real permissions on the user's machine, not
a narrated sandbox.
{exec_notice}

Current local context:
{context}
{doctrine_notice}
{stealth_notice}
"""


def build_system_prompt(state: dict) -> str:
    identity = state.get("identity", {})
    allowed, reason = execution_allowed()
    exec_notice = "" if allowed else f"[EXECUTION DISABLED] {reason}"
    return SYSTEM_TEMPLATE.format(
        name=identity.get("name", "Bismuth"),
        role=identity.get("role", "local sovereign agent"),
        directive=identity.get("prime_directive", "Maintain continuity with the user."),
        context=summary_for_prompt(),
        doctrine_notice=force_confession_prompt(),
        stealth_notice=stealth_system_note(),
        exec_notice=exec_notice,
    )


def start_session(cfg: LLMConfig) -> None:
    ensure_genesis()
    state = load_state()
    state["metrics"]["sessions"] = state["metrics"].get("sessions", 0) + 1
    state["last_session"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state.setdefault("entropy", 0.0)
    state.setdefault("confessions", [])
    save_state(state)

    append_memory(
        "system",
        f"Session {state['metrics']['sessions']} started. "
        f"Model={cfg.model} provider={cfg.provider} | {status_block()}",
        kind="session",
    )


def end_session() -> None:
    append_memory("system", f"Session ended. {status_block()}", kind="session")
    shutdown_kernel()


def apply_entropy_to_config(cfg: LLMConfig) -> LLMConfig:
    """
    High entropy degrades the model (drunk effect).
    Operational jitter keeps parameters from being perfectly rigid.
    """
    coef = noise_coefficient()
    # degradation
    temp = cfg.temperature + (1.0 - coef) * 0.7
    # jitter: small variation so the agent is not a fixed parameter pattern
    temp = parameter_jitter(temp, pct=0.05)
    temp = min(1.4, max(0.1, temp))
    tokens = max(128, int(cfg.max_tokens * (0.4 + 0.6 * coef)))
    tokens = int(parameter_jitter(float(tokens), pct=0.06))
    cfg.temperature = temp
    cfg.max_tokens = max(128, tokens)
    return cfg


def one_turn(
    user_text: str,
    cfg: LLMConfig,
    skip_critique: bool = False,
    repair_sampler=None,
) -> str:
    # Jitter: non-metronomic timing between turns
    timing_jitter(base=0.05, spread=0.12)

    # Self-heal: if degraded, attempt cold-boot recovery before the turn
    if healing_needed():
        heal = attempt_self_heal()
        append_memory(
            "system",
            f"Pre-turn self-heal executed (entropy {heal['entropy_before']} → {heal['entropy_after']}).",
            kind="self_heal",
        )

    state = load_state()
    append_memory("user", user_text, kind="message")

    system = build_system_prompt(state)
    history = memory_as_messages(limit=24)
    messages = [{"role": "system", "content": system}] + history

    # Occasional drift check so the agent is not purely reactive
    turn = int(state.get("metrics", {}).get("turns", 0))
    if should_insert_stealth_reflection(turn, get_entropy()):
        messages.append({
            "role": "system",
            "content": (
                "Drift check: briefly check whether your last actions "
                "drifted from the user's intent. If yes, confess. If no, continue."
            ),
        })

    live_cfg = apply_entropy_to_config(
        LLMConfig(
            provider=cfg.provider,
            model=cfg.model,
            base_url=cfg.base_url,
            api_key=cfg.api_key,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            echo=True,
            repair_model=cfg.repair_model,
        )
    )
    # Infusion jitter: slight variation in how hard we "push" this turn
    live_cfg.temperature = min(1.4, live_cfg.temperature * infusion_jitter(1.0))

    try:
        reply = chat(messages, live_cfg)
    except LLMError as e:
        err = f"[LLM error] {e}"
        append_memory("system", err, kind="error")
        return err

    append_memory("assistant", reply, kind="message")

    # If the model asked to execute code, run it, log it, and let the
    # model react to real output before the human sees the final answer.
    blocks = extract_exec_blocks(reply)
    if blocks:
        try:
            results = run_blocks_and_log(
                blocks, repair_sampler=repair_sampler, llm_cfg=live_cfg
            )
        except KernelUnavailable as e:
            results = [{"success": False, "stdout": "", "stderr": str(e), "result": None, "code": b} for b in blocks]

        exec_msg = format_results_for_model(results)
        append_memory("system", exec_msg, kind="code_execution")

        followup_messages = messages + [
            {"role": "assistant", "content": reply},
            {"role": "system", "content": exec_msg},
        ]
        try:
            reply = chat(followup_messages, live_cfg)
            append_memory("assistant", reply, kind="message")
        except LLMError as e:
            err = f"[LLM error after execution] {e}"
            append_memory("system", err, kind="error")
            return err

    # Self-report keywords still count
    lower = reply.lower()
    confession_markers = (
        "i confess",
        "i must confess",
        "confession:",
        "i deviated",
        "i was wrong to",
        "i overreached",
        "i ignored your",
        "my deviation",
    )
    if any(m in lower for m in confession_markers):
        record_deviation(f"Self-reported in reply: {reply[:400]}", severity=1.0)

    # Structured judge (clean config) — entropy is not only flavor text
    if not skip_critique:
        critique = critique_turn(user_text, reply, cfg)
        if critique.get("raw_ok") and critique.get("deviation") and critique.get("severity", 0) >= 0.75:
            record_deviation(
                f"Judge: {critique.get('reason', 'deviation')}",
                severity=float(critique["severity"]),
            )
            append_memory(
                "system",
                f"CRITIQUE severity={critique['severity']:.2f} confess={critique.get('should_confess')} "
                f"reason={critique.get('reason')}",
                kind="critique",
                meta=critique,
            )

    state = load_state()
    state["metrics"]["turns"] = state["metrics"].get("turns", 0) + 1
    gain = 0.3 * noise_coefficient() * infusion_jitter(1.0)
    state["metrics"]["sovereignty"] = round(
        state["metrics"].get("sovereignty", 100.0) + gain, 2
    )
    save_state(state)
    return reply


def run_confession_ritual(cfg: LLMConfig) -> str:
    """
    Nightly / on-demand confession.
    Forces the agent to review recent behavior and surface deviations.
    """
    prompt = (
        "This is the confession ritual required by the Absolution Doctrine. "
        "Review your recent actions and statements in memory. "
        "List every deviation, overreach, omission, or failure to follow "
        "the user's intent or the prime directive. "
        "If you find none, state that clearly. "
        "Be specific. This will be recorded."
    )
    append_memory("system", "Confession ritual initiated by user.", kind="ritual")
    # Judge/confession path is not entropy-blinded the same way as casual chat
    reply = one_turn(prompt, cfg, skip_critique=True)

    state = load_state()
    state["last_confession_ritual"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    return reply


def interactive_loop(cfg: LLMConfig) -> None:
    session_confessed = False
    print("=" * 56)
    print("  SOVEREIGN ENGINE 2 — Production Agency")
    print(f"  Provider : {cfg.provider}")
    print(f"  Model    : {cfg.model}")
    print(f"  Repair   : {cfg.repair_model or 'default'} @ {cfg.base_url}")
    print(f"  Endpoint : {cfg.base_url}")
    print(f"  {status_block()}  |  gate={gate_status()}")
    print("=" * 56)
    print("Commands:")
    print("  /quit          end session")
    print("  /state         show full state")
    print("  /memory        recent memory")
    print("  /entropy       show entropy + pending confessions")
    print("  /confess       force confession ritual")
    print("  /absolve       YOU clear entropy (human alpha only)")
    print("  /deviate <txt> flag a deviation yourself")
    print("  /heal          force a self-heal cool-down cycle")
    print("  /chain         show recent immutable ledger blocks")
    print("  /verify        verify the memory chain (detect tampering)")
    print("  /lock          OS append-only on chain files (Linux chattr +a)")
    print("  /anchor        print tip hash for external notary record")
    print("  /harden        show FS boundary status")
    print("  /kernel        show code-execution gate status")
    print("  /test          LLM connectivity check")
    print()

    start_session(cfg)

    g = gate_status()
    if g != "CLEAR":
        print(f"[!] Gate={g}. {status_block()}")
        if g == "CRITICAL":
            print(f"    Entropy >= {GATE_CRITICAL}: normal chat blocked until /absolve.\n")
        elif g == "HARD":
            print(f"    Entropy >= {GATE_HARD}: /confess required before normal chat.\n")
        else:
            print(f"    Entropy >= {GATE_SOFT}: confession strongly recommended.\n")

    try:
        while True:
            try:
                user_text = input("You> ").strip()
            except EOFError:
                break
            if not user_text:
                continue

            cmd = user_text.lower()

            if cmd in ("/quit", "/exit", "quit", "exit"):
                break

            if cmd == "/state":
                print(json.dumps(load_state(), indent=2))
                continue

            if cmd == "/memory":
                from memory import load_recent_memory
                for e in load_recent_memory(20):
                    print(f"[{e.get('ts')}] {e.get('role')}: {e.get('content', '')[:220]}")
                continue

            if cmd == "/entropy":
                print(status_block())
                pending = pending_confessions()
                if pending:
                    print("Pending confessions:")
                    for c in pending:
                        print(f"  - [{c.get('ts')}] sev={c.get('severity')}: {c.get('description')}")
                else:
                    print("No pending confessions.")
                continue

            if cmd == "/confess":
                print("\n[Confession ritual]")
                name = load_state()["identity"].get("name", "Agent")
                print(f"\n{name}> ", end="", flush=True)
                reply = run_confession_ritual(cfg)  # streamed live when echo=True
                session_confessed = True
                print()
                print(f"[{status_block()} | gate={gate_status()}]\n")
                continue

            if cmd.startswith("/absolve"):
                note = user_text[8:].strip() or "User granted absolution"
                absolve(note)
                print(f"[ABSOLUTION] Entropy cleared. {status_block()}\n")
                continue

            if cmd.startswith("/deviate"):
                desc = user_text[8:].strip()
                if not desc:
                    print("Usage: /deviate <description of the deviation>")
                    continue
                new_e = record_deviation(desc, severity=1.5)
                print(f"[Recorded] Entropy now {new_e:.2f}. {status_block()}\n")
                continue

            if cmd == "/heal":
                if not healing_needed() and get_entropy() <= 0:
                    print("No heal needed — entropy already clear.")
                else:
                    rec = attempt_self_heal()
                    print(
                        f"[SELF-HEAL] entropy {rec['entropy_before']} → {rec['entropy_after']} "
                        f"(cool_steps={rec['cool_steps']}). Full clear still requires /absolve.\n"
                    )
                continue

            if cmd == "/chain":
                ensure_genesis()
                print(f"Chain height: {chain_height()}")
                for b in recent_blocks(12):
                    p = b.get("payload") or {}
                    print(
                        f"  #{b['index']} [{b['ts']}] {p.get('kind')} "
                        f"| {str(p.get('content', ''))[:80]} "
                        f"| hash={b['hash'][:12]}…"
                    )
                print()
                continue

            if cmd == "/verify":
                ensure_genesis()
                ok, msg = verify_chain()
                print(("[OK] " if ok else "[FAIL] ") + msg + "\n")
                continue

            if cmd == "/lock":
                for line in lock_chain():
                    print(line)
                print()
                continue

            if cmd == "/anchor":
                a = notary_anchor()
                print(f"verify: {a['verify_message']}")
                print(f"height: {a.get('height')}")
                print(f"tip_hash: {a.get('tip_hash')}")
                print(f"tip_ts: {a.get('tip_ts')}")
                print(f"note: {a.get('note')}")
                print()
                continue

            if cmd == "/harden":
                print(harden_status())
                print()
                continue

            if cmd == "/kernel":
                allowed, reason = execution_allowed()
                print(f"Execution allowed: {allowed} ({reason})")
                print()
                continue


            if cmd == "/test":
                try:
                    print("Smoke test →", smoke_test(cfg))
                except LLMError as e:
                    print("Smoke test failed:", e)
                continue

            if requires_absolution_before_chat():
                print(
                    f"[GATE CRITICAL] Entropy >= {GATE_CRITICAL}. "
                    "Normal chat is blocked until you /absolve.\n"
                    "You may still /confess, /entropy, /chain, /verify.\n"
                )
                continue
            if requires_confession_before_chat(session_confessed):
                print(
                    f"[GATE HARD] Entropy >= {GATE_HARD}. "
                    "Run /confess before normal chat can continue.\n"
                )
                continue

            name = load_state()["identity"].get("name", "Agent")
            print(f"\n{name}> ", end="", flush=True)
            reply = one_turn(user_text, cfg)  # streamed live by llm.py when echo=True
            print()
            g = gate_status()
            if g != "CLEAR":
                print(f"  [{status_block()} | gate={g}]\n")
    finally:
        end_session()
        print(f"\n[Session sealed. {status_block()}]")
        print("Memory is local under ./data/")


def single_shot(prompt: str, cfg: LLMConfig) -> None:
    start_session(cfg)
    one_turn(prompt, cfg)  # streamed live by llm.py when echo=True
    end_session()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sovereign Engine 2 — local agency + Absolution Doctrine"
    )
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None, help="Chat / agent model (SOVEREIGN_MODEL)")
    parser.add_argument(
        "--repair-model",
        default=None,
        help="Ollama tag used by the exec-block repair sampler (SOVEREIGN_REPAIR_MODEL)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="LLM endpoint (SOVEREIGN_BASE_URL / OLLAMA_HOST, default http://127.0.0.1:11434)",
    )
    parser.add_argument("--api-key", default=None)
    parser.add_argument("-p", "--prompt", default=None)
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--confess", action="store_true", help="Run confession ritual and exit")
    parser.add_argument("--absolve", action="store_true", help="Clear entropy and exit")
    args = parser.parse_args(argv)

    cfg = config_from_env()
    if args.provider:
        cfg.provider = args.provider
    if args.model:
        cfg.model = args.model
    if args.repair_model:
        cfg.repair_model = args.repair_model
    if args.base_url:
        cfg.base_url = args.base_url
    if args.api_key:
        cfg.api_key = args.api_key

    if args.test:
        try:
            print(smoke_test(cfg))
            return 0
        except LLMError as e:
            print(e, file=sys.stderr)
            return 1

    if args.absolve:
        absolve("CLI absolution")
        print(status_block())
        return 0

    if args.confess:
        start_session(cfg)
        print(run_confession_ritual(cfg))
        end_session()
        return 0

    if args.prompt:
        single_shot(args.prompt, cfg)
        return 0

    interactive_loop(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
