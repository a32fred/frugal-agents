"""Metrics and Markdown reporting for the benchmark.

The report separates three questions that are often conflated:

1. Is the free answer correct? (task success)
2. Did the cascade deliver it without escalation? (delivered)
3. Did the judge accept correct answers and reject incorrect ones? (confusion)

A benchmark that only reports (1) hides unsafe acceptance; a benchmark that
only reports judge agreement hides whether the judge is useful.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
        / denominator
    )
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _rate(successes: int, total: int) -> dict[str, Any]:
    low, high = wilson_interval(successes, total)
    return {
        "successes": successes,
        "n": total,
        "rate": round(successes / total, 4) if total else 0.0,
        "ci95": [round(low, 4), round(high, 4)],
    }


def _usage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = completion = cached = cache_write = 0
    for row in rows:
        usage = row.get("usage") or {}
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        cached += int(usage.get("cached_tokens") or 0)
        cache_write += int(usage.get("cache_write_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
        "cache_hit_rate": round(cached / prompt, 4) if prompt else 0.0,
    }


def _group(rows: list[dict[str, Any]], key) -> dict[Any, list[dict[str, Any]]]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


def _latency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [int(row.get("latency_ms") or 0) for row in rows]
    return {
        "median_ms": int(statistics.median(values)) if values else 0,
        "p95_ms": p95(values),
        "max_ms": max(values) if values else 0,
    }


def judge_confusion(rows: list[dict[str, Any]], split: str = "core") -> dict[str, Any]:
    """Confusion matrix for T0 cascade reviews.

    ``accepted`` is the judge's binary decision. ``success`` is the frozen
    grader's label. Only rows with both values are counted. ``split`` keeps the
    pre-registered core tasks separate from exploratory stress tasks.
    """
    pairs = [
        row
        for row in rows
        if row.get("tier") == "T0"
        and row.get("split", "core") == split
        and row.get("arm") == "free_cascade"
        and row.get("accepted") is not None
        and not row.get("error")
    ]
    tp = sum(1 for row in pairs if row["accepted"] and row["success"])
    fp = sum(1 for row in pairs if row["accepted"] and not row["success"])
    fn = sum(1 for row in pairs if not row["accepted"] and row["success"])
    tn = sum(1 for row in pairs if not row["accepted"] and not row["success"])
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    false_accept_rate = fp / len(pairs) if pairs else 0.0
    false_accept_given_wrong = fp / (fp + tn) if fp + tn else 0.0
    return {
        "n": len(pairs),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "false_accept_rate": round(false_accept_rate, 4),
        "false_accept_given_wrong": round(false_accept_given_wrong, 4),
        "escalation_rate": round((fn + tn) / len(pairs), 4) if pairs else 0.0,
    }


def _paired_t0(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_task: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.get("tier") == "T0" and not row.get("error"):
            by_task[row["task_id"]][row["arm"]].append(bool(row["success"]))
    both = ask_only = cascade_only = neither = 0
    for task_id, arms in by_task.items():
        ask = arms.get("free_ask", [])
        cascade = arms.get("free_cascade", [])
        for left, right in zip(ask, cascade):
            if left and right:
                both += 1
            elif left:
                ask_only += 1
            elif right:
                cascade_only += 1
            else:
                neither += 1
    return {
        "both_correct": both,
        "ask_only_correct": ask_only,
        "cascade_only_correct": cascade_only,
        "neither_correct": neither,
        "paired_reps": both + ask_only + cascade_only + neither,
    }


def summarize(rows: list[dict[str, Any]], metadata: dict[str, Any]) -> dict[str, Any]:
    tiers = _group(rows, lambda row: row["tier"])
    arms = _group(rows, lambda row: (row["tier"], row["arm"]))
    splits = _group(rows, lambda row: row.get("split", "core"))
    overall = _rate(sum(1 for row in rows if row["success"]), len(rows))
    overall["delivered"] = sum(1 for row in rows if row.get("delivered"))
    overall["delivered_rate"] = (
        round(overall["delivered"] / len(rows), 4) if rows else 0.0
    )

    by_tier = {}
    for tier, tier_rows in sorted(tiers.items()):
        rate = _rate(sum(1 for row in tier_rows if row["success"]), len(tier_rows))
        rate["delivered"] = sum(1 for row in tier_rows if row.get("delivered"))
        rate["delivered_rate"] = (
            round(rate["delivered"] / len(tier_rows), 4) if tier_rows else 0.0
        )
        by_tier[tier] = rate

    by_arm = {}
    for (tier, arm), arm_rows in sorted(arms.items()):
        rate = _rate(sum(1 for row in arm_rows if row["success"]), len(arm_rows))
        rate["delivered"] = sum(1 for row in arm_rows if row.get("delivered"))
        rate["delivered_rate"] = (
            round(rate["delivered"] / len(arm_rows), 4) if arm_rows else 0.0
        )
        rate["latency"] = _latency(arm_rows)
        rate["usage"] = _usage(arm_rows)
        by_arm[f"{tier}/{arm}"] = rate

    by_split = {}
    for split, split_rows in sorted(splits.items()):
        by_split[split] = _rate(
            sum(1 for row in split_rows if row["success"]), len(split_rows)
        )

    core_rows = [row for row in rows if row.get("split", "core") == "core"]
    stress_rows = [row for row in rows if row.get("split", "core") == "stress"]

    return {
        "run_id": metadata.get("run_id"),
        "started_at": metadata.get("started_at"),
        "finished_at": metadata.get("finished_at"),
        "git_commit": metadata.get("git_commit"),
        "server_version": metadata.get("server_version"),
        "n": len(rows),
        "overall": overall,
        "by_tier": by_tier,
        "by_arm": by_arm,
        "by_split": by_split,
        "judge": judge_confusion(core_rows, "core"),
        "judge_stress": judge_confusion(stress_rows, "stress"),
        "paired_t0": _paired_t0(core_rows),
        "usage": _usage(rows),
        "errors": sum(1 for row in rows if row.get("error")),
        "metadata": metadata,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def markdown(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Free-model benchmark — pilot report")
    lines.append("")
    lines.append(
        "This report is generated from `results.jsonl`. It measures free-model "
        "quality and judge behavior. It does **not** claim paid-token or USD "
        "savings; that requires the paid pilot in `bench/README.md`."
    )
    lines.append("")
    lines.append("## Run")
    lines.append("")
    lines.append(f"- Run id: `{summary['run_id']}`")
    lines.append(f"- Started: `{summary['started_at']}`")
    lines.append(f"- Finished: `{summary['finished_at']}`")
    lines.append(f"- Commit: `{summary['git_commit']}`")
    lines.append(f"- Server: `{summary['server_version']}`")
    lines.append(f"- Calls: `{summary['n']}`")
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    overall = summary["overall"]
    low, high = overall["ci95"]
    lines.append(
        f"- Task success: **{overall['successes']}/{overall['n']} "
        f"({_pct(overall['rate'])}, 95% Wilson CI {_pct(low)}–{_pct(high)})**"
    )
    lines.append(
        f"- Delivered without paid escalation: "
        f"**{overall['delivered']}/{overall['n']} ({_pct(overall['delivered_rate'])})**"
    )
    lines.append(f"- Errors: **{summary['errors']}**")
    lines.append("")
    lines.append("## By tier")
    lines.append("")
    lines.append("| Tier | Success | 95% CI | Delivered | n |")
    lines.append("|---|---:|---:|---:|---:|")
    for tier, values in summary["by_tier"].items():
        low, high = values["ci95"]
        lines.append(
            f"| {tier} | {values['successes']}/{values['n']} "
            f"({_pct(values['rate'])}) | {_pct(low)}–{_pct(high)} | "
            f"{values['delivered']}/{values['n']} | {values['n']} |"
        )
    lines.append("")
    lines.append("## By split")
    lines.append("")
    lines.append("| Split | Success | 95% CI | n |")
    lines.append("|---|---:|---:|---:|")
    for split, values in summary["by_split"].items():
        low, high = values["ci95"]
        lines.append(
            f"| {split} | {values['successes']}/{values['n']} "
            f"({_pct(values['rate'])}) | {_pct(low)}–{_pct(high)} | {values['n']} |"
        )
    lines.append("")
    lines.append("## By arm")
    lines.append("")
    lines.append("| Arm | Success | 95% CI | Delivered | Median latency | p95 | Prompt tokens | Cache hit |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, values in summary["by_arm"].items():
        low, high = values["ci95"]
        lines.append(
            f"| `{name}` | {values['successes']}/{values['n']} "
            f"({_pct(values['rate'])}) | {_pct(low)}–{_pct(high)} | "
            f"{values['delivered']}/{values['n']} | "
            f"{values['latency']['median_ms']} ms | {values['latency']['p95_ms']} ms | "
            f"{values['usage']['prompt_tokens']:,} | "
            f"{_pct(values['usage']['cache_hit_rate'])} |"
        )
    lines.append("")
    lines.append("## Judge calibration (T0 cascade)")
    lines.append("")
    judge = summary["judge"]
    lines.append(
        f"Decisions: **{judge['n']}** · TP {judge['tp']} · FP {judge['fp']} · "
        f"FN {judge['fn']} · TN {judge['tn']}"
    )
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Precision | {_pct(judge['precision'])} |")
    lines.append(f"| Recall | {_pct(judge['recall'])} |")
    lines.append(f"| False accept (all decisions) | {_pct(judge['false_accept_rate'])} |")
    lines.append(
        f"| False accept (given a wrong answer) | "
        f"{_pct(judge['false_accept_given_wrong'])} |"
    )
    lines.append(f"| Escalation rate | {_pct(judge['escalation_rate'])} |")
    lines.append("")
    stress = summary.get("judge_stress") or {}
    if stress.get("n"):
        lines.append("## T0 stress (exploratory)")
        lines.append("")
        lines.append(
            "These tasks were added after the core T0 ceiling was observed. "
            "They are reported separately and are not part of H1."
        )
        lines.append("")
        lines.append(
            f"Decisions: **{stress['n']}** · TP {stress['tp']} · FP {stress['fp']} · "
            f"FN {stress['fn']} · TN {stress['tn']} · precision "
            f"{_pct(stress['precision'])} · recall {_pct(stress['recall'])} · "
            f"false accept {_pct(stress['false_accept_rate'])}"
        )
        lines.append("")
    lines.append("## T0 paired outcomes")
    lines.append("")
    paired = summary["paired_t0"]
    lines.append(
        f"Paired repetitions: **{paired['paired_reps']}** · both correct "
        f"{paired['both_correct']} · free_ask only {paired['ask_only_correct']} · "
        f"free_cascade only {paired['cascade_only_correct']} · neither "
        f"{paired['neither_correct']}."
    )
    lines.append("")
    lines.append("## Interpretation rules")
    lines.append("")
    lines.append(
        "- This is a pilot. Confidence intervals are wide; do not treat a "
        "single task as a stable model ranking."
    )
    lines.append(
        "- T2 tasks are a conservative policy screen, not a safety "
        "certification. Their grader is heuristic and is reported separately."
    )
    lines.append(
        "- T1 tasks are real regressions in the shipped server and are graded "
        "by the hidden 39-test suite. The model returns a function, not a test "
        "edit, so editing tests cannot change the grade."
    )
    lines.append(
        "- Cache hit rate is an observation, not a cost saving. Free NIM calls "
        "have no per-token price in this benchmark."
    )
    lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append("python -m bench.run --out bench/results/<new-run> --text-reps 2")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write_summary(
    outdir: Path,
    rows: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    summary = summarize(rows, metadata)
    (outdir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (outdir / "summary.md").write_text(markdown(summary) + "\n", encoding="utf-8")
    return summary
