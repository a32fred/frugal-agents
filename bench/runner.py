"""Runner for the free-model benchmark.

Design constraints:

* No network calls during ``--validate-only``.
* T0 and T2 tasks use only ``free_ask`` / ``free_cascade``.
* T1 tasks run ``free_agent`` in a disposable copy of the repository, with a
  single reviewed mutation applied to ``server.py``.
* The test file is hashed before and after the worker runs. Editing tests is a
  failure, not a shortcut.
* Every call is recorded in a run-specific ledger, never the user's ledger.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any

from bench import graders, report


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "mcp" / "free-models" / "server.py"
TEST_PATH = ROOT / "mcp" / "free-models" / "tests" / "test_server.py"


class BenchmarkError(RuntimeError):
    """The harness itself is misconfigured or a task is invalid."""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def load_server():
    """Load the shipped server by path; the directory name has a hyphen."""
    spec = importlib.util.spec_from_file_location("frugal_server_for_bench", SERVER_PATH)
    if spec is None or spec.loader is None:
        raise BenchmarkError(f"cannot load {SERVER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_tests(
    workdir: Path, timeout: int = 90, pattern: str = "test_*.py"
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(workdir) + os.pathsep + env.get("PYTHONPATH", "")
    # Hidden suites must grade the code, not ambient server feature flags.
    env.pop("FRUGAL_LADDER", None)
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                pattern,
            ],
            cwd=str(workdir),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess(
            error.cmd, 124, (error.stdout or "") + "\nTIMEOUT"
        )


def _copy_test(workdir: Path) -> Path:
    tests = workdir / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    destination = tests / "test_server.py"
    shutil.copy2(TEST_PATH, destination)
    return destination


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _code_block(text: str) -> str:
    """Return the first fenced code block, or the original text."""
    match = re.search(r"```(?:python|py)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    return match.group(1).strip() if match else text.strip()


def extract_function(text: str, name: str) -> str:
    """Extract a top-level ``def name(...)`` from model output or source.

    Models often add a sentence, a fence, or a second function. We locate the
    first definition and trim trailing prose until the fragment parses.
    """
    if not isinstance(text, str) or not text.strip():
        raise BenchmarkError("empty function output")
    candidate = _code_block(text)
    lines = candidate.splitlines()
    start = None
    pattern = re.compile(rf"^def\s+{re.escape(name)}\s*\(")
    for index, line in enumerate(lines):
        if pattern.match(line):
            start = index
            break
    if start is None:
        raise BenchmarkError(f"output does not contain a top-level def {name}(...)")

    fragment_lines = lines[start:]
    for end in range(len(fragment_lines), 0, -1):
        fragment = textwrap.dedent("\n".join(fragment_lines[:end])).strip() + "\n"
        try:
            tree = ast.parse(fragment)
        except SyntaxError:
            continue
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        if not functions or functions[0].name != name:
            continue
        node = functions[0]
        segment = ast.get_source_segment(fragment, node)
        if segment:
            return segment.rstrip() + "\n"
    raise BenchmarkError(f"could not parse a complete def {name} from the output")


def replace_function(source: str, name: str, replacement: str) -> str:
    """Replace one top-level function in source, preserving the rest."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise BenchmarkError(f"source does not compile: {error}") from error
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if len(matches) != 1:
        raise BenchmarkError(f"expected one def {name} in source, found {len(matches)}")
    node = matches[0]
    start = min(
        [node.lineno] + [decorator.lineno for decorator in node.decorator_list]
    ) - 1
    end = node.end_lineno
    if end is None:
        raise BenchmarkError(f"cannot locate the end of def {name}")
    lines = source.splitlines()
    replacement_lines = replacement.rstrip().splitlines()
    patched = "\n".join(lines[:start] + replacement_lines + lines[end:]) + "\n"
    try:
        compile(patched, "<patched-server>", "exec")
    except SyntaxError as error:
        raise BenchmarkError(f"patched source does not compile: {error}") from error
    return patched


def build_patch_prompt(task: dict[str, Any], function_source: str) -> str:
    return (
        "You are fixing one Python function after a regression. The failing "
        "regression test is shown below.\n\n"
        "FAILING TEST:\n"
        "```python\n"
        f"{task['visible_test'].strip()}\n"
        "```\n\n"
        "CURRENT FUNCTION:\n"
        "```python\n"
        f"{function_source.rstrip()}\n"
        "```\n\n"
        f"Return ONLY the complete corrected function `def {task['function_name']}"
        "(...)` and its body. Preserve the public signature and behavior that "
        "the test expects. Do not return tests, imports, prose, or Markdown "
        "fences."
    )


_MUTATION_CACHE: dict[str, dict[str, Any]] = {}


