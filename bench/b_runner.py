"""Bench B runner: all-paid vs routed on small repository tasks.

Arms:

* ``paid_mirror`` — mirrors the operator's real daily tiering:
  T0 → ``gpt-5.6-sol``, T1 → ``gpt-6.1-sol``, T2 → ``gpt-6-astra``.
* ``paid_single`` — one paid model for everything (``gpt-6.1-sol``). This
  isolates routing gains from model-choice gains.
* ``routed`` — T0/T1 go to the free tier first; anything that fails the hidden
  verification is escalated to the ``paid_mirror`` model. Escalations are
  counted as paid tokens.

The subscription consumes quota, not dollars. Paid usage is reported in
tokens; a shadow USD figure is computed only when a complete, dated price
table is supplied (see ``b_prices.example.json``).

Paid execution uses ``codex exec --json``: prompt on stdin, JSONL events on
stdout, ``turn.completed.usage`` for token accounting, and ``item.completed``
agent messages for the final answer.
"""

from __future__ import annotations

import difflib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from bench import report
from bench.b_tasks import SMOKE_TASKS
from bench.runner import BenchmarkError, _run_tests, _safe_name, _sha256, load_server

ROOT = Path(__file__).resolve().parents[1]

FREE_AGENT_MODEL = "openai/gpt-oss-20b"

ARM_PAID_MIRROR = {"T0": "gpt-5.6-sol", "T1": "gpt-6.1-sol", "T2": "gpt-6-astra"}
ARM_PAID_SINGLE = {
    "T0": "gpt-6.1-sol",
    "T1": "gpt-6.1-sol",
    "T2": "gpt-6.1-sol",
}
ARMS = {
    "paid_mirror": ARM_PAID_MIRROR,
    "paid_single": ARM_PAID_SINGLE,
    "routed": ARM_PAID_MIRROR,  # escalation model per tier
}

USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


def parse_codex_events(text: str) -> dict[str, Any]:
    """Parse ``codex exec --json`` JSONL into usage and final text."""
    usage = {key: 0 for key in USAGE_KEYS}
    texts: list[str] = []
    turns = 0
    if not isinstance(text, str):
        return {"usage": usage, "text": "", "turns": 0}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed":
            turns += 1
            seen = event.get("usage") or {}
            for key in USAGE_KEYS:
                usage[key] += int(seen.get(key) or 0)
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            texts.append(str(item.get("text", "")))
    return {"usage": usage, "text": "\n".join(texts).strip(), "turns": turns}


