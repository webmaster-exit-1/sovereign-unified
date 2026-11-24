# Sovereign Engine (unified)

This tree is a single package: **Sovereign Engine 2** (local memory, hash-chained ledger, agent loop) plus the **Quantum Diffusion REPL** repair engine, sharing one sandboxed execution kernel.

Official SE2 documents from the author live under [`docs/`](docs/) (`README.md`, `WHITEPAPER.md`, `ORIGIN.md`, `SE2_White_Paper.pdf`). This file describes the unified layout and how to run it.

## Layout

```
unified/
  sovereign/                 one installable package
    paths.py                 set_data_dir() / data file helpers
    memory.py, ledger.py, entropy.py, llm.py, agent.py
    goals.py                 dict + string-compatible goals
    audit_manager.py, critique.py, harden.py, resonance.py, stealth.py, visualize.py
    kernel.py                the only execution path
    sandbox_launch.py        bwrap + rlimits + optional seccomp
    repair.py                REPL feedback loop + ledger recording
    repl/                    quantum diffusion modules (canvas, engine, samplers, ...)
  tests/                     REPL suite + repair/ledger tests + sandbox probes
  demos/demo_goal_loop.py    offline fail → revise → success demo
  demos/handoff_sampler.py   test-only file-handoff sampler (not the default)
  demos/demo_handoff_repair.py  live handoff demo against the real kernel
  docs/                      official SE2 docs and white paper
  legacy/                    reference-only copies (not imported)
    sovereign_engine.py      older TCP/swarm simulation
    kernel.py, kernel_2.py   earlier SE2 kernels
  patches/                   local diffs against official SE2 sources
  pyproject.toml
```

There is no vendor/ or bridge package on the import path. Use `sovereign.*` only.

## Install and run

Python 3.10+ is required. A venv with kernel extras:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
# optional: pip install -e ".[seccomp]"   # pyseccomp filter
# optional: pip install -e ".[diffusion]" # torch/transformers sampler
```

Runtime extras the kernel uses:

- `bwrap` (bubblewrap) for the default sandbox. Without it, pass `use_bubblewrap=False` or the demo's `--no-bwrap`.
- `pyseccomp` is optional. If it is missing, the kernel starts without a seccomp filter and prints a warning.

### Agent REPL

```bash
sovereign
# or: python -m sovereign.agent
```

Data files (`memory.jsonl`, `state.json`, `chain.jsonl`, `ledger.csv`) are written under `./data` by default. Point them elsewhere with:

```python
from sovereign import set_data_dir
set_data_dir("/path/to/data")
```

Do not patch `DATA_DIR` globals; every module reads `sovereign.paths`.

### Tests

```bash
pip install pytest
PYTHONPATH=. pytest -q
```

### End-to-end demo (real kernel, bubblewrap)

```bash
PYTHONPATH=. python demos/demo_goal_loop.py --data-dir /tmp/sovereign-demo
# unsandboxed fallback:
PYTHONPATH=. python demos/demo_goal_loop.py --no-bwrap
```

The demo starts with a typo (`prin(...)`), records the failure and a revision in the SE2 ledger, then succeeds with `print('RESULT=' + str(2 + 2))`. It uses a deterministic `MappingRepairSampler`, so no LLM is required. Shannon (quantum) entropy is stored only in memory/ledger metadata, never in `state["entropy"]`.

## Repair model (Ollama)

The default repair sampler is **Ollama-backed** through `sovereign.llm.chat`. Both `agent.one_turn` exec-block repair and `SovereignREPLBridge` / `run_repair_loop` use it unless you pass a sampler explicitly.

Default repair tag:

```
chatgpt1/qwythos-9b-claude-mythos-5-1m-abliterated:latest
```

Endpoint default: `http://127.0.0.1:11434`.

Switch the tag or host without editing code:

```bash
# repair loop only (chat model stays SOVEREIGN_MODEL / --model)
export SOVEREIGN_REPAIR_MODEL=mistral:latest

# Ollama's standard host variable, used when SOVEREIGN_BASE_URL is unset
export OLLAMA_HOST=127.0.0.1:11434

# or a full URL, which wins over OLLAMA_HOST
export SOVEREIGN_BASE_URL=http://127.0.0.1:11434

# CLI
sovereign --repair-model mistral:latest --base-url http://127.0.0.1:11434
```

