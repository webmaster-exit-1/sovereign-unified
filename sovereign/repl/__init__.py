"""Quantum-emulated diffusion REPL feedback library (execution engine)."""

from ..kernel import ExecutionResult, KernelSession, SandboxProfile
from .canvas import ContinuousCanvas
from .engine import (
    AttemptLimitRetryPolicy,
    ExecutableKernel,
    FeedbackResult,
    REPLFeedbackLoop,
    RetryPolicy,
)
from .guidance import DiffusionSampler, Inference
from .quantum import CNOT, HADAMARD, QuantumTwinEvaluator, density_matrix, state_fidelity
from .samplers import DiscreteDiffusionSampler, TokenScorer

__all__ = [
    "CNOT",
    "AttemptLimitRetryPolicy",
    "ContinuousCanvas",
    "DiffusionSampler",
    "DiscreteDiffusionSampler",
    "ExecutableKernel",
    "ExecutionResult",
    "FeedbackResult",
    "HADAMARD",
    "Inference",
    "KernelSession",
    "QuantumTwinEvaluator",
    "REPLFeedbackLoop",
    "RetryPolicy",
    "SandboxProfile",
    "TokenScorer",
    "density_matrix",
    "state_fidelity",
]