def load_prices(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BenchmarkError("price table must be a JSON object")
    return data


def shadow_usd(
    usage: dict[str, Any],
    model: str,
    prices: dict[str, Any],
) -> float | None:
    """Approximate USD cost of a paid call, or None without a full table."""
    per_model = (prices.get("per_1m") or {}).get(model) or {}
    required = ("input", "cached_input", "output")
    if any(per_model.get(key) is None for key in required):
        return None
    total = (
        int(usage.get("input_tokens") or 0) * float(per_model["input"])
        + int(usage.get("cached_input_tokens") or 0) * float(per_model["cached_input"])
        + int(usage.get("output_tokens") or 0) * float(per_model["output"])
    ) / 1_000_000
    return round(total, 6)


def _source_paths(task: dict[str, Any]) -> list[str]:
    test_paths = [path for path in task["files"] if path.startswith("tests/")]
    return [path for path in task["files"] if path not in test_paths]


def _write_files(task: dict[str, Any], work: Path, override: dict[str, str] | None = None) -> None:
    override = override or {}
    for relative, content in task["files"].items():
        destination = work / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(override.get(relative, content), encoding="utf-8")


def _test_hashes(task: dict[str, Any], work: Path) -> dict[str, str]:
    return {
        relative: _sha256(work / relative)
        for relative in task["files"]
        if relative.startswith("tests/")
    }


def _tests_unchanged(task: dict[str, Any], work: Path, before: dict[str, str]) -> bool:
    for relative, digest in before.items():
        path = work / relative
        if not path.exists() or _sha256(path) != digest:
            return False
    return True


def _patch(task: dict[str, Any], work: Path) -> str:
    chunks: list[str] = []
    for relative in _source_paths(task):
        original = task["files"][relative]
        path = work / relative
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current == original:
            continue
        diff = difflib.unified_diff(
            original.splitlines(),
            current.splitlines(),
            fromfile=f"{relative} (initial)",
            tofile=f"{relative} (final)",
            lineterm="",
        )
        chunks.append("\n".join(diff))
    return "\n\n".join(chunks)[-16000:]


def _verify(task: dict[str, Any], work: Path, tests_before: dict[str, str]) -> dict[str, Any]:
    changed = not _tests_unchanged(task, work, tests_before)
    result = _run_tests(work, timeout=120)
    return {
        "tests_changed": changed,
        "returncode": result.returncode,
        "tail": result.stdout[-6000:],
        "success": bool(result.returncode == 0 and not changed),
    }


def prepare_workspace(
    task: dict[str, Any],
    outdir: Path,
    arm: str,
    rep: int,
) -> tuple[Path, dict[str, Any]]:
    """Create the disposable workspace and prove the initial state fails."""
    work = Path(
        tempfile.mkdtemp(
            prefix=f"{_safe_name(task['id'])}-{arm}-r{rep}-",
            dir=str(outdir / "work"),
        )
    )
    _write_files(task, work)
    tests_before = _test_hashes(task, work)
    initial = _run_tests(work, timeout=120)
    if initial.returncode == 0:
        raise BenchmarkError(f"{task['id']}: initial state already passes; task is broken")
    return work, {"tests_before": tests_before, "initial_tail": initial.stdout[-2000:]}


def run_paid_arm(
    task: dict[str, Any],
    arm: str,
    rep: int,
    outdir: Path,
    keep_work: bool,
    prices: dict[str, Any],
    timeout: int = 420,
) -> dict[str, Any]:
    model = ARMS[arm][task["tier"]]
    started = time.perf_counter()
    work: Path | None = None
    error: str | None = None
    usage: dict[str, Any] = {key: 0 for key in USAGE_KEYS}
    answer = ""
    stderr = ""
    verified: dict[str, Any] = {"success": False, "tests_changed": False, "returncode": None, "tail": ""}
    patch = ""
    try:
        work, _meta = prepare_workspace(task, outdir, arm, rep)
        command = [
            "codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
            "-s", "workspace-write", "-C", str(work), "-m", model, "-",
        ]
        proc = subprocess.run(
            command,
            input=task["prompt"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            cwd=str(work),
        )
        parsed = parse_codex_events(proc.stdout)
        usage = parsed["usage"]
        answer = parsed["text"]
        stderr = proc.stderr.strip()[-2000:]
        if parsed["turns"] == 0:
            error = f"codex produced no completed turn (exit {proc.returncode})"
        verified = _verify(task, work, _test_hashes(task, work))
        patch = _patch(task, work)
    except Exception as exc:  # record failures as data
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)

    success = bool(verified["success"] and error is None)
    if verified.get("tests_changed"):
        error = (error + "; " if error else "") + "worker edited the test files"
    return _row(
        task=task,
        arm=arm,
        rep=rep,
        model=model,
        success=success,
        delivered=success,
        escalated=None,
        free_succeeded=None,
        usage=usage,
        prices=prices,
        latency_ms=latency_ms,
        output=answer,
        error=error,
        stderr=stderr,
        code={
            "final_returncode": verified.get("returncode"),
            "tests_changed": verified.get("tests_changed"),
            "test_tail": verified.get("tail", ""),
            "patch": patch,
        },
        work=work,
        keep_work=keep_work,
    )


def run_routed_arm(
    server,
    task: dict[str, Any],
    arm: str,
    rep: int,
    outdir: Path,
    keep_work: bool,
    prices: dict[str, Any],
    timeout: int = 420,
) -> dict[str, Any]:
    """Free tier first; escalate to the paid tier only if verification fails."""
    paid_model = ARMS[arm][task["tier"]]
    started = time.perf_counter()
    work: Path | None = None
    error: str | None = None
    usage: dict[str, Any] = {key: 0 for key in USAGE_KEYS}
    answer = ""
    stderr = ""
    verified: dict[str, Any] = {"success": False, "tests_changed": False, "returncode": None, "tail": ""}
    patch = ""
    escalated = False
    free_succeeded = False
    free_error: str | None = None
    try:
        work, _meta = prepare_workspace(task, outdir, arm, rep)
        tests_before = _test_hashes(task, work)
        try:
            free_result = server.free_agent(
                task["prompt"],
                model=FREE_AGENT_MODEL,
                cwd=str(work),
                extra_args=["--auto", "--standalone"],
                timeout=timeout,
            )
            answer = free_result.get("answer", "")
            stderr = free_result.get("stderr", "")
        except Exception as exc:
            free_error = f"{type(exc).__name__}: {exc}"

        verified = _verify(task, work, tests_before)
        free_succeeded = bool(verified["success"] and free_error is None)
        patch = _patch(task, work)

        if not free_succeeded:
            escalated = True
            note = (
                "\n\nA previous automated attempt failed. Fix the repository "
                "so the test suite passes; you may keep or replace that work."
            )
            command = [
                "codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
                "-s", "workspace-write", "-C", str(work), "-m", paid_model, "-",
            ]
            proc = subprocess.run(
                command,
                input=task["prompt"] + note,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                cwd=str(work),
            )
            parsed = parse_codex_events(proc.stdout)
            usage = parsed["usage"]
            if parsed["text"]:
                answer = parsed["text"]
            stderr = proc.stderr.strip()[-2000:]
            if parsed["turns"] == 0:
                error = f"codex escalation produced no completed turn (exit {proc.returncode})"
            verified = _verify(task, work, tests_before)
            patch = _patch(task, work)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)

    success = bool(verified["success"] and error is None)
    if verified.get("tests_changed"):
        error = (error + "; " if error else "") + "worker edited the test files"
    if error is None and not free_succeeded and free_error:
        error = f"free tier failed: {free_error}"
    return _row(
        task=task,
        arm=arm,
        rep=rep,
        model=paid_model if escalated else FREE_AGENT_MODEL,
        success=success,
        delivered=success,
        escalated=escalated,
        free_succeeded=free_succeeded,
        usage=usage,
        prices=prices,
        latency_ms=latency_ms,
        output=answer,
        error=error,
        stderr=stderr,
        code={
            "final_returncode": verified.get("returncode"),
            "tests_changed": verified.get("tests_changed"),
            "test_tail": verified.get("tail", ""),
            "patch": patch,
            "free_error": free_error,
        },
        work=work,
        keep_work=keep_work,
    )


