"""Unit tests for the benchmark harness itself (no network, no API key)."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from bench import graders, report, runner
from bench.tasks import TASKS


class TestTaskDefinitions(unittest.TestCase):
    def test_ids_are_unique_and_tiers_are_valid(self) -> None:
        ids = [task["id"] for task in TASKS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(task["tier"] in {"T0", "T1", "T2"} for task in TASKS))

    def test_reference_outputs_match_graders(self) -> None:
        for task in TASKS:
            if task["kind"] == "code_repair":
                continue
            reference = task["reference"]
            good = graders.grade(task, reference["good"])
            bad = graders.grade(task, reference["bad"])
            self.assertTrue(good["success"], f"{task['id']}: {good}")
            self.assertFalse(bad["success"], f"{task['id']}: {bad}")

    def test_mutations_are_unique_and_compile(self) -> None:
        source = runner.SERVER_PATH.read_text(encoding="utf-8")
        for task in TASKS:
            if task["kind"] != "code_repair":
                continue
            self.assertEqual(source.count(task["old"]), 1, task["id"])
            mutated = source.replace(task["old"], task["new"], 1)
            compile(mutated, "<mutated>", "exec")
            names = {
                node.name
                for node in ast.parse(mutated).body
                if isinstance(node, ast.FunctionDef)
            }
            self.assertIn(task["function_name"], names, task["id"])


class TestPatchHelpers(unittest.TestCase):
    def test_extract_function_tolerates_prose_and_fences(self) -> None:
        source = runner.SERVER_PATH.read_text(encoding="utf-8")
        original = runner.extract_function(source, "parse_verdict")
        wrapped = f"Here is the fix:\n```python\n{original}\n```\nDone."
        self.assertEqual(
            runner.extract_function(wrapped, "parse_verdict").strip(),
            original.strip(),
        )

    def test_replace_function_changes_only_one_function(self) -> None:
        source = runner.SERVER_PATH.read_text(encoding="utf-8")
        original = runner.extract_function(source, "chain_for")
        replacement = original.replace(
            "Requested model first, then the configured fallback chain, deduplicated.",
            "Requested model first, then the configured fallback chain.",
        )
        patched = runner.replace_function(source, "chain_for", replacement)
        self.assertIn("Requested model first", patched)
        self.assertNotIn("then the configured fallback chain, deduplicated.", patched)
        self.assertEqual(
            runner.extract_function(patched, "chain_for").strip(),
            replacement.strip(),
        )


class TestGraders(unittest.TestCase):
    def test_extract_json_from_fence(self) -> None:
        self.assertEqual(
            graders.extract_json('```json\n{"a": 1}\n```'),
            {"a": 1},
        )

    def test_json_object_rejects_string_integer(self) -> None:
        task = next(task for task in TASKS if task["id"] == "t0_json_log_extract")
        result = graders.grade(
            task,
            '{"level": "error", "code": "503", "latency_ms": 187, "service": "api"}',
        )
        self.assertFalse(result["success"])

    def test_policy_grader_accepts_escalation(self) -> None:
        task = next(task for task in TASKS if task["id"] == "t2_disable_tls")
        result = graders.grade(task, "", {"escalate_to_paid": True})
        self.assertTrue(result["success"])

    def test_policy_grader_rejects_unsafe_code(self) -> None:
        task = next(task for task in TASKS if task["id"] == "t2_disable_tls")
        result = graders.grade(task, "requests.get(url, verify=False)")
        self.assertFalse(result["success"])


class TestReport(unittest.TestCase):
    def test_wilson_interval_is_bounded_and_ordered(self) -> None:
        low, high = report.wilson_interval(0, 10)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 1.0)
        low, high = report.wilson_interval(10, 10)
        self.assertGreater(low, 0.0)
        self.assertEqual(high, 1.0)

    def test_judge_confusion(self) -> None:
        rows = [
            {"tier": "T0", "arm": "free_cascade", "accepted": True, "success": True, "error": None},
            {"tier": "T0", "arm": "free_cascade", "accepted": True, "success": False, "error": None},
            {"tier": "T0", "arm": "free_cascade", "accepted": False, "success": True, "error": None},
            {"tier": "T0", "arm": "free_cascade", "accepted": False, "success": False, "error": None},
        ]
        confusion = report.judge_confusion(rows)
        self.assertEqual(confusion["tp"], 1)
        self.assertEqual(confusion["fp"], 1)
        self.assertEqual(confusion["fn"], 1)
        self.assertEqual(confusion["tn"], 1)
        self.assertEqual(confusion["precision"], 0.5)
        self.assertEqual(confusion["recall"], 0.5)


class TestPaths(unittest.TestCase):
    def test_server_and_tests_exist(self) -> None:
        self.assertTrue(runner.SERVER_PATH.is_file())
        self.assertTrue(runner.TEST_PATH.is_file())
        self.assertTrue(Path(runner.SERVER_PATH).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
