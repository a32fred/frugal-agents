"""Bench B task definitions.

Bench B measures the real routing trade-off on small, self-contained
repository tasks. Each task is a disposable directory: broken source files plus
a deterministic test suite. A task is solved when the shipped test suite passes
in a workspace where the test files are byte-identical to the originals.

Tasks are deliberately small. Bench B is about measuring paid-token burn and
free-tier absorption on realistic work, not about stumping the models.
"""

from __future__ import annotations

from typing import Any


# --------------------------------------------------------------------------- #
# Shared prompt shape
# --------------------------------------------------------------------------- #

def _prompt(target: str, goal: str) -> str:
    return (
        f"You are working in a small Python project. {goal}\n\n"
        f"Edit `{target}` (you may add helper functions in the same file). "
        "Do not edit anything under `tests/`. Do not add dependencies.\n\n"
        "Verify with:\n"
        "```\n"
        "python -m unittest discover -s tests -p \"test_*.py\"\n"
        "```\n"
        "It must pass with zero failures. Work until it does."
    )


# --------------------------------------------------------------------------- #
# b1 — slugify with accent folding (T1)
# --------------------------------------------------------------------------- #

_B1_BROKEN = '''"""Slugify helper (benchmark task b1)."""
import re


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\\s+", "-", text)
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")
'''

_B1_SOLUTION = '''"""Slugify helper (benchmark task b1)."""
import re
import unicodedata


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
'''

_B1_TESTS = '''"""Tests for slugify (do not edit)."""
import unittest

import slugify


class TestSlugify(unittest.TestCase):
    def test_examples(self) -> None:
        cases = [
            ("Héllo Wörld!", "hello-world"),
            ("Café & Chá", "cafe-cha"),
            ("  --Olá__Mundo-- ", "ola-mundo"),
            ("Ação123", "acao123"),
            ("", ""),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(slugify.slugify(source), expected)


if __name__ == "__main__":
    unittest.main()
'''

# --------------------------------------------------------------------------- #
# b2 — exponential backoff with cap (T1)
# --------------------------------------------------------------------------- #

_B2_BROKEN = '''"""Retry helpers (benchmark task b2)."""


def retry_delay(attempt: int) -> float:
    """Seconds to wait before retry number `attempt` (0-based)."""
    return 1.0


def call_with_retries(func, attempts: int = 5):
    """Call `func`, retrying `attempts` times total on RuntimeError."""
    last = None
    for _ in range(attempts):
        try:
            return func()
        except RuntimeError as exc:
            last = exc
    raise last
'''

_B2_SOLUTION = '''"""Retry helpers (benchmark task b2)."""


def retry_delay(attempt: int) -> float:
    """Seconds to wait before retry number `attempt` (0-based)."""
    if attempt < 0:
        return 1.0
    return min(float(2 ** attempt), 30.0)


def call_with_retries(func, attempts: int = 5):
    """Call `func`, retrying `attempts` times total on RuntimeError."""
    last = None
    for _ in range(attempts):
        try:
            return func()
        except RuntimeError as exc:
            last = exc
    raise last
'''

_B2_TESTS = '''"""Tests for retry helpers (do not edit)."""
import unittest

import retry


class TestRetryDelay(unittest.TestCase):
    def test_is_exponential(self) -> None:
        self.assertEqual(retry.retry_delay(0), 1.0)
        self.assertEqual(retry.retry_delay(1), 2.0)
        self.assertEqual(retry.retry_delay(2), 4.0)
        self.assertEqual(retry.retry_delay(4), 16.0)

    def test_caps_at_30_seconds(self) -> None:
        self.assertEqual(retry.retry_delay(5), 30.0)
        self.assertEqual(retry.retry_delay(10), 30.0)

    def test_negative_attempt_uses_base_delay(self) -> None:
        self.assertEqual(retry.retry_delay(-3), 1.0)


class TestCallWithRetries(unittest.TestCase):
    def test_returns_after_transient_failures(self) -> None:
        calls = []

        def flaky() -> str:
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("transient")
            return "ok"

        self.assertEqual(retry.call_with_retries(flaky, attempts=5), "ok")
        self.assertEqual(len(calls), 3)

    def test_raises_after_exhausting_attempts(self) -> None:
        calls = []

        def always() -> None:
            calls.append(1)
            raise RuntimeError("permanent")

        with self.assertRaises(RuntimeError):
            retry.call_with_retries(always, attempts=4)
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
'''

# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

SMOKE_TASKS: list[dict[str, Any]] = [
    {
        "id": "b1_slugify",
        "tier": "T1",
        "kind": "repo_fix",
        "prompt": _prompt(
            "slugify.py",
            "Make `slugify()` fold accented characters to plain ASCII "
            "(e.g. `é` → `e`) before building the slug, and treat every run "
            "of non-alphanumeric characters as a single hyphen.",
        ),
        "files": {
            "slugify.py": _B1_BROKEN,
            "tests/test_slugify.py": _B1_TESTS,
        },
        "solution": {"slugify.py": _B1_SOLUTION},
        "timeout": 420,
    },
    {
        "id": "b2_backoff",
        "tier": "T1",
        "kind": "repo_fix",
        "prompt": _prompt(
            "retry.py",
            "Make `retry_delay()` exponential in `attempt` starting at 1 "
            "second and capped at 30 seconds. Negative attempts use the base "
            "delay. Keep `call_with_retries()` behavior unchanged.",
        ),
        "files": {
            "retry.py": _B2_BROKEN,
            "tests/test_retry.py": _B2_TESTS,
        },
        "solution": {"retry.py": _B2_SOLUTION},
        "timeout": 420,
    },
]


def select_tasks(ids: list[str] | None = None) -> list[dict[str, Any]]:
    if ids:
        wanted = set(ids)
        return [task for task in SMOKE_TASKS if task["id"] in wanted]
    return list(SMOKE_TASKS)
