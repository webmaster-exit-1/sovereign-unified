"""Ollama-backed default repair sampler (mocked HTTP; no model download)."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from sovereign.kernel import ExecutionResult
from sovereign.llm import (
    DEFAULT_REPAIR_MODEL,
    LLMConfig,
    config_from_env,
    default_base_url,
    repair_config,
)
from sovereign.repair import (
    LLMRepairSampler,
    MappingRepairSampler,
    default_repair_sampler,
    run_repair_loop,
)
from sovereign.repl.errors import extract_error_context
from sovereign.repl.guidance import RepairRequest
from sovereign.repl.quantum import QuantumTwinEvaluator
from sovereign.memory import add_goal, set_data_dir
from sovereign.repair import SovereignREPLBridge

import tempfile


class _OllamaHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        self.server.requests.append((self.path, body))
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        reply = getattr(self.server, "reply", "print('RESULT=' + str(2 + 2))")
        payload = {"message": {"role": "assistant", "content": reply}, "done": True}
        self.wfile.write((json.dumps(payload) + "\n").encode("utf-8"))

    def log_message(self, format, *args):  # noqa: A003
        return


class MockOllama:
    def __init__(self, reply: str = "print('RESULT=' + str(2 + 2))"):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
        self.server.requests = []
        self.server.reply = reply
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _request(code: str = "prin('RESULT=' + str(2 + 2))") -> RepairRequest:
    failure = extract_error_context(
        code,
        ExecutionResult(
            status="error",
            error={
                "ename": "NameError",
                "evalue": "name 'prin' is not defined",
                "traceback": ['  File "<cell>", line 1, in <module>\n'],
            },
        ),
    )
    evaluator = QuantumTwinEvaluator()
    state = evaluator.twin_state(f"{failure.exception_type}\n{failure.message}")
    return RepairRequest(
        code=code,
        failure=failure,
        guidance=state,
        entropy=float(evaluator.entropy(state)),
    )


class RepairConfigTests(unittest.TestCase):
    def test_default_repair_model_is_qwythos_tag(self):
        self.assertEqual(
            DEFAULT_REPAIR_MODEL,
            "chatgpt1/qwythos-9b-claude-mythos-5-1m-abliterated:latest",
        )
        with patch.dict("os.environ", {}, clear=False):
            # drop only the keys we care about
            import os
            env = {k: v for k, v in os.environ.items() if k not in {
                "SOVEREIGN_REPAIR_MODEL", "SOVEREIGN_MODEL", "SOVEREIGN_BASE_URL", "OLLAMA_HOST",
            }}
            with patch.dict("os.environ", env, clear=True):
                cfg = config_from_env()
                repair = repair_config(cfg)
        self.assertEqual(repair.model, DEFAULT_REPAIR_MODEL)
        self.assertFalse(repair.echo)
        self.assertEqual(repair.base_url, "http://127.0.0.1:11434")
        sampler = default_repair_sampler(repair)
        self.assertIsInstance(sampler, LLMRepairSampler)
        self.assertEqual(sampler.cfg.model, DEFAULT_REPAIR_MODEL)

    def test_env_overrides_model_and_host(self):
        env = {
            "SOVEREIGN_REPAIR_MODEL": "mistral:latest",
            "OLLAMA_HOST": "10.0.0.9:12345",
        }
        # isolate from any pre-set SOVEREIGN_BASE_URL
        import os
        cleaned = {k: v for k, v in os.environ.items() if k not in {
            "SOVEREIGN_BASE_URL", "SOVEREIGN_REPAIR_MODEL", "OLLAMA_HOST",
        }}
        cleaned.update(env)
        with patch.dict("os.environ", cleaned, clear=True):
            self.assertEqual(default_base_url(), "http://10.0.0.9:12345")
            cfg = config_from_env()
            self.assertEqual(cfg.repair_model, "mistral:latest")
            self.assertEqual(cfg.base_url, "http://10.0.0.9:12345")
            self.assertEqual(repair_config(cfg).model, "mistral:latest")


class MockedOllamaRepairTests(unittest.IsolatedAsyncioTestCase):
    def test_sampler_calls_ollama_with_configured_tag(self):
        with MockOllama() as ollama:
            cfg = LLMConfig(
                provider="ollama",
                base_url=ollama.url,
                repair_model=DEFAULT_REPAIR_MODEL,
                echo=False,
            )
            sampler = LLMRepairSampler(cfg)
            repaired = sampler.repair(_request())
            self.assertEqual(repaired, "print('RESULT=' + str(2 + 2))")
            self.assertTrue(ollama.server.requests)
            path, body = ollama.server.requests[0]
            self.assertEqual(path, "/api/chat")
            self.assertEqual(body["model"], DEFAULT_REPAIR_MODEL)

    async def test_repair_loop_uses_mocked_ollama(self):
        class FakeKernel:
            def __init__(self):
                self.calls = []

            async def execute(self, code, *, timeout=60.0):
                self.calls.append(code)
                if "prin(" in code:
                    return ExecutionResult(
                        status="error",
                        error={
                            "ename": "NameError",
                            "evalue": "name 'prin' is not defined",
                            "traceback": ['  File "<cell>", line 1, in <module>\n'],
                        },
                    )
                return ExecutionResult(status="ok", stdout="RESULT=4\n")

        with tempfile.TemporaryDirectory() as root:
            set_data_dir(root)
            with MockOllama() as ollama:
                cfg = LLMConfig(provider="ollama", base_url=ollama.url, repair_model=DEFAULT_REPAIR_MODEL)
                result = await SovereignREPLBridge(
                    FakeKernel(), default_repair_sampler(cfg)
                ).run_goal(
                    add_goal("Evaluate 2+2", goal_id="sum-2-2", code="prin('RESULT=' + str(2 + 2))"),
                    max_attempts=3,
                )
        self.assertTrue(result["success"])
        self.assertTrue(result["corrected"])
        self.assertEqual(result["attempts"], 2)
        self.assertIn("RESULT=4", result["stdout"])

    def test_fallback_logs_when_ollama_unreachable(self):
        cfg = LLMConfig(
            provider="ollama",
            base_url="http://127.0.0.1:1",
            repair_model=DEFAULT_REPAIR_MODEL,
        )
        sampler = LLMRepairSampler(cfg, fallback=MappingRepairSampler({
            "prin('RESULT=' + str(2 + 2))": "print('RESULT=' + str(2 + 2))",
        }))
        with self.assertLogs("sovereign.repair", level="WARNING") as logs:
            repaired = sampler.repair(_request())
        self.assertEqual(repaired, "print('RESULT=' + str(2 + 2))")
        joined = "\n".join(logs.output)
        self.assertIn("falling back to the offline sampler", joined)
        self.assertIn(DEFAULT_REPAIR_MODEL, joined)
