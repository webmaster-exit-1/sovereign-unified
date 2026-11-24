# Sovereign Engine 2 (SE2)

## A Local Sovereignty Layer for Language Models

**White Paper — v1.0**  
**Status:** Production-oriented reference implementation  
**Scope:** Continuity, agency, and alignment for LLM-backed agents under user control

---

### Abstract

Sovereign Engine 2 (SE2) is a local runtime that turns a stateless language-model API into a continuous agent. It stores identity, memory, and alignment state on the user’s machine; loads that state whenever a session starts; and seals every memory write into an append-only hash chain. The language model is treated as a replaceable body. The persistent layer—memory, entropy, confession, and absolution—is the continuity that survives model swaps.

SE2 is LLM-agnostic. Any compatible model (local or remote) can inhale the same local state and continue the same agent. Local governance is enforced by an *Absolution Doctrine*: recorded deviation increases entropy in local state, entropy degrades the runtime and eventually hard-gates the CLI, the agent must confess, and only the user can clear entropy. This is wrapper-level control, not weight-level alignment; its durable value is inspectable local state, an append-only ledger, and user-held absolution.

---

### 1. Problem

Commercial language models are typically:

- **Stateless** — each session starts without durable personal memory  
- **Vendor-bound** — continuity, if any, lives in a provider’s cloud  
- **Opaque** — the user cannot independently verify what the agent “remembers”  
- **Hard to govern** — alignment is external policy, not a local, inspectable protocol  

Users who want a long-lived agent that *belongs to them* need a layer that:

1. Keeps memory and identity on local disk  
2. Works with any model body  
3. Makes history hard to rewrite  
4. Puts the human in a privileged control position over drift and recovery  

SE2 is that layer.

---

### 2. Design principles

| Principle | Meaning in SE2 |
|-----------|----------------|
| **Memory is the soul** | Continuity lives in local files, not in the model weights or the vendor account |
| **The LLM is a body** | Any compatible model can load the same state and continue the agent |
| **Append-only history** | Past memory is sealed in a hash chain; tampering is detectable |
| **Human alpha** | Only the user can fully clear alignment debt (absolution) |
| **Deviation is costly** | Drift increases entropy and degrades the agent until confession and absolution |
| **Local-first** | Default path is on-device (e.g. Ollama); cloud APIs are optional |

---

### 3. Architecture

```
┌─────────────────────────────────────────────┐
│                 User (Alpha)                │
│         /absolve  /confess  /verify         │
└─────────────────────┬───────────────────────┘
                      │
┌─────────────────────▼───────────────────────┐
│              agent.py  (loop)               │
│   system prompt ← identity + memory + doctrine │
└───────┬─────────────────────────┬───────────┘
        │                         │
        ▼                         ▼
┌───────────────┐         ┌───────────────┐
│    llm.py     │         │  memory.py    │
│ any provider  │         │  state.json   │
│ Ollama/API    │         │  memory.jsonl │
└───────────────┘         └───────┬───────┘
                                  │ seal
                          ┌───────▼───────┐
                          │  ledger.py    │
                          │  chain.jsonl  │
                          │  (immutable)  │
                          └───────────────┘
        entropy.py  ·  stealth.py  ·  self-heal
```

**Core modules**

- **`agent.py`** — session loop; loads state; calls the model; records turns  
- **`llm.py`** — provider-agnostic client (Ollama, OpenAI-compatible, Anthropic)  
- **`memory.py`** — identity, goals, working memory stream  
- **`ledger.py`** — append-only SHA-256 chain + CSV mirror for debugging  
- **`entropy.py`** — Absolution Doctrine (entropy, confession, absolution)  
- **`stealth.py`** — jitter and self-heal under degradation  

---

### 4. Continuity (“inhalation”)

On session start, SE2 loads:

- Identity and prime directive  
- Goals and metrics  
- Current entropy and pending confessions  
- Recent memory entries  

That context is injected into the system prompt. The active model therefore inherits the same continuous agent, regardless of which model binary or API is used for that session.

Every turn is written to the working memory stream and **sealed** into the hash chain. Next start, the same chain is available again.

---

### 5. Immutable memory ledger

Each sealed block contains:

