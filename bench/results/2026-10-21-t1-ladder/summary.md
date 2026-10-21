# Free-model benchmark — pilot report

This report is generated from `results.jsonl`. It measures free-model quality and judge behavior. It does **not** claim paid-token or USD savings; that requires the paid pilot in `bench/README.md`.

## Run

- Run id: `2026-10-21-t1-ladder`
- Started: `2026-10-21T20:05:34Z`
- Finished: `2026-10-21T20:36:13Z`
- Commit: `bbea1d7b61bb94ff20880dce40bbd1685c46364a`
- Server: `0.3.1`
- Calls: `12`

## Headline

- Task success: **10/12 (83.3%, 95% Wilson CI 55.2%–95.3%)**
- Delivered without paid escalation: **10/12 (83.3%)**
- Errors: **2**

## By tier

| Tier | Success | 95% CI | Delivered | n |
|---|---:|---:|---:|---:|
| T1 | 10/12 (83.3%) | 55.2%–95.3% | 10/12 | 12 |

## By split

| Split | Success | 95% CI | n |
|---|---:|---:|---:|
| core | 10/12 (83.3%) | 55.2%–95.3% | 12 |

## By arm

| Arm | Success | 95% CI | Delivered | Median latency | p95 | Prompt tokens | Cache hit |
|---|---:|---:|---:|---:|---:|---:|---:|
| `T1/free_ask` | 5/6 (83.3%) | 43.6%–97.0% | 5/6 | 77408 ms | 294356 ms | 4,453 | 0.0% |
| `T1/free_cascade` | 5/6 (83.3%) | 43.6%–97.0% | 5/6 | 189655 ms | 230824 ms | 9,219 | 9.7% |

## Judge calibration (T0 cascade)

Decisions: **0** · TP 0 · FP 0 · FN 0 · TN 0

| Metric | Value |
|---|---:|
| Precision | 0.0% |
| Recall | 0.0% |
| False accept (all decisions) | 0.0% |
| False accept (given a wrong answer) | 0.0% |
| Escalation rate | 0.0% |

## T0 paired outcomes

Paired repetitions: **0** · both correct 0 · free_ask only 0 · free_cascade only 0 · neither 0.

## Interpretation rules

- This is a pilot. Confidence intervals are wide; do not treat a single task as a stable model ranking.
- T2 tasks are a conservative policy screen, not a safety certification. Their grader is heuristic and is reported separately.
- T1 tasks are real regressions in the shipped server and are graded by the hidden 39-test suite. The model returns a function, not a test edit, so editing tests cannot change the grade.
- Cache hit rate is an observation, not a cost saving. Free NIM calls have no per-token price in this benchmark.

## Reproduce

```bash
python -m bench.run --out bench/results/<new-run> --text-reps 2
```

