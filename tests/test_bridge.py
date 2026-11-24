import csv
import json
import tempfile
import unittest
from pathlib import Path

from sovereign.kernel import ExecutionResult
from sovereign.memory import add_goal, get_goal, list_goals, load_all_memory, load_state, save_state
from sovereign.paths import set_data_dir
from sovereign.repair import MappingRepairSampler, SovereignREPLBridge
from sovereign.entropy import get_entropy, record_deviation
from sovereign.ledger import verify_chain, GENESIS_HASH
from sovereign.repl.quantum import QuantumTwinEvaluator


class FakeKernel:
    def __init__(self):
        self.calls = []

    async def execute(self, code, *, timeout=60.0):
        self.calls.append(code)
        if "prin(" in code or code.strip() == "bad":
            return ExecutionResult(
                status="error",
                error={
                    "ename": "NameError",
                    "evalue": "name 'prin' is not defined",
                    "traceback": ['  File "<cell>", line 1, in <module>\n'],
                },
            )
        return ExecutionResult(status="ok", stdout="RESULT=4\n")


class MemoryAndGoalsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        set_data_dir(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_memory_and_ledger_schemas_and_chain(self):
        from sovereign.memory import append_memory
        append_memory("user", "hello", kind="message")
        ok, message = verify_chain()
        self.assertTrue(ok, message)
        records = [
            json.loads(line)
            for line in Path(self._tmp.name, "memory.jsonl").read_text().splitlines()
            if line.strip()
        ]
        self.assertEqual(records[0]["content"], "hello")
        with open(Path(self._tmp.name) / "ledger.csv", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], ["index", "ts", "kind", "content", "hash", "prev_hash"])
        self.assertEqual(rows[1][2], "genesis")
        self.assertEqual(rows[1][5], GENESIS_HASH)

    def test_goals_dicts_and_string_compat(self):
        goal = add_goal("Print RESULT=4", goal_id="sum-2-2", code="print(2+2)")
        self.assertEqual(goal.goal_id, "sum-2-2")
        self.assertEqual(get_goal("sum-2-2").code, "print(2+2)")
        state = load_state()
        state["goals"].append("plain string goal")
        save_state(state)
        descriptions = [g.description for g in list_goals()]
        self.assertIn("Print RESULT=4", descriptions)
        self.assertIn("plain string goal", descriptions)

    def test_governance_entropy_is_not_quantum_entropy(self):
        self.assertEqual(get_entropy(), 0.0)
        record_deviation("test lie", severity=1.5)
        self.assertEqual(get_entropy(), 1.5)
        quantum = QuantumTwinEvaluator().entropy(
            QuantumTwinEvaluator().twin_state("NameError\nname 'prin' is not defined")
        )
        self.assertGreaterEqual(quantum, 0.0)
        self.assertLessEqual(quantum, 2.0)
        self.assertNotEqual(quantum, get_entropy())


class BridgeLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_goal_failure_revision_success(self):
        with tempfile.TemporaryDirectory() as root:
            set_data_dir(root)
            broken = "prin('RESULT=' + str(2 + 2))"
            fixed = "print('RESULT=' + str(2 + 2))"
            goal = add_goal("Evaluate 2+2 and print RESULT=4", goal_id="sum-2-2", code=broken)
            result = await SovereignREPLBridge(
                FakeKernel(), MappingRepairSampler({broken: fixed})
            ).run_goal(goal, max_attempts=3)
            self.assertTrue(result["success"])
            self.assertTrue(result["corrected"])
            self.assertEqual(result["attempts"], 2)
            self.assertIn("RESULT=4", result["stdout"])
            self.assertEqual(result["goal"].status, "completed")
            self.assertTrue(result["chain_ok"], result["chain_message"])
            kinds = [entry["kind"] for entry in load_all_memory()]
            self.assertIn("goal", kinds)
            self.assertIn("code_execution", kinds)
            self.assertIn("code_revision", kinds)
            failed = [
                entry for entry in load_all_memory()
                if entry["kind"] == "code_execution" and entry.get("meta", {}).get("quantum_entropy") is not None
            ]
            self.assertTrue(failed)
            self.assertEqual(get_entropy(), 0.0)


class LLMEchoTests(unittest.TestCase):
    def test_default_echo_is_false(self):
        from sovereign.llm import LLMConfig
        self.assertFalse(LLMConfig().echo)
