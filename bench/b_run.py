"""Command-line entry point for Bench B (paid vs routed).

Examples::

    python -m bench.b_run --validate-only
    python -m bench.b_run --out bench/results/b-smoke --arms paid_mirror,routed
    python -m bench.b_run --arms paid_single --tasks b1_slugify

Paid arms call the subscription-backed ``codex`` CLI and consume plan quota.
The routed arm needs a NIM key plus the ``opencode`` CLI for the free agent.
Shadow USD is only computed when a complete price table is passed via
``--prices`` (see ``b_prices.example.json``).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from bench import b_runner
from bench.b_runner import ARMS, BenchmarkError
from bench.b_tasks import select_tasks


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Bench B (paid vs routed)")
    parser.add_argument("--out", type=Path, help="Output directory")
    parser.add_argument("--tasks", help="Comma-separated task ids (default: smoke tasks)")
    parser.add_argument("--arms", help="Comma-separated arms (default: paid_mirror,routed)")
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=420, help="Per-call timeout in seconds")
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--prices", type=Path, help="Shadow price table JSON (optional)")
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--validate-only", action="store_true", help="No model calls")
    parser.add_argument("--codex-path", default="codex", help="Codex CLI to use")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    tasks = select_tasks(_csv(args.tasks))
    if not tasks:
        print("no tasks selected", file=sys.stderr)
        return 2

    if args.validate_only:
        try:
            result = b_runner.validate_bench_b(tasks)
        except BenchmarkError as error:
            print(f"validation failed: {error}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    try:
        subprocess.run(
            [args.codex_path, "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        print(f"codex CLI not found at {args.codex_path!r}", file=sys.stderr)
        return 2

    arms = _csv(args.arms) or ["paid_mirror", "routed"]
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        print(f"unknown arms: {unknown}; choose from {sorted(ARMS)}", file=sys.stderr)
        return 2

    out = args.out
    if out is None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out = Path("bench") / "results" / f"b-{stamp}-smoke"

    try:
        b_runner.run_bench_b(
            tasks,
            arms,
            out,
            reps=max(1, args.reps),
            delay=max(0.0, args.delay),
            keep_work=args.keep_work,
            seed=args.seed,
            prices_path=args.prices,
            timeout=max(60, args.timeout),
        )
    except BenchmarkError as error:
        print(f"bench B failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; partial results were kept", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
