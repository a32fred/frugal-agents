# Roadmap

Each phase has a measurable acceptance criterion. Do not start the next phase until the current one passes.

## Phase 0 — Baseline (2 hours)

**Deliverable:** one week of paid-token usage classified by task type.

- Tag sessions as T0/T1/T2 as you work.
- Record prompt and completion tokens per task.
- Count how many tasks are mechanical versus ambiguous.

**Acceptance:** you can state the share of tokens spent on T0 and T1 work. Without this number, every savings estimate is a guess.

**Rollback:** none needed; this is measurement only.

## Phase 1 — MCP server (1 day)

**Deliverable:** `free_ask`, `free_agent`, and `free_cascade` registered in your agent.

- Install the server and verify all three tools with the smoke test.
- Register it in `~/.codex/config.toml` (or your client's MCP config).
- Add the routing policy to `AGENTS.md`.
- Use it for commit messages, summaries, and one isolated refactor.

**Acceptance:**

- A commit message and a simple refactor are produced entirely on the free tier.
- Escalation rate ≤ 20% on the sampled tasks.
- No secrets appear in any free-tier prompt.
- The paid model still reviews every diff before merge.

**Rollback:** remove the `[mcp_servers.free-models]` block. No other state changes.

## Phase 2 — Calibration (half day)

**Deliverable:** tuned thresholds, task-specific checklists, and a working fallback chain.

- Add checklists for the task types you actually run (code, tests, prose, config).
- Calibrate the threshold so that escalation lands in the 10–20% band.
- Verify redaction on representative prompts.
- Force a 410 by requesting a retired model and confirm the fallback works.

**Acceptance:**

- Zero empty answers in a 50-task sample.
- Zero credential leaks in a redaction test suite.
- Escalation rate inside the target band.

**Rollback:** thresholds and prompts are configuration; revert the values.

## Phase 3 — Parallel workers (half day)

**Deliverable:** paid coordinator plus free workers in separate worktrees.

- Coordinator decomposes a batch (for example, ten small fixes) into independent tasks.
- Each worker runs `opencode run -m nim/<model>` in its own worktree.
- Coordinator reviews diffs only and runs the test suite.

**Acceptance:**

- Ten independent fixes complete without conflicting edits.
- Paid review is limited to diffs and failures.
- Total wall-clock time is not worse than sequential paid work.

**Rollback:** stop using the coordinator recipe; workers are ordinary agent sessions.

## Phase 4 — Optional: free Codex profile (half day)

**Deliverable:** a `free` profile backed by a Responses↔Chat proxy.

- Run LiteLLM (or a bridge) locally.
- Define a Codex profile pointing at it.
- Use it only for chores, never for T2.

**Acceptance:** a complete chore session runs on the free profile with acceptable tool-use behavior.

**Rollback:** delete the profile and stop the container.

## Phase 5 — Optional: inverted orchestration (half day)

**Deliverable:** a free orchestrator that calls the paid model only for hard sub-questions.

- Define escalation criteria the free orchestrator can apply.
- Cap paid calls per session.
- Log every escalation with a reason.

**Acceptance:** a typical session spends <20% of its tokens on the paid model with no quality regression in review.

**Rollback:** run the paid agent directly again.

## Success metrics (two weeks after Phase 1)

| Metric | Target |
|---|---|
| Paid-token reduction | ≥40% after Phase 2; ≥60% after Phase 3 |
| Judge escalation rate | 10–20% |
| Defects reaching production from free work | 0 |
| Latency for T0/T1 | equal or better than paid baseline |
| Credential leaks | 0 |
| Free share of tasks | ≥50% |

## Non-goals

- Replacing the paid model for architecture or security decisions.
- Removing human review from shipping work.
- Maximizing the number of models in the chain. Two good ones beat five flaky ones.
- Building a general-purpose router. This is a policy plus a small tool, deliberately.

## Maintenance

- Re-validate the free catalog monthly; update `FRUGAL_CHAIN`.
- Re-run the regression suite after any model change.
- Review the ledger every two weeks.
- Keep the judge rubric versioned in git so score changes are explainable.
