# Playbook: failure modes and mitigations

A cascade router is easy to demo and easy to get wrong. This is the list of ways it fails in practice, with the control that addresses each one.

## 1. Protocol mismatch

**Problem.** Codex CLI speaks the Responses API. Most free providers expose only `/chat/completions`. Pointing Codex at a hosted NIM endpoint fails before the first token.

**Mitigation.** Delegate through MCP (default), or run a Responses↔Chat proxy and define a dedicated Codex profile. Never assume a provider supports Responses because it advertises "OpenAI-compatible".

**Detection.** Connection errors at startup, or a provider rejecting the request shape.

## 2. Free-tier churn

**Problem.** Free catalogs rotate. Models disappear and return HTTP 410; quotas and rate limits change without notice.

**Mitigation.** Ordered fallback chain, health cooldown after failure, and a model list that is data, not code (`FRUGAL_CHAIN`). Re-validate the catalog when failures cluster.

**Detection.** `fallbacks` field in each response; ledger entries with repeated failures.

## 3. Empty answers from reasoning models

**Problem.** A reasoning model can spend the whole `max_tokens` budget thinking and return `content: null`.

**Mitigation.** Retry once with at least 4096 tokens; read `reasoning_content` when present; never treat an empty answer as an accepted answer.

**Detection.** `empty content` in the error chain, or a judge score of 0 on a task that looked easy.

## 4. An unreliable judge

**Problem.** A judge that is too lenient ships bad work; one that is too strict doubles cost. This is the single largest risk in the literature.

**Mitigation.** Different model family from the generator; concrete scoring rubric; temperature 0; structured JSON; threshold calibrated per task type; monitor escalation rate and sample accepted work.

**Detection.** Escalation rate outside 10–20%; accepted answers later fixed by a human.

## 5. Handoff overhead

**Problem.** Sending the full conversation to the paid model can cost more than generating there in the first place.

**Mitigation.** Fixed delegation contract: task statement ≤ 20 lines, free answer, judge issues, relevant diff. No transcript, no logs, no full files unless essential.

**Detection.** Paid prompt tokens per escalation trending up.

## 6. Secrets and proprietary data

**Problem.** Free tiers are external services. Prompts may contain keys, customer data, or unreleased security work.

**Mitigation.** Policy first: free tiers never receive secrets, `.env` content, customer data, or security-sensitive diffs. Redaction as a backstop for accidental token shapes. Self-hosted endpoint for zero egress.

**Detection.** Audit the ledger's task previews (the ledger stores metadata, not prompt text, by design).

## 7. Latency at the margin

**Problem.** A rejection means two sequential generations. For interactive work this is visible.

**Mitigation.** Keep T0/T1 on fast models; make escalation rare; stream when the client supports it; prefer routing hard work straight to T2 instead of cascading into a rejection.

**Detection.** p95 latency in the ledger.

## 8. Invisible quality drift

**Problem.** Free models improve and regress. A pipeline that was safe last month may not be today.

**Mitigation.** Mandatory T2 review for anything shipping; weekly human sampling of accepted free work; pin model versions where the provider allows it; keep a small regression suite of representative tasks.

**Detection.** Sample review findings; re-run the regression suite after changing a model.

## 9. Operational fragility

**Problem.** A broken server silently sends everything back to the paid model — savings evaporate without an error.

**Mitigation.** The server is a single stdlib script with no daemon. Failures surface as `isError` tool results. Track the share of tasks handled free; alert when it drops.

**Detection.** Weekly free-share metric from the ledger.

## 10. The habit problem

**Problem.** The paid model keeps doing everything because that is the path of least resistance.

**Mitigation.** Make the free path the default in `AGENTS.md`; phrase the policy as an instruction, not a suggestion; review sessions for unnecessary paid generation.

**Detection.** Paid-token trend per task type.

## 11. Rate limits under parallelism

**Problem.** Free tiers enforce requests-per-minute limits. Parallel workers hit them quickly.

**Mitigation.** Bound concurrency; exponential backoff on 429; cache repeated questions (commit templates, boilerplate); stagger workers.

**Detection.** 429 counts in the ledger.

## 12. Context growth in long sessions

**Problem.** Cost grows with transcript length, not just with task difficulty. A long paid session can dwarf any routing saving.

**Mitigation.** New session per task; compact before switching topics; low reasoning effort for simple sessions; short, stable `AGENTS.md` so the cached prefix stays reusable; limit tool output.

**Detection.** Prompt tokens per request over time.

## Calibration loop

Run this every two weeks:

1. Export the ledger.
2. Compute: free share, escalation rate, accepted-answer score distribution, p95 latency.
3. Sample 10 accepted answers and review them by hand.
4. If escalation > 20%: the work is T2, or the judge is too strict.
5. If escalation < 5% and sampling finds defects: the judge is too lenient — tighten the rubric, not the threshold alone.
6. Update the tier table and the judge checklist; record the change in the changelog.

## Anti-patterns

- **Judge and generator from the same model.** Correlated errors; review becomes ceremony.
- **Threshold as the only control.** A lenient judge with a high threshold still accepts bad work at 0.81.
- **Cascading T2 work.** If the task is ambiguous, escalate first; do not spend two free generations discovering that.
- **Sending the transcript "for context".** It destroys the economics.
- **Trusting a single benchmark number.** Measure your own distribution.
- **Removing the human gate.** The cascade reduces cost; it does not remove accountability.
