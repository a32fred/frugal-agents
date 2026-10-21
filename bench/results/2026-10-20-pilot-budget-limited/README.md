# Archived first full run — budget-limited

This directory is the first full pilot, kept for audit. **Do not use it for the
H1 judge gate.**

The run used 300–600 output tokens for T0/T2 and 1600 for T1 patches. Several
answers were visibly truncated mid-JSON, mid-regex, or mid-function, so those
failures reflect the completion budget rather than the model's answer. The
budgets were raised and the full run was repeated in
`../2026-10-20-pilot/`.

The raw `results.jsonl` is intentionally preserved. `work/` and the run ledger
are excluded from git.
