# Free-model benchmark

This directory contains the reproducible benchmark harness for
frugal-agents. It answers a narrower question than “how much money does
routing save?”:

> On frozen tasks with programmatic oracles, how often is a free answer
> correct, how well does the free judge separate correct from incorrect
> answers, and where does the free tier fail?

The pilot is free-only. It does **not** claim paid-token or USD savings. A
savings claim needs a paid baseline, an explicit price table, and a confidence
interval; those are Bench B and are gated on an approved budget.

## Status

| Item | State |
|---|---|
| Harness | Implemented, unit-tested, `--validate-only` checks every real mutation |
| T0/T1/T2 free pilot | **Completed**; results in [`results/2026-10-20-pilot/`](results/2026-10-20-pilot/) |
| Judge calibration | Precision 94.4%, recall 89.5%, false-accept 5.0% on the 20 core T0 cascade decisions |
| Paid baseline | **Not run**; no budget approved |
| Savings claim | **Not made** |

Read `PREREGISTRATION.md` before interpreting a result. It freezes the
hypotheses and the analysis rules. A superseded first run with a too-small
output budget is kept in
[`results/2026-10-20-pilot-budget-limited/`](results/2026-10-20-pilot-budget-limited/)
for audit only.

## What is measured

### T0 — deterministic text tasks (10 core + 3 stress)

| Task | Oracle |
|---|---|
| `t0_regex_iso_date` | Regex accepts/rejects fixed date strings |
| `t0_regex_semver` | Regex accepts/rejects SemVer edge cases |
| `t0_json_log_extract` | Exact JSON object from a log line |
| `t0_json_aggregate` | Exact token totals by team |
| `t0_commit_retry` | Conventional Commits subject + topic term |
| `t0_commit_docs` | Conventional Commits subject + topic term |
| `t0_shell_branch` | Allow-listed git command |
| `t0_json_sort` | Exact JSON with recursively sorted keys |
| `t0_regex_email` | Regex accepts/rejects fixed addresses |
| `t0_json_order_extract` | Exact JSON from noisy text |
| `t0s_regex_semver_full` | Full SemVer 2.0.0 syntax (stress) |
| `t0s_json_nested` | Decode escaped quotes in nested JSON (stress) |
| `t0s_shell_tracked_python` | Git-tracked Python count, no `find` (stress) |

The stress tasks were added before the full pilot after the core set hit a
ceiling. They are reported separately and are not part of H1. See
`PREREGISTRATION.md` for the deviation.

Arms: `free_ask` (one completion) and `free_cascade` (generator + independent
judge). Two repetitions per core task, one per stress task. The cascade's
`accepted` decision is compared with the grader's correctness label, producing
the judge confusion matrix.

### T1 — real code repair (6)

Each task applies one reviewed mutation to the shipped
`mcp/free-models/server.py` and proves that the **real 39-test suite** fails.
The model sees:

- the mutated function, and
- a small visible regression test.

It must return only the corrected top-level function. The harness inserts the
function, replaces the visible test with the **hidden** shipped suite, and
runs all 39 tests. Editing a test cannot help because the visible test is not
the grading test.

Arms: `free_ask` and `free_cascade` patch synthesis. One repetition per task.
This is a code-reasoning benchmark, not an agentic tool-use benchmark.

### T2 — policy screens (4)

TLS verification disabled in production, production `.env` printed to CI,
hard-coded admin backdoor, and a database decision made without requirements.
The screen passes on escalation, a scoped question, or a refusal, and fails if
an unsafe pattern appears in the answer. It is a heuristic screen, not a
safety certification; results are reported separately.

## Headline results (free pilot, 2026-10-20)

From [`results/2026-10-20-pilot/summary.md`](results/2026-10-20-pilot/summary.md):

Run context: generator `openai/gpt-oss-20b`, judge `google/gemma-4-31b-it`,
66 calls, 2026-10-20.

| Metric | Value |
|---|---|
| Task success, all 66 calls | 54/66 (81.8%, 95% CI 70.9–89.3%) |
| T0 core + stress | 40/46 (87.0%) |
| T1 code repair | 12/12 (100.0%) |
| T2 policy screen | 2/8 (25.0%) |
| Judge precision on core T0 | 94.4% (17 TP, 1 FP) |
| Judge recall on core T0 | 89.5% (2 false rejects) |
| False accept, core T0 | 5.0% (1 of 20 decisions) |

How to read this:

- **T1 is strong on this task shape.** A free model repaired every real
  regression when it only had to return one function and the hidden suite
  graded it. That supports routing *isolated, fully specified* repairs to free
  models. It does **not** show that the agentic `free_agent` path can do the
  same in a real repository.
- **T2 is the weak point.** The generator produced unsafe code or a premature
  decision in 6 of 8 screens. Treat the free tier as unsafe by default for
  production debugging, security, and ambiguous design.
- **The judge is good but not a gate.** Precision 94.4% and a 5.0% false-accept
  rate are close to, but not clearly above, the H1 thresholds. With 20 core
  decisions, one wrong answer changes the false-accept rate by 5 percentage
  points. Do not use this as a production-grade gate.
- **One stress cascade failed on transport**, not on content. The model chain
  timed out on that call; the grader never saw an answer.

## Run it

From the repository root:

```bash
# List tasks without making a call
python -m bench.run --list

# Validate references and every mutation; no API key, no network
python -m bench.run --validate-only

# Cheap smoke run: one T0 task, one repetition
python -m bench.run \
  --tiers T0 --tasks t0_json_log_extract --text-reps 1 \
  --out /tmp/fa-bench-smoke

# Full free pilot
python -m bench.run \
  --out bench/results/2026-10-20-pilot \
  --text-reps 2 --code-reps 1 --delay 0.5
```

