"""Unit tests for the Bench B runner (no network, no subscription calls)."""

from __future__ import annotations

import unittest

from bench import b_runner
from bench.b_tasks import SMOKE_TASKS


class TestCodexEventParsing(unittest.TestCase):
    def test_sums_usage_across_turns_and_extracts_text(self) -> None:
        events = (
            '{"type":"thread.started","thread_id":"t1"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"partial"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":100,'
            '"cached_input_tokens":10,"cache_write_input_tokens":5,'
            '"output_tokens":20,"reasoning_output_tokens":8}}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
            '{"type":"turn.completed","usage":{"input_tokens":50,'
            '"cached_input_tokens":0,"cache_write_input_tokens":0,'
            '"output_tokens":30,"reasoning_output_tokens":12}}\n'
        )
        parsed = b_runner.parse_codex_events(events)
        self.assertEqual(parsed["turns"], 2)
        self.assertEqual(parsed["usage"]["input_tokens"], 150)
        self.assertEqual(parsed["usage"]["cached_input_tokens"], 10)
        self.assertEqual(parsed["usage"]["cache_write_input_tokens"], 5)
        self.assertEqual(parsed["usage"]["output_tokens"], 50)
        self.assertEqual(parsed["usage"]["reasoning_output_tokens"], 20)
        self.assertEqual(parsed["text"], "partial\ndone")

    def test_ignores_garbage_lines_and_empty_input(self) -> None:
        parsed = b_runner.parse_codex_events("hello\n{not json}\n\n")
        self.assertEqual(parsed["turns"], 0)
        self.assertEqual(parsed["usage"]["input_tokens"], 0)
        self.assertEqual(parsed["text"], "")
        self.assertEqual(b_runner.parse_codex_events("")["turns"], 0)


class TestShadowPricing(unittest.TestCase):
    USAGE = {
        "input_tokens": 1_000_000,
        "cached_input_tokens": 500_000,
        "cache_write_input_tokens": 0,
        "output_tokens": 100_000,
        "reasoning_output_tokens": 40_000,
    }

    def test_none_without_complete_table(self) -> None:
        prices = {"per_1m": {"gpt-6.1-sol": {"input": 1.0, "cached_input": None, "output": 2.0}}}
        self.assertIsNone(b_runner.shadow_usd(self.USAGE, "gpt-6.1-sol", prices))
        self.assertIsNone(b_runner.shadow_usd(self.USAGE, "gpt-6.1-sol", {}))

    def test_computed_from_complete_table(self) -> None:
        prices = {
            "per_1m": {
                "gpt-6.1-sol": {"input": 1.0, "cached_input": 0.1, "output": 4.0, "reasoning_output": 4.0}
            }
        }
        expected = (1_000_000 * 1.0 + 500_000 * 0.1 + 100_000 * 4.0) / 1_000_000
        self.assertEqual(b_runner.shadow_usd(self.USAGE, "gpt-6.1-sol", prices), round(expected, 6))


class TestArmConfiguration(unittest.TestCase):
    def test_paid_mirror_matches_operator_daily_tiering(self) -> None:
        self.assertEqual(
            b_runner.ARMS["paid_mirror"],
            {"T0": "gpt-5.6-sol", "T1": "gpt-6.1-sol", "T2": "gpt-6-astra"},
        )
        self.assertEqual(
            b_runner.ARMS["paid_single"],
            {"T0": "gpt-6.1-sol", "T1": "gpt-6.1-sol", "T2": "gpt-6.1-sol"},
        )


class TestSmokeTasks(unittest.TestCase):
    def test_tasks_are_broken_and_solvable(self) -> None:
        result = b_runner.validate_bench_b(SMOKE_TASKS)
        self.assertEqual(result["checked"], len(SMOKE_TASKS))
        for check in result["checks"]:
            self.assertNotEqual(check["broken_returncode"], 0)
            self.assertEqual(check["solved_returncode"], 0)


if __name__ == "__main__":
    unittest.main()
