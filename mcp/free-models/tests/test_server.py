"""Unit tests for the free-models MCP server (no API key required)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
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
