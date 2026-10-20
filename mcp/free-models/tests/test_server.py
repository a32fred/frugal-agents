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