`SOVEREIGN_MODEL` / `--model` still selects the *chat* model (default `llama3.2`). The repair tag is independent so you can keep a different model for revision.

If Ollama is not running, the host is unreachable, or the chat call fails, the sampler falls back to the offline `MappingRepairSampler` and logs a clear warning (`Ollama repair sampler unavailable ... falling back to the offline sampler`). The offline sampler is not the default.

The end-to-end demo passes an explicit `MappingRepairSampler` so it can run without a model.

`demos/handoff_sampler.py` / `demos/demo_handoff_repair.py` are **test-only**.
They write a request JSON and wait for a response file so an operator can
stand in as the model. They are not imported by `default_repair_sampler()`.

```bash
PYTHONPATH=. python demos/demo_handoff_repair.py \
  --data-dir /tmp/sovereign-handoff-demo \
  --handoff-dir /tmp/sovereign-handoff-demo/handoff
```


## Single kernel


The only executor is `sovereign.kernel.KernelSession` (and the sync facade `get_kernel()` / `execute_code()` / `run_blocks_and_log()` used by `agent.one_turn`).

It keeps the REPL wrapper's async `KernelSession` API (already covered by the test suite) and folds in SE2 `kernel_2.py` sandbox features:

- bubblewrap with `--ro-bind /`, writable workspace, tmpfs `/tmp`, `--dev`, `--proc`
- `--unshare-pid --unshare-uts --unshare-ipc`, optional `--unshare-cgroup`
- `--unshare-net` unless `allow_network=True`
- `--die-with-parent --new-session`
- official rlimits: 1 GiB address space, 30s CPU, 256 file descriptors, 64 processes
- optional seccomp (degrades if `pyseccomp` is absent)
- Jupyter IPC sockets written under the workspace bind-mount (`ip=workspace/kernel`)

SE2 `kernel.py` (unsandboxed) and `kernel_2.py` are kept under `legacy/` for reference and are not importable from the package.

`agent.py` exec blocks and the REPL feedback loop both go through this kernel.

### Rlimit notes (not loosened)

- `RLIMIT_CPU=30` is a **process-lifetime** CPU-time cap, not a per-cell timeout. A long-lived kernel will eventually get `SIGXCPU` even if each cell is short. Cell timeouts are enforced separately by `KernelSession.execute(..., timeout=...)`.
- `RLIMIT_NPROC=64` is **per-UID**, not per-sandbox. If the launching user already has 64 or more tasks, applying this cap would make `bwrap` fail with `EAGAIN`. The launcher skips *only* `NPROC` in that situation, logs a warning, and leaves the official 64-process cap unchanged in the source. It does not raise the limit.

`JPY_PARENT_PID` is cleared inside the sandbox. After `--unshare-pid` the host manager PID is not visible, so ipykernel's parent poller would otherwise shut the kernel down immediately. `--die-with-parent` still kills the sandbox if the manager exits.

## Entropy (two different numbers)

- **Governance entropy** (`sovereign.entropy`, `state["entropy"]`, 0–10): SE2 Absolution Doctrine. Only this value lives in `state["entropy"]`.
- **Quantum / Shannon entropy** (0–2 bits of a 4-amplitude error-state vector): used by the repair loop as guidance and recorded in ledger/memory *metadata* (`meta.quantum_entropy`). It is never written to `state["entropy"]`.

## Goals

Goals may be strings (SE2's original `state["goals"]` list) or dicts `{goal_id, description, status, code}`. `list_goals()` / `add_goal()` / `update_goal()` accept both.

When a model is configured and reachable, failed exec blocks in `one_turn` are revised by `LLMRepairSampler` (`llm.chat`, `echo=False`). Otherwise the loop falls back to the offline sampler.

## License

The two upstream trees shipped their own license files: `LICENSE-Sovereign_Engine` and `LICENSE-quantum-diffusion`. Official SE2 sources included no license file; treat them according to the author's terms.
