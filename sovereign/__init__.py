"""Sovereign Engine: local continuity, ledger, and a sandboxed REPL repair loop."""

from .goals import Goal
from .kernel import (
    ExecutionResult,
    KernelSession,
    KernelUnavailable,
    SandboxProfile,
    get_kernel,
    shutdown_kernel,
)
from .memory import (
    add_goal,
    append_memory,
    get_goal,
    list_goals,
    load_state,
    set_data_dir,
    update_goal,
)
from .paths import set_data_dir as configure_data_dir
from .repair import MappingRepairSampler, SovereignREPLBridge

__all__ = [
    "ExecutionResult",
    "Goal",
    "KernelSession",
    "KernelUnavailable",
    "MappingRepairSampler",
    "SandboxProfile",
    "SovereignREPLBridge",
    "add_goal",
    "append_memory",
    "configure_data_dir",
    "get_goal",
    "get_kernel",
    "list_goals",
    "load_state",
    "set_data_dir",
    "shutdown_kernel",
    "update_goal",
]
