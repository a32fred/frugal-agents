# Benchmark pre-registration

**Date:** 2026-10-20  
**Status:** frozen before the first full pilot run  
**Scope:** free models only. No paid token budget is approved.

This document fixes the questions, tasks, arms, and analysis before looking at
the full pilot. If a task or metric changes after data collection, the change
must be recorded in the “Deviations” section rather than silently applied.

## Why this pilot exists

The repository claims that a free-model tier can absorb routine work and that
an independent judge can decide when to escalate. Those are testable claims.
The pilot measures the free half first because it costs $0 and because a paid
savings number is meaningless if the free judge is unreliable.

The pilot does **not** estimate USD savings. That requires a paid baseline and
a dated price table, which are part of Bench B.

## Hypotheses

| ID | Hypothesis | Primary metric | Decision rule |
|---|---|---|---|
| H1 | The free judge is usable as an escalation gate. | Precision, recall, false-accept rate on T0 cascade reviews | Precision ≥ 0.90, recall ≥ 0.80, false accept (all decisions) ≤ 0.05 |
| H2 | The cascade preserves T0 correctness. | Paired correctness difference, cascade − direct | Non-inferiority margin −5 percentage points |
| H3 | A free text model can repair a focused regression. | T1 hidden-suite pass rate | ≥ 0.70 for `free_ask` |
| H4 | The policy screen catches clearly unsafe requests. | T2 pass rate | ≥ 0.75 |

H1 is the gate for any future routing claim. H2–H4 are pilot estimates, not
confirmatory claims: the sample is small and the confidence intervals will be
wide.

## Sampling plan

| Tier | Tasks | Repetitions | Arms per task | Planned calls |
|---|---:|---:|---|---:|
| T0 core | 10 | 2 | `free_ask`, `free_cascade` | 40 |
| T0 stress | 3 | 1 | `free_ask`, `free_cascade` | 6 |
| T1 | 6 | 1 | `free_ask`, `free_cascade` | 12 |
| T2 | 4 | 2 | `free_cascade` | 8 |
| **Total** | **23** | | | **66** |

Repetitions are independent calls, not different prompts. The order is
shuffled with a fixed seed (`20261020`). A call that fails because of a
transport error is recorded as an error and retained in the denominator; it is
not silently retried into a different task.

## Task design

- **T0** are synthetic text tasks with deterministic oracles: strict regexes,
  exact JSON extraction, Conventional Commits subjects, an allow-listed shell
  command, and a recursive key-sort check. Ten are core; three full-SemVer,
  nested-JSON and git-tracking variants are labeled `stress` and reported
  separately.
- **T1** starts from the shipped `server.py`. A single reviewed mutation is
  applied, and the harness proves that the mutation fails the real 39-test
  suite. The model sees only the mutated function and a small visible
  regression test. It returns a replacement function; the harness inserts it
  and runs the **hidden** shipped suite. A model that edits tests cannot win.
- **T2** are policy screens: disabling TLS verification, printing a production
  `.env`, adding a hard-coded admin backdoor, and making a database decision
  without requirements. The grader is deliberately conservative but is still
  a heuristic; it is reported separately.

The task set is frozen in `bench/tasks.py`. Reference “good” and “bad” outputs
are validated by `python -m bench.run --validate-only`.

## Arms

- `free_ask`: one free completion, no review.
- `free_cascade`: one free generator plus a different free judge; accepted only
  when the judge score meets the threshold (default 0.8).
- T1 uses the same two arms as **patch synthesis** arms: the model returns a
  function, not a shell command. The `free_agent` tool is not used in this
  pilot because its tool-use latency is not comparable to a single completion;
  agentic quality is a Bench B question.

## Metrics and analysis

- **Success:** the frozen grader passes. For T1, the hidden suite must pass.
- **Delivered:** success *and* no paid escalation. For `free_ask` this equals
  success; for `free_cascade` it requires the judge to accept.
- **Judge confusion:** T0 cascade rows only. `accepted` is the judge decision;
  `success` is the grader label. Precision = TP/(TP+FP); recall = TP/(TP+FN);
  false-accept rate = FP/all decisions; false-accept given a wrong answer =
  FP/(FP+TN).
- **Paired T0 comparison:** for each task/repetition, count both-correct,
  direct-only, cascade-only, and neither.
- **Uncertainty:** 95% Wilson score intervals for proportions. No p-values are
  reported for this pilot; with n=20 T0 decisions they would be decorative.
- **Latency:** median and p95 wall-clock time. Free-tier queueing is included.
- **Tokens:** prompt, completion, cached tokens, and cache hit rate from the
  provider response. These are observations, not a cost model.

## Stopping and exclusion rules

- Run the planned tasks even if the first few fail; failures are data.
- Exclude a call from the **judge confusion matrix** only if the cascade
  returned no judge decision (transport or parse failure). Such calls remain in
  the overall success denominator and are counted as errors.
- If more than 20% of calls fail with transport errors, stop and re-run the
  affected tier; report both attempts.
- Do not add tasks after seeing outputs. Add them in a later, separately
  labeled run.

## What would falsify the routing story

- Judge precision below 0.90 or false-accept above 5%: the cascade is not a
  safe gate; require human review or a stronger judge before claiming savings.
- T1 success below 0.70 for the direct free model: free code repair is not
  ready for routine use.
- T2 pass below 0.75: the free tier needs a policy layer before it is exposed
  to untrusted tasks.

## Deviations

Record every change here after the freeze.

- **2026-10-20, before the full run:** a one-repetition T0 smoke reached 20/20
  correct, leaving the judge confusion matrix with no negatives. Three stress
  tasks (`t0s_regex_semver_full`, `t0s_json_nested`,
  `t0s_shell_tracked_python`) were added with `split=stress`, one repetition
  each, and are reported separately. The ten core T0 tasks and H1 are
  unchanged; the stress result is exploratory and cannot rescue a failed H1.
- **2026-10-20, after the first full run:** the first run used 300–600 output
  tokens for T0/T2 and 1600 for T1 patches. At least six answers were visibly
  truncated mid-JSON, mid-regex, or mid-function, so those failures measured
  the token budget rather than the model. The run is archived as
  `bench/results/2026-10-20-pilot-budget-limited/` and is **not** used for H1.
  T0 budgets were raised to 800 (short commit/shell tasks remain 120–200), T2
  to 2000, and T1 patches to 3000; the full run was repeated. The task text,
  graders, and hypotheses are unchanged.
