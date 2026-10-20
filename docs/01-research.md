# Research: routing and cascades for LLM agents

This document summarizes the evidence behind the tiering policy in this repository, and — more importantly — the limits of that evidence. Numbers below are the ones reported by the original authors; they are not reproduced here.

## 1. The cost problem is a distribution problem

Agent workloads are long-tailed. A single session mixes trivial text (commit messages, renames, summaries) with genuinely hard work (a race condition in production, an ambiguous migration). Routing every token through the most expensive model prices the trivial majority as if it were the hard minority.

Two families of solutions exist:

- **Routing** — decide which model handles a request *before* generating.
- **Cascading** — generate cheaply, verify, and escalate only on failure.

They compose: route first, cascade inside the cheap tier.

## 2. FrugalGPT (Stanford, arXiv 2305.05176)

**Mechanism.** A cascade of increasingly expensive LLMs plus a learned scoring function that decides whether a candidate answer is good enough to stop.

**Reported result.** 50–98% cost reduction while matching the best single model's accuracy on the benchmarks studied.

**Why it matters here.** It establishes the core economics: most answers from the cheap model are acceptable, so paying for the expensive model on every request is dominated by paying for it only on failures.

**Caveat.** The scorer is trained per task distribution. A generic prompt-based judge is weaker than a fitted scorer, which is why this repository uses explicit, task-specific checklists and measures the escalation rate.

## 3. RouteLLM (LMSYS)

**Mechanism.** A classifier trained on preference data predicts whether a weak model is sufficient; difficult queries go to the strong model.

**Reported result.** More than 85% cheaper than routing everything to GPT-4 while retaining roughly 95% of its quality.

**Why it matters here.** It shows that a pre-generation decision is viable when the classifier is good, and that the quality cost is not zero — it is a few percent, concentrated at the difficulty boundary.

**Caveat.** Classifiers are reported to be right about 75–85% of the time on the easy/hard boundary. In a coding agent, a false "easy" on an ambiguous requirement is expensive. This repository therefore routes by an explicit, auditable policy (T0/T1/T2) rather than a black-box classifier, and keeps T2 out of the free tier entirely.

## 4. AutoMix and generator–critic cascades

**Mechanism.** The cheap model answers, a verifier (often a different model) checks the answer against the question, and failures escalate.

**Reported result.** 97–99% of the strong model's accuracy at a fraction of the cost, across several 2025–2026 cascade variants (including cluster-then-route designs such as arXiv 2606.27457).

**Why it matters here.** Two design choices come directly from this line of work:

1. **The critic must be independent.** Self-review by the same model is correlated with its own mistakes. The server defaults to a different model family for the judge.
2. **The verifier needs a concrete rubric.** "Is this good?" invites leniency. The judge prompt scores correctness, completeness, and usability, and must return structured JSON.

**Caveat.** Verification is a second inference. When the judge rejects too often, the cascade costs more than the paid model alone. Escalation rate is the metric to watch; 10–20% is healthy.

## 5. Gateways and production routing (OpenRouter, LiteLLM)

Production gateways add what research prototypes omit: price and latency awareness, health checks, retries, and fallback across providers.

**Reported result.** Classifier-based routing in production deployments is commonly cited at 85%+ cost reduction, with the gateway handling provider failures transparently.

**Why it matters here.** Free tiers are unreliable by definition: models rotate out, quotas change, endpoints return 410 or 429. The server implements the gateway lessons in miniature — an ordered fallback chain, a per-model health cooldown, and a larger retry budget for reasoning models that return empty content.

## 6. The Codex/NIM protocol mismatch

Codex CLI communicates with model providers using the **Responses API**. Hosted NVIDIA NIM exposes **`/chat/completions`**. `wire_api = "chat"` was removed from Codex, so pointing Codex directly at a hosted NIM endpoint does not work.

Three ways around it:

1. **Delegate through MCP** (this repository's default). Codex stays on its paid model and calls a tool that speaks Chat Completions. No proxy process, no protocol translation.
2. **Run a Responses↔Chat proxy** (community projects such as `codex-nvidia-proxy`). This lets Codex use NIM as a full model backend, at the cost of another service to operate.
3. **Use the free model as the orchestrator** and call the paid model only for hard sub-questions (`codex exec`). Maximum savings, more moving parts.

## 7. What the evidence does *not* support

| Claim | Reality |
|---|---|
| "Free models are as good as paid ones" | False in general. They are close on bounded, well-specified tasks and clearly worse on ambiguous, multi-file, or architectural work. |
| "Cascades always save 50%+" | Savings equal the share of work that passes the judge. A repository full of hard tasks saves little. |
| "A judge can replace tests" | No. The judge reviews text and diffs; it does not execute code. Tests and the paid T2 review remain mandatory for shipping work. |
| "More free models means better answers" | Only with a rubric and a disagreement signal. Unstructured multi-model voting adds latency and cost without a guarantee. |

## 8. Design conclusions

1. **Route by policy, not vibes.** An explicit tier table beats an opaque classifier for auditability.
2. **Judge with a different model and a concrete rubric.** Structured JSON, threshold 0.8 by default.
3. **Escalate with evidence, not transcript.** Task + answer + issues only.
4. **Never move T2 to free.** Ambiguity, security, and production debugging stay paid.
5. **Instrument everything.** Without a ledger, savings claims are fiction.
6. **Assume the free tier is flaky.** Fallback chain, cooldown, and retry-with-bigger-budget are requirements, not polish.

## References

- Chen, Zaharia, Zou. *FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance.* arXiv:2305.05176.
- Ong et al. *RouteLLM: Learning to Route LLMs with Preference Data.* LMSYS, 2024.
- *Dynamic Model Routing and Cascading: A Survey.* arXiv:2603.04445.
- *Cluster, Route, Escalate.* arXiv:2606.27457.
- NVIDIA NIM documentation — OpenAI-compatible Chat Completions API.
- OpenRouter and LiteLLM documentation — gateway routing, fallbacks, and health checks.
- Community Responses↔Chat bridges for Codex CLI (`codex-nvidia-proxy`, `codex-nim-poc`).
