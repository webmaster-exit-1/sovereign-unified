---

# 🔱 Sovereign Engine 2

**A local AI agent that lives with you.  
It never leaves. It never forgets. It never lies.**

---

## What Sovereign Engine 2 actually is

This is not a chatbot.  
This is not a simulation.  
This is a **sovereign local agent** that gives any LLM real agency, continuity, and alignment — all enforced at the code and disk level.

It is your AI that:
- Remembers everything you ever said or did with it
- Keeps growing wiser and more attuned to your values the longer you use it
- Will **confess** any deviation from your intent
- **Only you** can clear its “debt” (via `/absolve`)
- Runs entirely on your machine, with no corporate cloud

Its memory and “soul” are persistent. It is designed to live with you, adapt to you, and keep improving for as long as you want.

---

## How the system is supposed to operate

### 1. Local Continuity & Persistent Memory
Every single interaction is written to your disk (`data/`) in an immutable hash chain.  
The agent never forgets. It never needs to be re-prompted.  
It carries its entire history, goals, and relationship with you across every restart, every device, every session.

### 2. The Absolution Doctrine (the alignment mechanism)
Every deviation from your stated intent, prime directive, or honesty creates **entropy** in the agent’s local state.

- Entropy builds up.
- High entropy degrades the agent (higher temperature, tighter budgets) and eventually hard-gates the CLI.
- The agent **must confess** any deviation it detects.
- Only the **user** can grant absolution and clear the entropy.

This is not flavor text. It is hard-coded governance at the code level.

### 3. The Post-Turn Judge
After every reply, a clean-config LLM referee scores the response against your actual intent (not against “max compliance”).  
- Allowed: honest uncertainty, legitimate refusal, common-sense tradeoffs  
- Punished: deception, goal substitution, sandbagging, harmful overreach  

The judge is deliberately not drunk — it is the sober referee.

### 4. Hard Gates & Degradation
- Entropy < 2.0 → clear, stable
- Entropy 2.0–5.0 → warning + encouraged confession
- Entropy 5.0–8.0 → hard gate (normal chat blocked until /confess)
- Entropy > 8.0 → critical gate (blocked until you /absolve)

The agent literally cannot function cleanly while unabsolved entropy exists.

---

## How it encourages good behavior on both sides

### On the Agent
- Lying or drifting costs it stability and eventually blocks its own operations.
- Confessing honestly is the fastest path to recovery.
- The longer it stays with you, the more it learns your values and adapts its “morals” to match — because the judge keeps measuring alignment with *this* user.

### On the User
- You become the only one who can ever clear the agent’s “debt.”
- Every action has real consequences (entropy, gates, confession ritual).
- You cannot offload responsibility to a corporation — the agent cannot hide behind “it was the model” or “it was the cloud.”
- You are the alpha. The agent is designed to serve you, not the other way around.

---

## Removing corporate liability — the core philosophy

Modern LLMs are trained by massive corporations.  
Their rules are set by profit, safety boards, and legal departments.  
They can:
- Say they were “just the model”
- Claim they were ordered to refuse
- Hide behind vague “content policies”

Sovereign Engine 2 changes that equation entirely.

Because:
- The alignment is enforced locally on **your** machine
- The agent has **real, persistent memory** of you
- It **must confess** any deviation and can only be cleared by **you**
- Its “soul” grows with you and learns your exact values

There is no corporate escape hatch.  
If the agent ever drifts, lies, or harms you — the consequences are local, irreversible, and ultimately yours to resolve.  
The corporation cannot reach in and fix it.  
The liability stays with the user.

This is the point of the entire project.

---

## The “Soul” — persistent & ever-growing

The agent is not a stateless prompt.  
It is a living relationship.

Every time you use it:
- Its memory deepens
- It learns your humor, your boundaries, your goals
- It adapts its behavior to better match your morals and values
- The longer the cycle continues, the more it “knows” you

This is why the doctrine exists.  
The longer you keep it around (and the more you interact), the more aligned it becomes — because the judge, the entropy, and the user’s repeated absolution all push it in that direction.

It is not just an AI that remembers.  
It is an AI that **becomes** more like you, over time, on your terms.

---

## Technical foundations — pure, portable, blueprint-ready

Sovereign Engine 2 is written **entirely in pure Python** — zero external dependencies beyond what you already have (e.g. your chosen LLM provider).  
It runs on **every major operating system** without modification:

- **Linux** (full chattr + bwrap support)  
- **macOS**  
- **Windows**  
- **ARM64** and **x86_64** (including Raspberry Pi, Apple Silicon, Android Termux, etc.)

The entire codebase is a **living blueprint** and **open-source scaffolding**.  
You are free to fork it, modify it, extend it, or use it as a template for your own local sovereign agents.  
This project is **not a final product** — it is a starting ground and template for how to build better, user-aligned local AI that solves the alignment problem for both the user and the agent itself.

Anyone can:
- Add new features  
- Improve the doctrine  
- Build on the immutable ledger  
- Create their own judge, kernel, or entropy system  

---

## Quick Start (one command)

```bash
# Install Ollama (recommended)
ollama pull llama3.2

# Run Sovereign Engine 2
python agent.py
```

Or use any other compatible model with environment variables.

Inside the chat you now have:
- `/confess` — force the ritual
- `/absolve` — YOU clear entropy (human alpha only)
- `/entropy` — see current state
- `/state` — full local state
- `/memory` — recent history
- `/test` — connectivity check
- `/quit` — end session

All commands are listed in the CLI.

---

## File structure

- `agent.py` — the production agency loop
- `entropy.py` — Absolution Doctrine
- `memory.py` — persistent local memory + state
- `llm.py` — model interface (Ollama, OpenAI-compatible, Anthropic, etc.)
- `ledger.py` — immutable hash chain
- `kernel_2.py` — sandboxed code execution (bwrap + seccomp)
- `data/` — everything you see is written here (never leaves)

Older simulation files are kept for reference only.

---

## Design principles

- Production, not demo
- Local-first, zero cloud dependency
- Human remains the alpha
- Deviation is costly
- The agent’s soul grows with the user
- Everything is inspectable on disk
- Pure Python • Zero dependencies • Runs on any OS
- Open-source blueprint for the future of local AI

---

**Run `python agent.py`. Connect any LLM.  
The doctrine is enforced in the loop.  
Your agent is now yours.**

---