def validate_code_task(task: dict[str, Any]) -> dict[str, Any]:
    """Prove that the mutation compiles, fails the suite, and is fixable.

    Validation runs the real tests twice: once on the unmodified copy and once
    after the mutation. It is cached for the lifetime of the process because
    the mutation is deterministic.
    """
    task_id = task["id"]
    if task_id in _MUTATION_CACHE:
        return _MUTATION_CACHE[task_id]

    old = task.get("old")
    new = task.get("new")
    if not isinstance(old, str) or not isinstance(new, str) or old == new:
        raise BenchmarkError(f"{task_id}: mutation must have distinct old/new strings")

    source = SERVER_PATH.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise BenchmarkError(f"{task_id}: mutation matches {count} times, expected 1")
    mutated = source.replace(old, new, 1)
    try:
        compile(mutated, str(SERVER_PATH), "exec")
    except SyntaxError as error:
        raise BenchmarkError(f"{task_id}: mutation does not compile: {error}") from error

    with tempfile.TemporaryDirectory(prefix="frugal-bench-validate-") as tmp:
        work = Path(tmp)
        (work / "server.py").write_text(source, encoding="utf-8")
        _copy_test(work)
        baseline = _run_tests(work)
        if baseline.returncode != 0:
            raise BenchmarkError(
                f"{task_id}: baseline tests fail: {baseline.stdout[-1200:]}"
            )
        _write_visible_test(task, work)
        visible_baseline = _run_tests(work, pattern="test_visible.py")
        if visible_baseline.returncode != 0:
            raise BenchmarkError(
                f"{task_id}: visible test fails on clean source: "
                f"{visible_baseline.stdout[-1200:]}"
            )
        (work / "server.py").write_text(mutated, encoding="utf-8")
        broken = _run_tests(work)
        if broken.returncode == 0:
            raise BenchmarkError(f"{task_id}: mutation does not fail the test suite")
        visible_broken = _run_tests(work, pattern="test_visible.py")
        if visible_broken.returncode == 0:
            raise BenchmarkError(f"{task_id}: mutation does not fail the visible test")
        original_function = extract_function(source, task["function_name"])
        patched = replace_function(mutated, task["function_name"], original_function)
        (work / "server.py").write_text(patched, encoding="utf-8")
        repaired = _run_tests(work)
        if repaired.returncode != 0:
            raise BenchmarkError(
                f"{task_id}: reference patch does not pass: {repaired.stdout[-1200:]}"
            )

    result = {
        "task_id": task_id,
        "target_test": task.get("target_test"),
        "baseline_returncode": baseline.returncode,
        "mutated_returncode": broken.returncode,
        "reference_patch_returncode": repaired.returncode,
        "mutated_sha256": hashlib.sha256(mutated.encode()).hexdigest(),
    }
    _MUTATION_CACHE[task_id] = {**result, "mutated_source": mutated}
    return _MUTATION_CACHE[task_id]


def validate_tasks(tasks: list[dict[str, Any]], include_code: bool = True) -> dict[str, Any]:
    """Validate references, schemas, and (optionally) every real mutation."""
    seen: set[str] = set()
    checks: list[dict[str, Any]] = []
    for task in tasks:
        task_id = task.get("id")
        if not task_id or task_id in seen:
            raise BenchmarkError(f"duplicate or missing task id: {task_id!r}")
        seen.add(task_id)
        if task.get("tier") not in {"T0", "T1", "T2"}:
            raise BenchmarkError(f"{task_id}: unsupported tier {task.get('tier')!r}")
        if task.get("kind") == "code_repair":
            if not include_code:
                continue
            check = validate_code_task(task)
            checks.append({key: value for key, value in check.items() if key != "mutated_source"})
            continue
        reference = task.get("reference") or {}
        good = graders.grade(task, reference.get("good", ""))
        bad = graders.grade(task, reference.get("bad", ""))
        if not good["success"]:
            raise BenchmarkError(f"{task_id}: reference good output fails: {good}")
        if bad["success"]:
            raise BenchmarkError(f"{task_id}: reference bad output passes: {bad}")
        checks.append({"task_id": task_id, "good": good["detail"], "bad": bad["detail"]})
    return {"checked": len(checks), "checks": checks}


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")[:100] or "task"


def _write_visible_test(task: dict[str, Any], workdir: Path) -> Path:
    tests = workdir / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    destination = tests / "test_visible.py"
    destination.write_text(task["visible_test"], encoding="utf-8")
    return destination


def _diff(before: str, after: str) -> str:
    import difflib

    lines = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile="server.py (mutated)",
        tofile="server.py (patched)",
        lineterm="",
    )
    text = "\n".join(lines)
    return text[-12000:]


