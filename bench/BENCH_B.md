# Bench B pre-registration — paid vs routed (subscription quota)

**Date frozen:** 2026-10-20
**Status:** harness implemented; smoke not yet run
**Billing model:** OpenAI subscription (ChatGPT plan) via the `codex` CLI —
quota, not per-token USD. API keys are not used for the paid arm.

## Why this exists

Bench A measured the free tier in isolation: task success and judge
calibration. Bench B measures the actual routing trade-off on repository
work: how much paid-model usage the free tier absorbs, what escalation costs,
and whether quality survives.

## Arms and models

The paid baseline mirrors the operator's real daily tiering. That is the
honest comparison: "what I would have done anyway."

| Arm | T0 | T1 | T2 | Purpose |
|---|---|---|---|---|
| `paid_mirror` | `gpt-5.6-sol` | `gpt-6.1-sol` | `gpt-6-astra` | Baseline = real usage |
| `paid_single` | `gpt-6.1-sol` | `gpt-6.1-sol` | `gpt-6.1-sol` | Isolates routing from model choice |
| `routed` | free → escalate to `paid_mirror` model | free → escalate | escalate to `paid_mirror` model | The routing hypothesis |

Free tier (routed arm): generator `openai/gpt-oss-20b` via `free_agent`
(`opencode run`, NIM). The judge pair from Bench A is not used here; Bench B
grades with the hidden test suite, which is stronger than an LLM judge.

Escalation is part of the design, not a failure of it: a routed task that the
free tier cannot finish is handed to the paid model, and those paid tokens are
counted. A routed arm that escalates everything is a failed routing
hypothesis, not a broken harness.

## Execution contract

- Paid calls: `codex exec --json --ephemeral --skip-git-repo-check -s
  workspace-write -C <workdir> -m <model> -` with the prompt on stdin.
- Token accounting: `turn.completed.usage` events summed per task
  (`input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
  `output_tokens`, `reasoning_output_tokens`).
- Free execution: `free_agent` with the task prompt on stdin, process-group
  cancellation, per-call timeout.
- Verification: the shipped test suite must pass and the test files must be
  byte-identical to the originals. Editing tests is a failure in every arm.
- Every run gets a run-scoped ledger and `run.json` with commit, models,
  timeouts, and price-table provenance.

## Metrics

| Metric | Definition |
|---|---|
| Success | Hidden suite passes, tests unedited |
| Absorption | Share of routed tasks solved with zero paid tokens |
| Paid tokens per successful task | `input + cached + output` on the paid arm (or on escalations) |
| Paid-token reduction | `1 − routed/paid_mirror`, with Wilson CI on the paired proportions |
| Quality non-inferiority | Routed success ≥ paid success − 5 pp |
| Shadow USD | Only with a complete, dated price table; labeled as an estimate |

## Subscription accounting

The paid arm consumes plan quota, not dollars. Therefore:

1. The primary unit of cost is **paid tokens per task**, not USD.
2. A **shadow USD** figure may be computed from `b_prices.json` (API prices,
   dated). It is an estimate of what the same tokens would cost on metered
   billing and must never be presented as a bill or a realized saving.
3. Weekly/hourly quota limits are reported as observations (call failures with
   quota errors), not converted to dollars.

## Sampling plan

| Phase | Tasks | Arms | Calls | Purpose |
|---|---:|---|---:|---|
| Smoke | 2 (T1) | `paid_mirror`, `routed` | ~4–6 | Validate harness, parse, escalation |
| Full | 8–12 (T0/T1/T2 mix) | all three | 16–36+ | The measured result |

Smoke tasks live in `b_tasks.py` (`b1_slugify`, `b2_backoff`) and are validated
to be broken initially and solvable by the reference solution
(`python -m bench.b_run --validate-only`).

## Decision rules

- Proceed to the full run only if the smoke shows: no harness crashes, usage
  parsing non-zero on every paid call, escalation counted correctly, and at
  least one routed task absorbed by the free tier (or an understood failure).
- Claim routing value only if paid-token reduction vs `paid_mirror` is ≥ 20%
  at the 95% CI lower bound **and** quality is non-inferior.
- If `paid_single` and `paid_mirror` differ materially, report both and prefer
  `paid_mirror` for the headline (it is the real counterfactual).

## Threats to validity

- **Tiny smoke sample.** The smoke exists to validate the pipeline, not to
  claim savings.
- **Codex harness overhead.** A trivial call costs ~25k input tokens; small
  tasks inherit that floor, which *favors* the routed arm. Report it.
- **Subscription model drift.** Model aliases and rate limits can change
  mid-run; `run.json` records what was requested, not what the provider served.
- **Task size.** These tasks are small by design; absorption on 2-file tasks
  does not extrapolate to 20-file refactors. The full task set must include
  harder T1/T2 work.
- **Non-sandboxed paid execution.** `workspace-write` sandbox is enabled, but
  review every patch before trusting it; see `SECURITY.md`.

## Deviations

Record every change here after the freeze.

- None yet.