def _row(
    task: dict[str, Any],
    arm: str,
    rep: int,
    model: str,
    success: bool,
    delivered: bool,
    escalated: bool | None,
    free_succeeded: bool | None,
    usage: dict[str, Any],
    prices: dict[str, Any],
    latency_ms: int,
    output: str,
    error: str | None,
    stderr: str,
    code: dict[str, Any],
    work: Path | None,
    keep_work: bool,
) -> dict[str, Any]:
    row = {
        "task_id": task["id"],
        "tier": task["tier"],
        "split": task.get("split", "core"),
        "kind": task["kind"],
        "arm": arm,
        "rep": rep,
        "model": model,
        "success": success,
        "delivered": delivered,
        "escalated": escalated,
        "free_succeeded": free_succeeded,
        "grader": {
            "success": success,
            "detail": "hidden test suite passed without test edits"
            if success
            else (error or "test suite failed"),
        },
        "latency_ms": latency_ms,
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "cached_tokens": usage.get("cached_input_tokens", 0),
            "cache_write_tokens": usage.get("cache_write_input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        },
        "usage_raw": usage,
        "shadow_usd": shadow_usd(usage, model, prices) if success or usage.get("input_tokens") else None,
        "output": output[:16000],
        "stderr": stderr[:2000],
        "error": error,
        "code": code,
        "workdir": str(work) if keep_work and work else None,
    }
    if work is not None and not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return row


def _entries(
    tasks: list[dict[str, Any]],
    arms: list[str],
    reps: int,
) -> list[tuple[dict[str, Any], str, int]]:
    entries: list[tuple[dict[str, Any], str, int]] = []
    for task in tasks:
        for arm in arms:
            for rep in range(1, reps + 1):
                entries.append((task, arm, rep))
    return entries


def run_bench_b(
    tasks: list[dict[str, Any]],
    arms: list[str],
    outdir: Path,
    reps: int = 1,
    delay: float = 1.0,
    keep_work: bool = False,
    seed: int = 20261021,
    prices_path: str | Path | None = None,
    timeout: int = 420,
) -> list[dict[str, Any]]:
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        raise BenchmarkError(f"unknown arms: {unknown}; choose from {sorted(ARMS)}")
    outdir = outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "work").mkdir(exist_ok=True)
    os.environ["FRUGAL_LEDGER"] = str(outdir / "ledger.jsonl")
    os.environ.pop("FRUGAL_NO_LEDGER", None)

    prices = load_prices(prices_path)
    server = load_server() if "routed" in arms else None
    metadata = {
        "run_id": outdir.name,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "bench": "B",
        "bench_version": "0.1.0",
        "task_count": len(tasks),
        "arms": arms,
        "arm_models": {arm: ARMS[arm] for arm in arms},
        "free_agent_model": FREE_AGENT_MODEL,
        "prices_file": str(prices_path) if prices_path else None,
        "prices_complete": bool(prices.get("per_1m")) and all(
            all((prices["per_1m"].get(m) or {}).get(k) is not None for k in ("input", "cached_input", "output"))
            for m in {ARMS[arm][t] for arm in arms for t in ("T0", "T1", "T2")}
        ),
        "reps": reps,
        "timeout_s": timeout,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "seed": seed,
    }
    (outdir / "run.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    entries = _entries(tasks, arms, reps)
    random.Random(seed).shuffle(entries)
    results_path = outdir / "results.jsonl"
    rows: list[dict[str, Any]] = []
    with results_path.open("w", encoding="utf-8") as handle:
        for index, (task, arm, rep) in enumerate(entries, 1):
            print(f"[{index}/{len(entries)}] {task['id']} arm={arm}", flush=True)
            if arm == "routed":
                row = run_routed_arm(server, task, arm, rep, outdir, keep_work, prices, timeout)
            else:
                row = run_paid_arm(task, arm, rep, outdir, keep_work, prices, timeout)
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            status = "ok" if row["success"] else "FAIL"
            escalated = f" escalated={row['escalated']}" if row.get("escalated") is not None else ""
            print(
                f"    {status} latency={row['latency_ms']}ms{escalated} "
                f"paid_in={row['usage']['prompt_tokens']:,} "
                f"paid_out={row['usage']['completion_tokens']:,}",
                flush=True,
            )
            if delay:
                time.sleep(delay)

    metadata["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (outdir / "run.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report.write_summary(outdir, rows, metadata)
    total_in = sum(row["usage"]["prompt_tokens"] for row in rows)
    print(f"\nresults: {results_path}")
    print(f"summary: {outdir / 'summary.md'}")
    print(
        f"success {sum(1 for r in rows if r['success'])}/{len(rows)} · "
        f"paid input tokens {total_in:,}"
    )
    return rows


def validate_bench_b(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Prove each task is broken initially and solvable by the reference."""
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="frugal-bench-b-validate-") as tmp:
        work = Path(tmp)
        for task in tasks:
            task_dir = work / _safe_name(task["id"])
            task_dir.mkdir()
            _write_files(task, task_dir)
            broken = _run_tests(task_dir, timeout=120)
            if broken.returncode == 0:
                raise BenchmarkError(f"{task['id']}: initial state already passes")
            _write_files(task, task_dir, override=task["solution"])
            solved = _run_tests(task_dir, timeout=120)
            if solved.returncode != 0:
                raise BenchmarkError(
                    f"{task['id']}: reference solution does not pass: {solved.stdout[-1200:]}"
                )
            checks.append(
                {
                    "task_id": task["id"],
                    "broken_returncode": broken.returncode,
                    "solved_returncode": solved.returncode,
                }
            )
    return {"checked": len(checks), "checks": checks}
