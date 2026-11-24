"""Protocol tests for the test-only HandoffRepairSampler. Not a model."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from demos.handoff_sampler import HandoffRepairSampler
from sovereign.kernel import ExecutionResult
from sovereign.repl.errors import extract_error_context
from sovereign.repl.guidance import RepairRequest
from sovereign.repl.quantum import QuantumTwinEvaluator


def _request(code: str = "prin('x')") -> RepairRequest:
    result = ExecutionResult(
        status="error",
        error={
            "ename": "NameError",
            "evalue": "name 'prin' is not defined",
            "traceback": ['  File "<cell>", line 1, in <module>\n'],
        },
    )
    failure = extract_error_context(code, result.error, result.stderr)
    evaluator = QuantumTwinEvaluator()
    state = evaluator.twin_state(f"{failure.exception_type}\n{failure.message}")
    return RepairRequest(
        code=code,
        failure=failure,
        guidance=state,
        entropy=float(evaluator.entropy(state)),
    )


class HandoffSamplerProtocolTests(unittest.TestCase):
    def test_writes_request_and_reads_response(self):
        with tempfile.TemporaryDirectory() as root:
            sampler = HandoffRepairSampler(root, timeout=5.0, poll_interval=0.05)
            sampler.set_goal("demo", "Print x")

            def reply():
                handoff = Path(root)
                deadline = time.time() + 5
                req = None
                while time.time() < deadline:
                    found = list(handoff.glob("*.request.json"))
                    if found:
                        req = found[0]
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(req)
                payload = json.loads(req.read_text(encoding="utf-8"))
                self.assertEqual(payload["goal_id"], "demo")
                self.assertEqual(payload["error_type"], "NameError")
                self.assertIn("quantum_entropy", payload)
                resp = req.with_name(req.name.replace(".request.json", ".response.json"))
                resp.write_text(json.dumps({"code": "print('x')"}), encoding="utf-8")

            thread = threading.Thread(target=reply)
            thread.start()
            try:
                revised = sampler.repair(_request())
            finally:
                thread.join(timeout=5)
            self.assertEqual(revised, "print('x')")
            self.assertTrue(sampler.applies_full_source)

    def test_timeout_without_response(self):
        with tempfile.TemporaryDirectory() as root:
            sampler = HandoffRepairSampler(root, timeout=0.3, poll_interval=0.05)
            with self.assertRaises(TimeoutError):
                sampler.repair(_request())


class FullSourceLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_source_survives_repair_loop_wrapper(self):
        """A complete program must replace the candidate, not be spliced into one line."""
        import tempfile
        from sovereign.memory import set_data_dir
        from sovereign.repair import run_repair_loop

        class FakeKernel:
            def __init__(self):
                self.calls = []

            async def execute(self, code, *, timeout=60.0):
                self.calls.append(code)
                if "prin(" in code or "def word_count" in code and "counts[word] += 1" in code:
                    return ExecutionResult(
                        status="error",
                        error={
                            "ename": "NameError" if "prin(" in code else "KeyError",
                            "evalue": "boom",
                            "traceback": ['  File "<cell>", line 1, in <module>\n'],
                        },
                    )
                return ExecutionResult(status="ok", stdout="ok\n")

        broken = "def word_count(text):\n    counts = {}\n    for word in text.split():\n        counts[word] += 1\n    return counts\n"
        fixed = "def word_count(text):\n    counts = {}\n    for word in text.split():\n        counts[word] = counts.get(word, 0) + 1\n    return counts\nprint('counts-ok')\n"

        with tempfile.TemporaryDirectory() as root:
            set_data_dir(root)
            sampler = HandoffRepairSampler(root, timeout=5.0, poll_interval=0.05)

            def reply():
                handoff = Path(root)
                deadline = time.time() + 5
                req = None
                while time.time() < deadline:
                    found = list(handoff.glob("*.request.json"))
                    if found:
                        req = found[0]
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(req)
                resp = req.with_name(req.name.replace(".request.json", ".response.json"))
                resp.write_text(json.dumps({"code": fixed}), encoding="utf-8")

            thread = threading.Thread(target=reply)
            thread.start()
            try:
                result = await run_repair_loop(
                    broken, sampler, kernel=FakeKernel(), max_attempts=3
                )
            finally:
                thread.join(timeout=5)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["code"], fixed)
        self.assertNotIn("counts[word] += 1", result["code"])
