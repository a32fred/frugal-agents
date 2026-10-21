"""Command-line entry point for the free-model benchmark.

Examples::

    python -m bench.run --list
    python -m bench.run --validate-only
    python -m bench.run --tiers T0 --text-reps 1 --out /tmp/fa-bench-t0
    python -m bench.run --out bench/results/2026-10-20-pilot

No paid API is used. The T1 arm needs the local ``opencode`` CLI and a NIM
key, exactly like the shipped ``free_agent`` tool.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from bench import runner
from bench.tasks import TASKS, select_tasks


def _csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the frugal-agents free-model benchmark")
    parser.add_argument(
        "--out",
        type=Path,
        help="Output directory (default: bench/results/<UTC timestamp>-pilot)",
    )
    parser.add_argument("--tasks", help="Comma-separated task ids")
    parser.add_argument("--tiers", help="Comma-separated tiers, e.g. T0,T1,T2")
    parser.add_argument("--arms", help="Comma-separated arms, e.g. free_ask,agent_fast")
    parser.add_argument("--text-reps", type=int, default=2, help="Repetitions for T0/T2")
    parser.add_argument("--code-reps", type=int, default=1, help="Repetitions for T1")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between calls")
    parser.add_argument("--seed", type=int, default=20261020)
    parser.add_argument("--keep-work", action="store_true", help="Keep disposable T1 workspaces")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Check task references and mutations without network calls",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append missing task/arm/rep rows to an existing output directory",
    )
    parser.add_argument("--no-code", action="store_true", help="Skip T1 mutation validation")
    parser.add_argument("--list", action="store_true", help="List tasks and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        for task in TASKS:
            print(f"{task['id']}\t{task['tier']}\t{task['kind']}")
        return 0

    tasks = select_tasks(_csv(args.tiers), _csv(args.tasks))
    if not tasks:
        print("no tasks selected", file=sys.stderr)
        return 2

    if args.validate_only:
        try:
            result = runner.validate_tasks(tasks, include_code=not args.no_code)
        except runner.BenchmarkError as error:
            print(f"validation failed: {error}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    out = args.out
    if out is None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out = Path("bench") / "results" / f"{stamp}-pilot"

    try:
        server = runner.load_server()
        server.load_api_key()
    except Exception as error:
        print(f"cannot start benchmark: {error}", file=sys.stderr)
        return 2

    try:
        runner.run_benchmark(
            tasks,
            out,
            text_reps=max(1, args.text_reps),
            code_reps=max(1, args.code_reps),
            delay=max(0.0, args.delay),
            keep_work=args.keep_work,
            seed=args.seed,
            arms=_csv(args.arms),
            resume=args.resume,
        )
    except runner.BenchmarkError as error:
        print(f"benchmark failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; partial results were kept", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
