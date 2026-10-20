"""Unit tests for the free-models MCP server (no API key required)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"

spec = importlib.util.spec_from_file_location("free_models_server", SERVER)
assert spec and spec.loader
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class TestRedaction(unittest.TestCase):
    def test_nvidia_key(self) -> None:
        self.assertEqual(server.redact("key nvapi-abcdefghijklmnop1234"), "key [REDACTED]")

    def test_openai_key(self) -> None:
        self.assertNotIn("sk-", server.redact("sk-proj-abcdefghijklmnopqrstuv"))

    def test_assignment(self) -> None:
        cleaned = server.redact('API_KEY="supersecretvalue"')
        self.assertNotIn("supersecretvalue", cleaned)
        self.assertIn("REDACTED", cleaned)

    def test_private_key_block(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
        self.assertEqual(server.redact(text), "[REDACTED]")

    def test_plain_text_untouched(self) -> None:
        text = "Add a retry with exponential backoff to the HTTP client."
        self.assertEqual(server.redact(text), text)

    def test_empty(self) -> None:
        self.assertEqual(server.redact(""), "")


class TestVerdict(unittest.TestCase):
    def test_plain_json(self) -> None:
        verdict = server.parse_verdict('{"score": 0.9, "verdict": "accept", "issues": []}')
        self.assertEqual(verdict["score"], 0.9)
        self.assertEqual(verdict["verdict"], "accept")

    def test_fenced_json(self) -> None:
        raw = 'Here you go:\n```json\n{"score": 0.4, "verdict": "reject", "issues": ["incomplete"]}\n```'
        verdict = server.parse_verdict(raw)
        self.assertEqual(verdict["verdict"], "reject")
        self.assertEqual(verdict["issues"], ["incomplete"])

    def test_score_is_clamped(self) -> None:
        self.assertEqual(server.parse_verdict('{"score": 7}')["score"], 1.0)
        self.assertEqual(server.parse_verdict('{"score": -2}')["score"], 0.0)

    def test_invalid(self) -> None:
        verdict = server.parse_verdict("I cannot review this.")
        self.assertEqual(verdict["verdict"], "reject")
        self.assertEqual(verdict["issues"], ["unparsable verdict"])

    def test_issues_string_coerced(self) -> None:
        verdict = server.parse_verdict('{"score": 1, "verdict": "accept", "issues": "none"}')
        self.assertEqual(verdict["issues"], ["none"])

    def test_per_call_threshold_is_used(self) -> None:
        raw = '{"score": 0.85}'
        self.assertEqual(server.parse_verdict(raw)["verdict"], "accept")
        self.assertEqual(server.parse_verdict(raw, threshold=0.9)["verdict"], "reject")


class TestUsageNormalization(unittest.TestCase):
    def test_openai_cached_and_reasoning(self) -> None:
        usage = server.normalize_usage(
            {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 1100,
                "prompt_tokens_details": {"cached_tokens": 800},
                "completion_tokens_details": {"reasoning_tokens": 40},
            }
        )
        self.assertEqual(usage["cached_tokens"], 800)
        self.assertEqual(usage["cache_hit_rate"], 0.8)
        self.assertEqual(usage["reasoning_tokens"], 40)
        self.assertEqual(usage["total_tokens"], 1100)

    def test_deepseek_cache_fields(self) -> None:
        usage = server.normalize_usage(
            {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "prompt_cache_hit_tokens": 900,
                "prompt_cache_miss_tokens": 100,
            }
        )
        self.assertEqual(usage["cached_tokens"], 900)
        self.assertEqual(usage["cache_hit_rate"], 0.9)

    def test_anthropic_fields_are_additive(self) -> None:
        usage = server.normalize_usage(
            {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 800,
                "cache_creation_input_tokens": 100,
            }
        )
        self.assertEqual(usage["prompt_tokens"], 1000)
        self.assertEqual(usage["cached_tokens"], 800)
        self.assertEqual(usage["cache_write_tokens"], 100)
        self.assertEqual(usage["cache_hit_rate"], 0.8)
        self.assertEqual(usage["completion_tokens"], 50)

    def test_empty_usage(self) -> None:
        usage = server.normalize_usage(None)
        self.assertEqual(usage["prompt_tokens"], 0)
        self.assertEqual(usage["cache_hit_rate"], 0.0)

    def test_merge_usage_sums_generator_and_judge(self) -> None:
        merged = server.merge_usage(
            {"prompt_tokens": 100, "cached_tokens": 80, "completion_tokens": 10},
            {"prompt_tokens": 50, "cached_tokens": 0, "completion_tokens": 5},
        )
        self.assertEqual(merged["prompt_tokens"], 150)
        self.assertEqual(merged["cached_tokens"], 80)
        self.assertEqual(merged["completion_tokens"], 15)
        self.assertAlmostEqual(merged["cache_hit_rate"], 0.5333, places=3)


class TestStats(unittest.TestCase):
    ROWS = [
        {
            "tool": "free_ask",
            "model": "m1",
            "usage": {"prompt_tokens": 100, "cached_tokens": 80, "completion_tokens": 10},
            "latency_ms": 100,
        },
        {
            "tool": "free_cascade",
            "model": "m1",
            "judge": "m2",
            "usage": {"prompt_tokens": 100, "cached_tokens": 20},
            "accepted": True,
            "latency_ms": 900,
        },
        {
            "tool": "free_cascade",
            "model": "m1",
            "usage": {"prompt_tokens": 0},
            "accepted": False,
            "latency_ms": 300,
        },
    ]

    def test_aggregates(self) -> None:
        stats = server.compute_stats(self.ROWS)
        self.assertEqual(stats["calls"], 3)
        self.assertEqual(stats["legacy_rows"], 3)
        self.assertEqual(stats["by_tool"]["free_cascade"], 2)
        self.assertEqual(stats["cascade"]["accepted"], 1)
        self.assertEqual(stats["cascade"]["escalated"], 1)
        self.assertEqual(stats["cascade"]["escalation_rate"], 0.5)
        self.assertEqual(stats["cache"]["cached_tokens"], 100)
        self.assertEqual(stats["cache"]["hit_rate"], 0.5)
        self.assertEqual(stats["latency_ms"]["p95"], 900)

    def test_empty(self) -> None:
        stats = server.compute_stats([])
        self.assertEqual(stats["calls"], 0)
        self.assertEqual(stats["cascade"]["escalation_rate"], 0.0)
        self.assertIn("no ledger entries", server.format_stats(stats))

    def test_format_contains_rates(self) -> None:
        text = server.format_stats(server.compute_stats(self.ROWS))
        self.assertIn("escalation 50.0%", text)
        self.assertIn("50.0% hit rate", text)


class TestChain(unittest.TestCase):
    def test_requested_model_first_and_deduplicated(self) -> None:
        os.environ["FRUGAL_CHAIN"] = "a/model,b/model,a/model"
        try:
            chain = server.chain_for("a/model")
        finally:
            del os.environ["FRUGAL_CHAIN"]
        self.assertEqual(chain, ["a/model", "b/model"])

    def test_default_chain_used_without_override(self) -> None:
        os.environ.pop("FRUGAL_CHAIN", None)
        chain = server.chain_for("custom/model")
        self.assertEqual(chain[0], "custom/model")
        self.assertIn("openai/gpt-oss-20b", chain)


class TestToolSchema(unittest.TestCase):
    def test_three_tools(self) -> None:
        names = [tool["name"] for tool in server.TOOLS]
        self.assertEqual(names, ["free_ask", "free_agent", "free_cascade"])

    def test_required_fields(self) -> None:
        schemas = {tool["name"]: tool["inputSchema"] for tool in server.TOOLS}
        self.assertEqual(schemas["free_ask"]["required"], ["prompt"])
        self.assertEqual(schemas["free_agent"]["required"], ["task", "cwd"])
        self.assertEqual(schemas["free_cascade"]["required"], ["task"])

    def test_annotations_describe_side_effects(self) -> None:
        by_name = {tool["name"]: tool for tool in server.TOOLS}
        for tool in server.TOOLS:
            self.assertIn("annotations", tool)
            self.assertIn("readOnlyHint", tool["annotations"])
        self.assertTrue(by_name["free_ask"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["free_cascade"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["free_agent"]["annotations"]["readOnlyHint"])
        self.assertTrue(by_name["free_agent"]["annotations"]["destructiveHint"])


class TestLedger(unittest.TestCase):
    def test_schema_lock_and_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ledger.jsonl")
            keys = ("FRUGAL_LEDGER", "FRUGAL_LEDGER_MAX_BYTES", "FRUGAL_NO_LEDGER")
            previous = {key: os.environ.get(key) for key in keys}
            os.environ["FRUGAL_LEDGER"] = path
            os.environ["FRUGAL_LEDGER_MAX_BYTES"] = "10"
            os.environ.pop("FRUGAL_NO_LEDGER", None)
            try:
                server.record({"tool": "free_ask", "model": "m1", "usage": {}})
                server.record({"tool": "free_ask", "model": "m1", "usage": {}})
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

            self.assertTrue(os.path.exists(path + ".1"))
            self.assertTrue(os.path.exists(path + ".lock"))
            rows = server.read_ledger(path)
            self.assertEqual(len(rows), 2)
            self.assertTrue(
                all(
                    row["schema_version"] == server.LEDGER_SCHEMA_VERSION
                    for row in rows
                )
            )


class TestRetry(unittest.TestCase):
    def test_backoff_has_jitter_and_a_cap(self) -> None:
        for attempt in range(4):
            delay = server.retry_delay(attempt)
            ceiling = min(8.0, 0.5 * (2**attempt))
            self.assertGreaterEqual(delay, ceiling / 2 - 1e-9)
            self.assertLessEqual(delay, ceiling)
        self.assertLessEqual(server.retry_delay(10), 8.0)


class TestJudgeIndependence(unittest.TestCase):
    def _run_cascade(self, generator_model: str, judge_model: str):
        calls: list[dict[str, object]] = []
        original = server.complete

        def fake_complete(
            prompt,
            model=None,
            system=None,
            max_tokens=2048,
            temperature=0.2,
            reasoning_effort=None,
            exclude_models=None,
            cancel_event=None,
            record_call=True,
        ):
            calls.append({"system": system, "exclude_models": exclude_models})
            is_judge = system is server.JUDGE_SYSTEM
            return {
                "text": (
                    '{"score": 1.0, "verdict": "accept", "issues": []}'
                    if is_judge
                    else "answer"
                ),
                "model": judge_model if is_judge else generator_model,
                "usage": server.normalize_usage(None),
                "latency_ms": 1,
                "fallbacks": 0,
            }

        server.complete = fake_complete
        os.environ["FRUGAL_NO_LEDGER"] = "1"
        try:
            result = server.free_cascade("do the task")
        finally:
            server.complete = original
            os.environ.pop("FRUGAL_NO_LEDGER", None)
        return result, calls

    def test_judge_excludes_generator_model(self) -> None:
        result, calls = self._run_cascade("gen/model", "judge/model")
        self.assertTrue(result["judge_independent"])
        self.assertEqual(result["warnings"], [])
        self.assertEqual(calls[1]["exclude_models"], ["gen/model"])

    def test_same_model_is_flagged_not_hidden(self) -> None:
        result, _ = self._run_cascade("gen/model", "gen/model")
        self.assertFalse(result["judge_independent"])
        self.assertTrue(result["warnings"])
        self.assertIn("not independent", result["warnings"][0])


class TestProtocolNegotiation(unittest.TestCase):
    def test_known_version_is_echoed(self) -> None:
        self.assertEqual(server.negotiate_protocol("2025-06-18"), "2025-06-18")
        self.assertEqual(server.negotiate_protocol("2024-11-05"), "2024-11-05")

    def test_unknown_version_falls_back(self) -> None:
        self.assertEqual(
            server.negotiate_protocol("2026-07-28"), server.PROTOCOL_VERSION
        )
        self.assertEqual(server.negotiate_protocol(None), server.PROTOCOL_VERSION)


class TestDoctor(unittest.TestCase):
    def test_missing_key_is_a_failure(self) -> None:
        original_key = server.load_api_key
        original_which = server.shutil.which

        def missing_key() -> str:
            raise server.FrugalError("NIM_API_KEY is not set")

        server.load_api_key = missing_key
        server.shutil.which = lambda name: None
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = server.doctor()
        finally:
            server.load_api_key = original_key
            server.shutil.which = original_which

        output = buffer.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("NIM key", output)
        self.assertIn("result: 1 check(s) failed", output)


class TestFreeAgentStdin(unittest.TestCase):
    def test_task_goes_to_stdin_not_argv(self) -> None:
        captured: dict[str, object] = {}

        class RecordingStdin(io.StringIO):
            def close(self) -> None:
                captured["stdin_text"] = self.getvalue()
                super().close()

        class FakeProc:
            returncode = 0
            pid = 999999

            def __init__(self, command, **kwargs):
                captured["command"] = command
                self.stdin = RecordingStdin()
                self.stdout = io.StringIO(
                    '{"type": "text", "part": {"type": "text", "text": "DONE"}}\n'
                )
                self.stderr = io.StringIO("")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        original_popen = server.subprocess.Popen
        original_which = server.shutil.which
        os.environ["FRUGAL_NO_LEDGER"] = "1"
        server.subprocess.Popen = FakeProc
        server.shutil.which = lambda name: "/usr/bin/opencode"
        try:
            result = server.free_agent("SECRET_TASK_TEXT", cwd="/tmp")
        finally:
            server.subprocess.Popen = original_popen
            server.shutil.which = original_which
            os.environ.pop("FRUGAL_NO_LEDGER", None)

        self.assertEqual(captured["command"][0], "opencode")
        self.assertNotIn("SECRET_TASK_TEXT", captured["command"])
        self.assertEqual(captured["stdin_text"], "SECRET_TASK_TEXT")
        self.assertEqual(result["answer"], "DONE")

    def test_relative_cwd_is_rejected(self) -> None:
        with self.assertRaises(server.FrugalError):
            server.free_agent("x", cwd="relative/path")

    def test_missing_cwd_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "missing")
            with self.assertRaises(server.FrugalError):
                server.free_agent("x", cwd=missing)


class TestMcpConcurrency(unittest.TestCase):
    def test_ping_works_during_a_tool_call_and_cancel_is_honored(self) -> None:
        captured: list[dict] = []
        capture_lock = threading.Lock()
        original_send = server.send
        original_tool = server.tool_call

        def fake_send(message: dict) -> None:
            with capture_lock:
                captured.append(message)

        def slow_tool(name, arguments, cancel_event=None):
            for _ in range(500):
                if cancel_event is not None and cancel_event.is_set():
                    raise server.CancelledError("cancelled")
                time.sleep(0.01)
            return {"answer": "late"}

        server.send = fake_send
        server.tool_call = slow_tool
        mcp = server.McpServer()
        try:
            mcp.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "free_ask", "arguments": {}},
                }
            )
            deadline = time.time() + 2
            while time.time() < deadline:
                with capture_lock:
                    if captured:
                        break
                time.sleep(0.01)

            mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})
            mcp.handle(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/cancelled",
                    "params": {"requestId": 1, "reason": "test"},
                }
            )
            deadline = time.time() + 5
            while time.time() < deadline:
                with capture_lock:
                    if any(message.get("id") == 1 for message in captured):
                        break
                time.sleep(0.01)
        finally:
            mcp.close()
            server.send = original_send
            server.tool_call = original_tool

        by_id = {message.get("id"): message for message in captured if "id" in message}
        self.assertEqual(by_id[2]["result"], {})
        self.assertEqual(by_id[1]["error"]["code"], -32800)

    def test_progress_notifications(self) -> None:
        captured: list[dict] = []
        capture_lock = threading.Lock()
        original_send = server.send
        original_tool = server.tool_call

        def fake_send(message: dict) -> None:
            with capture_lock:
                captured.append(message)

        def fast_tool(name, arguments, cancel_event=None):
            return {"answer": "ok"}

        server.send = fake_send
        server.tool_call = fast_tool
        mcp = server.McpServer()
        try:
            mcp.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "free_ask",
                        "arguments": {},
                        "_meta": {"progressToken": "tok"},
                    },
                }
            )
            deadline = time.time() + 5
            while time.time() < deadline:
                with capture_lock:
                    if any(message.get("id") == 5 for message in captured):
                        break
                time.sleep(0.01)
        finally:
            mcp.close()
            server.send = original_send
            server.tool_call = original_tool

        progress = [
            message
            for message in captured
            if message.get("method") == "notifications/progress"
        ]
        self.assertEqual(
            [message["params"]["progress"] for message in progress], [0, 1]
        )
        self.assertTrue(
            all(message["params"]["progressToken"] == "tok" for message in progress)
        )
        reply = next(message for message in captured if message.get("id") == 5)
        self.assertFalse(reply["result"]["isError"])


class TestMcpProtocol(unittest.TestCase):
    def test_initialize_and_list_tools(self) -> None:
        requests = "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"},
                        },
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                json.dumps({"jsonrpc": "2.0", "id": 3, "method": "bogus/method"}),
            ]
        )

        proc = subprocess.run(
            [sys.executable, str(SERVER)],
            input=requests + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

        replies = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        by_id = {reply.get("id"): reply for reply in replies}

        self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "free-models")
        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(
            [tool["name"] for tool in by_id[2]["result"]["tools"]],
            ["free_ask", "free_agent", "free_cascade"],
        )
        self.assertEqual(by_id[3]["error"]["code"], -32601)

    def test_unknown_tool_returns_tool_error_not_crash(self) -> None:
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        )
        proc = subprocess.run(
            [sys.executable, str(SERVER)],
            input=request + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            cwd=ROOT,
        )
        reply = json.loads(proc.stdout.strip())
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("unknown tool", reply["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
