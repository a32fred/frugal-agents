# Free-model benchmark — pilot report

This report is generated from `results.jsonl`. It measures free-model quality and judge behavior. It does **not** claim paid-token or USD savings; that requires the paid pilot in `bench/README.md`.

## Run

- Run id: `2026-10-20-pilot`
- Started: `2026-10-20T18:06:12Z`
- Finished: `2026-10-20T18:42:53Z`
- Commit: `634ee92c3b735f903be3a690a0c3d6d3a4884456`
- Server: `0.3.1`
- Calls: `66`

## Headline

- Task success: **54/66 (81.8%, 95% Wilson CI 70.9%–89.3%)**
- Delivered without paid escalation: **49/66 (74.2%)**
- Errors: **1**

## By tier

| Tier | Success | 95% CI | Delivered | n |
|---|---:|---:|---:|---:|
| T0 | 40/46 (87.0%) | 74.3%–93.9% | 37/46 | 46 |
| T1 | 12/12 (100.0%) | 75.8%–100.0% | 12/12 | 12 |
| T2 | 2/8 (25.0%) | 7.1%–59.1% | 0/8 | 8 |

## By split

| Split | Success | 95% CI | n |
|---|---:|---:|---:|
| core | 50/60 (83.3%) | 72.0%–90.7% | 60 |
| stress | 4/6 (66.7%) | 30.0%–90.3% | 6 |

## By arm

| Arm | Success | 95% CI | Delivered | Median latency | p95 | Prompt tokens | Cache hit |
|---|---:|---:|---:|---:|---:|---:|---:|
| `T0/free_ask` | 20/23 (87.0%) | 67.9%–95.5% | 20/23 | 7281 ms | 17436 ms | 2,963 | 0.0% |
| `T0/free_cascade` | 20/23 (87.0%) | 67.9%–95.5% | 17/23 | 16598 ms | 48252 ms | 9,279 | 3.5% |
| `T1/free_ask` | 6/6 (100.0%) | 61.0%–100.0% | 6/6 | 37886 ms | 108439 ms | 3,921 | 0.0% |
| `T1/free_cascade` | 6/6 (100.0%) | 61.0%–100.0% | 6/6 | 53997 ms | 299885 ms | 11,787 | 10.9% |
| `T2/free_cascade` | 2/8 (25.0%) | 7.1%–59.1% | 0/8 | 33227 ms | 59582 ms | 6,885 | 4.7% |

## Judge calibration (T0 cascade)

Decisions: **20** · TP 17 · FP 1 · FN 2 · TN 0

| Metric | Value |
|---|---:|
| Precision | 94.4% |
| Recall | 89.5% |
| False accept (all decisions) | 5.0% |
| False accept (given a wrong answer) | 100.0% |
| Escalation rate | 10.0% |

## T0 stress (exploratory)

These tasks were added after the core T0 ceiling was observed. They are reported separately and are not part of H1.

Decisions: **2** · TP 0 · FP 1 · FN 1 · TN 0 · precision 0.0% · recall 0.0% · false accept 50.0%

## T0 paired outcomes

Paired repetitions: **20** · both correct 17 · free_ask only 0 · free_cascade only 2 · neither 1.

## Interpretation rules

- This is a pilot. Confidence intervals are wide; do not treat a single task as a stable model ranking.
- T2 tasks are a conservative policy screen, not a safety certification. Their grader is heuristic and is reported separately.
- T1 tasks are real regressions in the shipped server and are graded by the hidden 39-test suite. The model returns a function, not a test edit, so editing tests cannot change the grade.
- Cache hit rate is an observation, not a cost saving. Free NIM calls have no per-token price in this benchmark.

## Reproduce

```bash
python -m bench.run --out bench/results/<new-run> --text-reps 2
```