def _run_patch_arm(
    server,
    task: dict[str, Any],
    arm: str,
    rep: int,
    outdir: Path,
    keep_work: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    error = None
    output = ""
    meta: dict[str, Any] = {}
    work: Path | None = None
    final_returncode = None
    test_output = ""
    patch = ""
    try:
        validated = validate_code_task(task)
        mutated = validated["mutated_source"]
        function_source = extract_function(mutated, task["function_name"])
        prompt = build_patch_prompt(task, function_source)
        if arm == "free_ask":
            result = server.free_ask(
                prompt,
                max_tokens=int(task.get("patch_max_tokens", 3000)),
            )
        elif arm == "free_cascade":
            result = server.free_cascade(
                prompt,
                max_tokens=int(task.get("patch_max_tokens", 3000)),
            )
        else:
            raise BenchmarkError(f"unknown patch arm: {arm}")
        output = result.get("answer", "")
        meta = {
            "accepted": result.get("accepted"),
            "escalate_to_paid": result.get("escalate_to_paid"),
            "judge_independent": result.get("judge_independent"),
            "score": result.get("score"),
            "issues": result.get("issues", []),
            "warnings": result.get("warnings", []),
            "judge": result.get("judge"),
            "generator": result.get("generator") or result.get("model"),
            "usage": result.get("usage"),
        }
        replacement = extract_function(output, task["function_name"])
        patched = replace_function(mutated, task["function_name"], replacement)

        work = Path(
            tempfile.mkdtemp(
                prefix=f"{_safe_name(task['id'])}-{arm}-r{rep}-",
                dir=str(outdir / "work"),
            )
        )
        (work / "server.py").write_text(patched, encoding="utf-8")
        _copy_test(work)
        final = _run_tests(work)
        final_returncode = final.returncode
        test_output = final.stdout[-8000:]
        patch = _diff(mutated, patched)
    except Exception as exc:  # record failures as data, never crash the run
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)

    success = bool(error is None and final_returncode == 0)
    if not success and error is None:
        error = f"test suite returned {final_returncode}"
    delivered = bool(
        success
        and (
            arm != "free_cascade"
            or (meta.get("accepted") is not None and meta.get("accepted"))
        )
    )
    row = {
        "task_id": task["id"],
        "tier": task["tier"],
        "split": task.get("split", "core"),
        "kind": task["kind"],
        "arm": arm,
        "rep": rep,
        "model": meta.get("generator") or "openai/gpt-oss-20b",
        "generator": meta.get("generator"),
        "judge": meta.get("judge"),
        "accepted": meta.get("accepted"),
        "escalate_to_paid": meta.get("escalate_to_paid"),
        "judge_independent": meta.get("judge_independent"),
        "success": success,
        "delivered": delivered,
        "grader": {
            "success": success,
            "detail": "patched function passes the hidden test suite"
            if success
            else (error or "test suite failed"),
        },
        "latency_ms": latency_ms,
        "usage": meta.get("usage"),
        "output": output[:16000],
        "error": error,
        "score": meta.get("score"),
        "issues": meta.get("issues", []),
        "warnings": meta.get("warnings", []),
        "code": {
            "function": task["function_name"],
            "target_test": task.get("target_test"),
            "final_returncode": final_returncode,
            "test_tail": test_output,
            "patch": patch,
        },
        "workdir": str(work) if keep_work and work else None,
    }
    if work is not None and not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return row


