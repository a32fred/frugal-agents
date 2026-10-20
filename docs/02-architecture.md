# Architecture

## 1. Division of labor

> The paid model decides and reviews. Free models produce volume.

| Tier | Work | Executor | Gate |
|---|---|---|---|
| T0 | commit messages, PR descriptions, summaries, explanations, regex, docstrings, JSON/YAML, boilerplate, renames, translations | fast free model | none for drafts; T2 review before merge |
| T1 | isolated implementation, tests, spec-driven refactor, step-by-step migration, bug reproduction | strong free model | free judge + diff review by T2 before merge |
| T2 | architecture, ambiguous requirements, production debugging, security, design decisions, final review | paid model | human sign-off |

The boundary is **ambiguity**, not file count. A 400-line mechanical rename is T1. A 10-line change to authentication is T2.

## 2. The cascade

```
task
 │
 ├─ T2 ──────────────────────────────► paid model ──► answer
 │
 └─ T0/T1 ──► free generator ──► free judge (different model, rubric)
                                   │
                                   ├─ accept (score ≥ threshold) ──► answer
                                   └─ reject ──► paid model with
                                                 {task, answer, issues}
                                                 ──► answer
```

Properties:

- **Independent judge.** Different model family from the generator, temperature 0, structured JSON verdict.
- **Evidence-carrying escalation.** The paid model sees a compact package, not the conversation. This caps handoff cost at roughly 10% of the saved tokens.
- **No silent downgrade.** T2 work never enters the free path, regardless of judge score.

## 3. Why a judge at all?

The alternative is to trust the free model directly. The judge buys three things:

1. **Defect detection.** Different models fail differently; a second family catches more.
2. **A measurement signal.** Every verdict is logged, so escalation rate and judge accuracy can be tracked.
3. **A cheap escalation trigger.** One 300-token judge call is far cheaper than a paid generation.

The judge is not a test suite. It reviews text and diffs for correctness, completeness, and usability. Executable verification remains the job of tests.

## 4. Implementation paths

### A. MCP server (recommended, implemented here)

Codex (or any MCP client) keeps its paid model and calls:

- `free_ask` — one-shot text completion with fallback.
- `free_agent` — delegates to `opencode run` with file tools in a chosen directory.
- `free_cascade` — generator + judge with an `escalate_to_paid` flag.

**Pros:** no extra service, no protocol translation, works today, reversible (delete one config block).
**Cons:** the paid model still pays to decide and to phrase the delegation; savings depend on it actually using the tools.

### B. Codex profile backed by a Responses↔Chat proxy

Run LiteLLM (or a small bridge) in Docker, expose an OpenAI-compatible Responses endpoint, and add a `free` profile to Codex.

**Pros:** whole sessions run free; no delegation overhead.
**Cons:** one more service; the free model must drive the agent loop, which is exactly where weak models struggle.

### C. Orca orchestration (paid coordinator + free workers)

The paid model plans and reviews; workers run `opencode --model nim/...` in separate worktrees.

**Pros:** parallel, high throughput on batches of similar fixes; review is diff-only.
**Cons:** requires an orchestrator; merge conflicts and duplicated context are real costs.

### D. Inverted orchestrator

A free model coordinates and calls `codex exec` only for hard sub-questions.

**Pros:** maximum savings.
**Cons:** a weak coordinator misjudges difficulty; debugging the orchestrator costs more than it saves for small tasks.

**Recommended combination:** A for daily work, C for batches. B and D are optional experiments.

## 5. Server internals

```
MCP client ──stdio JSON-RPC──► server.py
                                 │
                                 ├─ redact()          strip credential shapes
                                 ├─ chain_for()       requested model + fallbacks
                                 ├─ unhealthy()       skip recently failed models
                                 ├─ chat()            POST /chat/completions
                                 ├─ parse_verdict()   tolerant JSON extraction
                                 └─ record()          append JSONL ledger
```

### Fallback chain

Default order: `openai/gpt-oss-20b` → `google/gemma-4-31b-it` → `nvidia/nemotron-3.5-lightning-30b-a3b` → `z-ai/glm-5.3` → `deepseek-ai/deepseek-v4.1-flash`.

On HTTP 404/410 (retired model), 408/409/425/429, or 5xx, the next model is tried. A failing model is marked unhealthy for 15 minutes (retired) or 3 minutes (transient). This prevents one dead catalog entry from adding a timeout to every call.

### Reasoning-model quirk

Some models spend the entire `max_tokens` budget on reasoning and return `content: null`. The server retries once with at least 4096 tokens before moving on. If content is still empty, the model is treated as failed.

### Ledger

One JSON object per line, with usage normalized across providers:

```json
{"ts": 1792496191.2, "tool": "free_cascade", "model": "openai/gpt-oss-20b",
 "judge": "google/gemma-4-31b-it", "usage": {"prompt_tokens": 71, "completion_tokens": 47,
 "cached_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0, "cache_hit_rate": 0.0},
 "score": 1.0, "accepted": true, "escalate_to_paid": false, "latency_ms": 10528}
```

`normalize_usage()` accepts OpenAI/NIM (`prompt_tokens_details.cached_tokens`), DeepSeek (`prompt_cache_hit_tokens`) and Anthropic (`cache_read_input_tokens`, `cache_creation_input_tokens`) shapes. Cascades sum generator and judge usage.

Use `server.py stats` to compute escalation rate, cache hit rate, model mix and p95 latency. See [`05-token-glossary.md`](05-token-glossary.md) for the terms and [`06-prompt-caching.md`](06-prompt-caching.md) for the caching mechanics.

## 6. Security model

| Risk | Control |
|---|---|
| Credentials pasted into a prompt | `redact()` strips common token shapes before egress |
| Proprietary code sent to a free cloud tier | Explicit policy: free tiers never receive `.env`, customer data, or unreleased security work; T2 handles it |
| Prompt injection through repository content | Free workers run with the same sandbox/permissions as any agent; review diffs, not prose |
| Silent data retention by the provider | Use a self-hosted endpoint (`NIM_BASE_URL`) when retention matters |
| Runaway worker | `free_agent` timeout, per-call timeout, and the ledger make cost/time visible |

Redaction is a backstop. The policy is the control.

## 7. Failure modes and responses

| Symptom | Likely cause | Action |
|---|---|---|
| `all free models failed` | catalog rotation or key invalid | check `server.py models`, refresh the model list, verify `NIM_API_KEY` |
| Empty answers | reasoning consumed the budget | handled by retry; raise `max_tokens` for very small budgets |
| Judge rejects everything | rubric too strict or wrong model | inspect ledger scores; lower threshold or change judge |
| Escalation rate > 30% | tasks are harder than T1, or judge is strict | re-tier the work; tighten the task prompt; keep the work paid |
| `free_agent` edits the wrong repo | missing `cwd` | always pass an absolute `cwd` |
| Latency spikes | a slow fallback is being retried | cooldown handles repeat failures; check ledger latency |

## 8. Extension points

- **New provider:** set `NIM_BASE_URL` to any OpenAI-compatible endpoint.
- **New judge rubric:** edit `JUDGE_SYSTEM` or pass a task-specific checklist.
- **New tier:** add a prefix or a tool; the policy lives in `AGENTS.md`.
- **Parallelism:** run `free_agent` workers in separate worktrees (path C).
