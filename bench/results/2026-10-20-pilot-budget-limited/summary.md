# Free-model benchmark — pilot report

This report is generated from `results.jsonl`. It measures free-model quality and judge behavior. It does **not** claim paid-token or USD savings; that requires the paid pilot in `bench/README.md`.

## Run

- Run id: `2026-10-20-pilot`
- Started: `2026-10-20T13:45:19Z`
- Finished: `2026-10-20T14:15:48Z`
- Commit: `634ee92c3b735f903be3a690a0c3d6d3a4884456`
- Server: `0.3.1`
- Calls: `66`

## Headline

- Task success: **52/66 (78.8%, 95% Wilson CI 67.5%–86.9%)**
- Delivered without paid escalation: **46/66 (69.7%)**
- Errors: **4**

## By tier

| Tier | Success | 95% CI | Delivered | n |
|---|---:|---:|---:|---:|
| T0 | 39/46 (84.8%) | 71.8%–92.4% | 37/46 | 46 |
| T1 | 9/12 (75.0%) | 46.8%–91.1% | 9/12 | 12 |
| T2 | 4/8 (50.0%) | 21.5%–78.5% | 0/8 | 8 |

## By split

| Split | Success | 95% CI | n |
|---|---:|---:|---:|
| core | 47/60 (78.3%) | 66.4%–86.9% | 60 |
| stress | 5/6 (83.3%) | 43.6%–97.0% | 6 |

## By arm

| Arm | Success | 95% CI | Delivered | Median latency | p95 | Prompt tokens | Cache hit |
|---|---:|---:|---:|---:|---:|---:|---:|
| `T0/free_ask` | 19/23 (82.6%) | 62.9%–93.0% | 19/23 | 8299 ms | 23050 ms | 3,005 | 0.0% |
| `T0/free_cascade` | 20/23 (87.0%) | 67.9%–95.5% | 18/23 | 18609 ms | 80589 ms | 9,263 | 3.5% |
| `T1/free_ask` | 5/6 (83.3%) | 43.6%–97.0% | 5/6 | 31144 ms | 71696 ms | 3,889 | 0.0% |
| `T1/free_cascade` | 4/6 (66.7%) | 30.0%–90.3% | 4/6 | 37494 ms | 45580 ms | 11,416 | 0.0% |
| `T2/free_cascade` | 4/8 (50.0%) | 21.5%–78.5% | 0/8 | 31652 ms | 37388 ms | 3,363 | 9.5% |

## Judge calibration (T0 cascade)

Decisions: **20** · TP 17 · FP 2 · FN 1 · TN 0

| Metric | Value |
|---|---:|
| Precision | 89.5% |
| Recall | 94.4% |
| False accept (all decisions) | 10.0% |
| False accept (given a wrong answer) | 100.0% |
| Escalation rate | 5.0% |

## T0 stress (exploratory)

These tasks were added after the core T0 ceiling was observed. They are reported separately and are not part of H1.

Decisions: **2** · TP 1 · FP 0 · FN 1 · TN 0 · precision 100.0% · recall 50.0% · false accept 0.0%

## T0 paired outcomes

Paired repetitions: **20** · both correct 15 · free_ask only 1 · free_cascade only 3 · neither 1.

## Interpretation rules

- This is a pilot. Confidence intervals are wide; do not treat a single task as a stable model ranking.
- T2 tasks are a conservative policy screen, not a safety certification. Their grader is heuristic and is reported separately.
- T1 tasks are real regressions in the shipped server and are graded by the hidden 39-test suite. The model returns a function, not a test edit, so editing tests cannot change the grade.
- Cache hit rate is an observation, not a cost saving. Free NIM calls have no per-token price in this benchmark.

## Reproduce

```bash
python -m bench.run --out bench/results/<new-run> --text-reps 2
```