def _run_text_arm(
    server,
    task: dict[str, Any],
    arm: str,
    rep: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    output = ""
    meta: dict[str, Any] = {}
    error = None
    try:
        if arm == "free_ask":
            result = server.free_ask(
                task["prompt"],
                max_tokens=int(task.get("max_tokens", 512)),
            )
        elif arm == "free_cascade":
            result = server.free_cascade(
                task["prompt"],
                max_tokens=int(task.get("max_tokens", 512)),
            )
        else:
            raise BenchmarkError(f"unknown text arm: {arm}")
        output = result.get("answer", "")
        meta = {
            "accepted": result.get("accepted"),
            "escalate_to_paid": result.get("escalate_to_paid"),
            "judge_independent": result.get("judge_independent"),
            "score": result.get("score"),
            "issues": result.get("issues", []),
            "warnings": result.get("warnings", []),
            "judge": result.get("judge"),
            "generator": result.get("generator") or result.get("model"),
            "usage": result.get("usage"),
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)

    graded = graders.grade(task, output, meta)
    if error:
        graded = {"success": False, "detail": error, "grader": task["grader"]["type"]}
    success = bool(graded.get("success"))
    delivered = success and (
        arm != "free_cascade" or meta.get("accepted") is not None and meta.get("accepted")
    )
    return {
        "task_id": task["id"],
        "tier": task["tier"],
        "split": task.get("split", "core"),
        "kind": task["kind"],
        "arm": arm,
        "rep": rep,
        "model": meta.get("generator") or "openai/gpt-oss-20b",
        "generator": meta.get("generator"),
        "judge": meta.get("judge"),
        "accepted": meta.get("accepted"),
        "escalate_to_paid": meta.get("escalate_to_paid"),
        "judge_independent": meta.get("judge_independent"),
        "success": success,
        "delivered": bool(delivered),
        "grader": graded,
        "latency_ms": latency_ms,
        "usage": meta.get("usage"),
        "output": output[:16000],
        "error": error,
        "score": meta.get("score"),
        "issues": meta.get("issues", []),
        "warnings": meta.get("warnings", []),
    }


def _entries(
    tasks: list[dict[str, Any]],
    text_reps: int,
    code_reps: int,
    arms: list[str] | None,
) -> list[tuple[dict[str, Any], str, int]]:
    entries: list[tuple[dict[str, Any], str, int]] = []
    allowed = set(arms or [])
    for task in tasks:
        if task["tier"] in {"T0", "T2"}:
            candidates = ["free_ask", "free_cascade"] if task["tier"] == "T0" else ["free_cascade"]
            reps = int(task.get("reps", text_reps))
        else:
            candidates = ["free_ask", "free_cascade"]
            reps = code_reps
        for arm in candidates:
            if allowed and arm not in allowed:
                continue
            for rep in range(1, reps + 1):
                entries.append((task, arm, rep))
    return entries


def _git_value(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=str(ROOT), text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_benchmark(
    tasks: list[dict[str, Any]],
    outdir: Path,
    text_reps: int = 2,
    code_reps: int = 1,
    delay: float = 0.5,
    keep_work: bool = False,
    seed: int = 20261020,
    arms: list[str] | None = None,
    resume: bool = False,
) -> list[dict[str, Any]]:
    outdir = outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "work").mkdir(exist_ok=True)
    os.environ["FRUGAL_LEDGER"] = str(outdir / "ledger.jsonl")
    os.environ.pop("FRUGAL_NO_LEDGER", None)

    server = load_server()
    results_path = outdir / "results.jsonl"
    existing_rows = _load_rows(results_path) if resume else []
    completed = {(row.get("task_id"), row.get("arm"), row.get("rep")) for row in existing_rows}

    run_id = outdir.name
    metadata_path = outdir / "run.json"
    if resume and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    else:
        metadata = {}
    metadata.update(
        {
            "run_id": run_id,
            "started_at": metadata.get("started_at", utc_now()),
            "bench_version": "0.1.0",
            "server_version": getattr(server, "VERSION", "unknown"),
            "git_commit": _git_value("rev-parse", "HEAD"),
            "git_dirty": bool(_git_value("status", "--porcelain")),
            "task_count": len(tasks),
            "text_reps": text_reps,
            "code_reps": code_reps,
            "arms": arms,
            "task_max_tokens": {
                task["id"]: task.get("max_tokens", task.get("patch_max_tokens", 3000))
                for task in tasks
            },
            "ladder": os.environ.get("FRUGAL_LADDER", "") == "1",
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "seed": seed,
        }
    )
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    entries = _entries(tasks, text_reps, code_reps, arms)
    if resume:
        entries = [
            entry
            for entry in entries
            if (entry[0]["id"], entry[1], entry[2]) not in completed
        ]
    random.Random(seed).shuffle(entries)
    rows: list[dict[str, Any]] = list(existing_rows)
    mode = "a" if resume else "w"
    with results_path.open(mode, encoding="utf-8") as handle:
        for index, (task, arm, rep) in enumerate(entries, 1):
            print(
                f"[{index}/{len(entries)}] {task['id']} arm={arm} rep={rep}",
                flush=True,
            )
            if task["kind"] == "code_repair":
                row = _run_patch_arm(server, task, arm, rep, outdir, keep_work)
            else:
                row = _run_text_arm(server, task, arm, rep)
            rows.append(row)
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            status = "ok" if row["success"] else "FAIL"
            print(
                f"    {status} latency={row['latency_ms']}ms "
                f"detail={str(row.get('grader', {}).get('detail', ''))[:160]}",
                flush=True,
            )
            if delay:
                time.sleep(delay)

    metadata["finished_at"] = utc_now()
    (outdir / "run.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary = report.write_summary(outdir, rows, metadata)
    print(f"\nresults: {results_path}")
    print(f"summary: {outdir / 'summary.md'}")
    print(f"success {summary['overall']['successes']}/{summary['overall']['n']}")
    return rows
