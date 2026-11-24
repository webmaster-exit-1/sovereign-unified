# Patches vs official SE2 (`/workspace/sovereign-integration/se2_user`)

Legacy copies of `kernel.py`, `kernel_2.py`, and `sovereign_engine.py` are byte-identical to official SE2 and are not imported by the package.

## Module changes


### agent.py
- Package-relative imports (`from .llm import ...`, `from .kernel import ...`).
- `one_turn(..., repair_sampler=None)` runs exec blocks through `run_blocks_and_log(..., repair_sampler, llm_cfg=live_cfg)`, so failures go through the single kernel + repair loop.
- `live_cfg` keeps `echo=True` for user-facing turns and copies `repair_model`.
- Confession / chat / single-shot no longer reprint the full model reply after `llm.chat` already streamed it (echo/double-print fix).
- CLI: `--repair-model` (and help text for `--model` / `--base-url`). Banner prints the repair tag and endpoint.

### memory.py
- Package-relative imports; `set_data_dir()` delegates to `sovereign.paths` instead of a module-global `DATA_DIR = Path("data")`.
- Goal helpers: `list_goals`, `add_goal`, `get_goal`, `update_goal` with dict `{goal_id, description, status, code}` and backward-compatible strings.

### ledger.py
- Package-relative imports; file paths come from `sovereign.paths` (`set_data_dir()`).

### llm.py
- `import time` at module top (was missing / implicit).
- `LLMConfig.echo` defaults to `False`; Ollama stream printing and the trailing newline run only when `echo=True` (fixes agent double-print).
- `DEFAULT_REPAIR_MODEL = chatgpt1/qwythos-9b-claude-mythos-5-1m-abliterated:latest`.
- `SOVEREIGN_REPAIR_MODEL`, `OLLAMA_HOST`, `SOVEREIGN_BASE_URL` via `config_from_env()`, `default_base_url()`, `repair_config()`.
- Chat model default remains `llama3.2` (`SOVEREIGN_MODEL`).

### entropy.py, critique.py, stealth.py, audit_manager.py
- Package-relative imports only. No behavior change.

### harden.py
- Package-relative imports; chain/lock file paths go through `sovereign.paths`.

### visualize.py
- Uses `paths.data_dir()`; skips non-numeric production ledger rows (`index,ts,kind,content,hash,prev_hash`).

### resonance.py
- Uses `paths.data_dir()`; prefers `sovereign_ledger.csv` then `ledger.csv`; skips non-numeric rows so the production ledger does not raise.

### kernel.py / kernel_2.py
- Not in the package. Official unmodified copies are in `legacy/`. Production executor is new `sovereign/kernel.py` + `sovereign/sandbox_launch.py`.

### test_sandbox.py
- Rewritten as unittest against the unified `KernelSession` (bwrap probes always; seccomp probe skipped when `pyseccomp` is missing).
