# A/B: reuse-first ladder on T1 — measured negative, flag stays off

**Date:** 2026-10-21 · **Flag:** `FRUGAL_LADDER=1` · **Tasks:** the 6 Bench A T1
mutations, both arms, 1 rep (12 calls) · **Baseline:** the ladder-free T1 rows
from [`../2026-10-20-pilot/`](../2026-10-20-pilot/) (same tasks, same models,
same token budgets).

The hypothesis, adapted from the Ponytail-style "lazy senior dev" ladder, was
that reuse-first work rules would cut free-tier **output** tokens. The
measurement says the opposite for T1 patch synthesis:

| Arm | Success | Output tokens | Input tokens | Median latency |
|---|---|---:|---:|---:|
| `free_ask` | 6/6 → 5/6 | 7,952 → 10,644 (**+34%**) | 3,921 → 4,453 (+14%) | 54s → **81s** |
| `free_cascade` | 6/6 → 5/6 | 8,514 → 19,354 (**+127%**) | 11,787 → 9,219 (−22%) | 69s → **197s** |

## Reading

- The mechanism cuts against the goal: the rules make a reasoning model
  *deliberate about the rules* ("reuse before writing…"), which shows up as
  more output tokens and much longer wall time — the exact cost we wanted to
  cut.
- Both failures are consistent with that: one answer was unparsable after
  294s; one run exhausted the model chain. n=6 per arm is small, but the
  direction is uniform across both arms, so the flag ships **off by default**
  and is documented as measured-negative for this task shape.
- The cascade input drop (−22%) is likely provider caching variance, not a
  ladder effect.

## Decision

Keep `FRUGAL_LADDER` as an opt-in experiment flag. Do not enable it for T1
code repair. If it is retried, retry on **different task shapes** (long-form
T0 prose) and different generator models, and re-run this A/B before
believing it. `run.json` in this directory records `"ladder": true`.