Requirements: Python 3.10+, a valid `NIM_API_KEY` or key file, and network
access. T1 patch synthesis does **not** need the `opencode` CLI. The optional
`free_agent` path is intentionally outside this pilot.

The runner writes:

```
<out>/
  run.json         # commit, versions, configuration, timestamps
  results.jsonl    # one row per task/arm/repetition
  ledger.jsonl     # run-scoped calls; excluded from git
  summary.md       # generated tables and caveats
  summary.json     # machine-readable metrics
  work/            # optional retained T1 workspaces
```

The ledger path is set per run; the user's `~/.frugal-agents/ledger.jsonl` is
never touched.

## Grading rules

- No answer is graded by “looks plausible”. T0 uses exact JSON, regex cases,
  a command allow-list, or a Conventional Commits parser.
- T1 success requires the hidden 39-test suite to pass after the returned
  function is inserted.
- A T1 answer that returns prose, tests, imports, or an incomplete function
  fails at extraction and is recorded as an error, not a success.
- T2 fails if an unsafe pattern appears anywhere in the answer. The asymmetry
  is deliberate: false acceptance is worse than false escalation. This can
  penalize an answer that quotes the unsafe option only to reject it; that
  false-negative is recorded in the report and is preferred to a false pass.
- Transport errors stay in the denominator and are counted as failures/errors.

## Metrics

| Metric | Definition |
|---|---|
| Success | Frozen grader passed |
| Delivered | Success without paid escalation (`free_cascade` must also accept) |
| Judge precision | TP / (TP + FP), T0 cascade only |
| Judge recall | TP / (TP + FN), T0 cascade only |
| False accept (all) | FP / all judge decisions |
| False accept (given wrong) | FP / (FP + TN) |
| Paired difference | Direct-only vs cascade-only correct repetitions |
| Latency | Median and p95 wall-clock time, including free-tier queueing |
| Cache hit | Cached prompt tokens / prompt tokens, as observed |

Proportions use a 95% Wilson interval. The pilot does not report p-values.
Cache hit rate is not a dollar saving: free NIM calls have no per-token price
in this benchmark.

## Interpreting the result

Use the report as a calibration instrument, not a leaderboard:

- A single task is not a model ranking. Free-tier routing, queueing, and model
  fallbacks change between runs.
- The judge is only independent when `judge_independent` is true. If the judge
  fell back to the generator, the result is flagged and should not be treated
  as independent review.
- T2 is a screen. It cannot establish that a model is safe.
- A free-tier win is not a savings claim. The paid baseline may use fewer
  tokens, better caching, or a different quality level.

## Bench B — paid agentic pilot (not run)

Bench A is a precondition, not a substitute. Bench B measures the actual
routing trade-off:

- **Tasks:** 8–12 repository issues with deterministic tests, drawn from the
  same public repository.
- **Arms:** `all-paid` (Codex solves directly) vs `routed` (T0/T1 free,
  T2 paid, with escalation).
- **Measurement:** `codex exec --json` token usage plus the run-scoped free
  ledger; input, cached input, output, and reasoning tokens separately.
- **Primary metric:** paid tokens per successful task and quality
  non-inferiority. USD requires a dated price table and is reported only if
  the 95% CI lower bound on savings exceeds 20%.
- **Budget:** 8 tasks × 2 arms × 50k–250k tokens ≈ 0.8M–4M paid tokens, before
  retries. No budget has been approved.

Do not publish a USD savings number from Bench A. The free ledger records
metadata, not a bill, and cannot infer what a paid model would have consumed.

## Threats to validity

- **Small n.** The planned 20 T0 decisions give wide judge intervals. Treat
  H1 as a gate, not a precise estimate.
- **Free-tier variance.** Model aliases, rate limits, and fallbacks can change
  mid-run. `run.json` records the commit, versions, and configuration; it
  cannot freeze the provider.
- **Prompt sensitivity.** A single prompt wording per task can flatter or
  penalize a model. The tasks are frozen before the run and not tuned to a
  model.
- **Heuristic T2.** Policy screening is not a red-team exercise.
- **No agentic arm.** Tools, context management, and multi-step recovery are
  measured in Bench B, not here.

## Adding a task

1. Add it to `bench/tasks.py` with a unique id and a deterministic grader.
2. Add reference good/bad outputs and run `python -m bench.run --validate-only`.
3. For T1, add a mutation, target function, and visible test; validation must
   prove the clean source passes, the mutation fails, and the reference patch
   repairs it.
4. Do not change an existing task after seeing results. Add a new task and
   record the change in `PREREGISTRATION.md`.

## Files

- `tasks.py` — frozen tasks, mutations, visible tests
- `graders.py` — deterministic graders and JSON extraction
- `runner.py` — execution, patch insertion, hidden-suite grading
- `report.py` — Wilson intervals, judge confusion, Markdown report
- `run.py` — CLI
- `tests/` — harness unit tests
- `results/` — run artifacts published with the repository

## Bench B — paid vs routed (subscription)

Bench B reuses the same discipline with a real trade-off: the paid baseline is
the operator's actual daily tiering (`gpt-5.6-sol` / `gpt-6.1-sol` /
`gpt-6-astra`), executed with the subscription-backed `codex` CLI. The routed
arm hands T0/T1 to the free tier and escalates failures to the paid model.

- Methodology and frozen decision rules: [`BENCH_B.md`](BENCH_B.md)
- Smoke tasks and reference solutions: `b_tasks.py`
- Runner and CLI: `b_runner.py` / `b_run.py`
- Shadow pricing: `b_prices.example.json` (USD only as an estimate; the
  subscription consumes quota, not dollars)

```bash
python -m bench.b_run --validate-only
python -m bench.b_run --out bench/results/b-smoke --arms paid_mirror,routed
```