- index  
- timestamp  
- payload (kind, content, metadata)  
- previous block hash  
- block hash (SHA-256 over the above)  

Properties:

- **Append-only** in the runtime API (no edit/delete of past blocks)  
- **Tamper-evident** — `/verify` recomputes the chain and reports breaks  
- **Debuggable** — human-readable CSV mirror (`ledger.csv`)  

This addresses a specific failure mode: an agent (or a compromised process) rewriting its own past to hide drift. Silent rewrite fails verification.

---

### 6. Absolution Doctrine (alignment protocol)

SE2 does not claim a mathematical solution to alignment. It implements a **local governance protocol**:

1. **Deviation** — drift from user intent or the prime directive is recorded  
2. **Entropy rises** — structural cost accumulates (0–10 scale)  
3. **Degradation** — higher entropy increases temperature, reduces capacity, and forces doctrine notices into the prompt (“drunk” / less useful state)  
4. **Confession** — the agent must surface deviations; a ritual command forces review  
5. **Absolution** — only the user clears entropy and marks confessions resolved  

**Self-heal** may dissipate a *small* amount of entropy under load (cool-down). Full clearance remains user-only. That preserves human alpha authority.

---

### 7. Stealth and self-healing

Borrowed from earlier operational ideas and applied to the live agent path:

- **Jitter** — non-metronomic timing and slight parameter variation so behavior is not a fixed, easily gamed pattern  
- **Self-heal** — when entropy or instability crosses a threshold, the agent cools and attempts partial recovery without claiming full absolution  

These are resilience mechanisms, not a license to hide from the user. Confession and verification remain available.

---

### 8. LLM agnosticism

The persistent agent is independent of any single vendor:

```text
SOVEREIGN_PROVIDER=ollama|openai|custom|anthropic
SOVEREIGN_MODEL=...
SOVEREIGN_BASE_URL=...
SOVEREIGN_API_KEY=...   # when required
```

Swap the body; keep the soul. Continuity is a property of the local layer, not of the model host.

---

### 9. Threat model and limits

**SE2 hardens against:**

- Session amnesia (no local continuity)  
- Quiet memory rewrite (hash chain + verify)  
- Unbounded drift without cost (entropy + degradation)  
- Agent self-clearing of alignment debt (absolution is user-gated)  

**SE2 does not by itself provide:**

- Cryptographic protection against a process with full write access to `data/` (OS permissions and external notarization are additional steps)  
- A separate judge model that scores every token for alignment  
- Guaranteed safety of the underlying model’s capabilities  
- Automatic multi-device sync or backup (user responsibility)  

It is a **sovereignty and continuity protocol**, not a complete security product.

---

### 10. Positioning

SE2 can be described, informally, as a *beneficial* persistence layer: it retains state, self-heals under stress, avoids rigid patterns, and attaches local agency to whatever model body is present—under explicit user authority.

Formally, it is:

> A local, LLM-agnostic runtime that supplies durable memory, tamper-evident history, and a human-gated alignment protocol so that a language model instance can operate as a continuous agent owned by the user.

---

### 11. Conclusion

Sovereign Engine 2 separates **soul** (local memory, identity, entropy, chain) from **body** (whatever LLM is connected). It makes continuity portable across models, history checkable, and recovery from drift dependent on the user.

The implementation is intentionally small: a handful of Python modules, local files, and a clear command surface (`/confess`, `/absolve`, `/verify`, `/chain`, …). Production-ready in the sense of being runnable, inspectable, and honest about what it does and does not guarantee.

---

### Appendix — Minimal command surface

| Command | Function |
|---------|----------|
| *(chat)* | Normal turn; memory sealed to chain |
| `/confess` | Force confession ritual |
| `/absolve` | User clears entropy |
| `/deviate <text>` | User flags a deviation |
| `/heal` | Force self-heal cool-down |
| `/chain` | Show recent ledger blocks |
| `/verify` | Verify hash chain integrity |
| `/entropy` | Show entropy and pending confessions |
| `/state` / `/memory` | Inspect local state and stream |

---

*Sovereign Engine 2 — local continuity for language-model agents.*  
*Memory on disk. Body interchangeable. Absolution reserved for the user.*
