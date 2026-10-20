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

- **Independent judge.** Different model family from the generator, temperature 0, structured JSON verdict. The server excludes the generator from the judge call and flags `judge_independent: false` when no alternative model exists.
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
MCP client ──stdio JSON-RPC──► McpServer (bounded thread pool)
                                 │  ping · progress · cancellation
                                 ├─ redact()          strip credential shapes
                                 ├─ chain_for()       requested model + fallbacks
                                 ├─ complete()        retries, backoff, cooldown
                                 ├─ free_cascade()    generator ≠ judge guard
                                 ├─ free_agent()      stdin task, process group
                                 └─ record()          schema, lock, rotation
```

### Concurrency and cancellation

The server reads stdin on the main thread and runs tool calls on a pool of four workers (`FRUGAL_MAX_CONCURRENCY`). This keeps `ping` responsive and lets a second request make progress while a long `free_agent` runs.

- `notifications/cancelled` sets a per-request event.
- HTTP calls check the event before the request, after the response, and between retries. A socket read already in progress is not aborted.
- `free_agent` polls every 100 ms and terminates the worker's process group, so child processes do not survive a cancel.
- A cancelled request receives JSON-RPC error `-32800`. The ledger may still contain the usage that actually happened.
- If the client supplies `_meta.progressToken`, the server emits `notifications/progress` at start and finish. It does not stream tokens.

### Fallback chain and backoff

Default order: `openai/gpt-oss-20b` → `google/gemma-4-31b-it` → `nvidia/nemotron-3.5-lightning-30b-a3b` → `z-ai/glm-5.3` → `deepseek-ai/deepseek-v4.1-flash`.

On 429/5xx, the server retries the same model up to `FRUGAL_RETRIES` times (default 2) with exponential backoff plus jitter, capped at 8 seconds, before moving to the next model. On 404/410 (retired model), it moves on immediately. A failing model is marked unhealthy for 15 minutes (retired) or 3 minutes (transient). This prevents one dead catalog entry from adding a timeout to every call.

### Judge independence

`free_cascade` passes `exclude_models=[generated_model]` to the judge call. If the chain has another healthy model, the judge cannot be the generator. If it has no alternative, the review still runs but the result sets `judge_independent: false`, adds a warning, and the ledger records the same flag. The server never claims an independent review it did not get.

The per-call `threshold` is used both to interpret a missing verdict and to make the final accept/reject decision. A malformed verdict falls back to the explicit threshold, not the global default.

### Reasoning-model quirk

Some models spend the entire `max_tokens` budget on reasoning and return `content: null`. The server retries once with at least 4096 tokens before moving on. If content is still empty, the model is treated as failed.

### Ledger

One JSON object per line, with usage normalized across providers and a schema version:

```json
{"schema_version": 1, "ts": 1792496191.2, "tool": "free_cascade",
 "model": "openai/gpt-oss-20b", "judge": "google/gemma-4-31b-it",
 "judge_independent": true,
 "usage": {"prompt_tokens": 71, "completion_tokens": 47, "cached_tokens": 0,
 "cache_write_tokens": 0, "reasoning_tokens": 0, "cache_hit_rate": 0.0},
 "score": 1.0, "accepted": true, "escalate_to_paid": false, "latency_ms": 10528}
```

Cascades write one record with merged generator + judge usage; the internal calls do not write separate `free_ask` rows, which keeps `stats` from double-counting. Appends take a POSIX `flock` and rotate to `ledger.jsonl.1` at 10 MB (`FRUGAL_LEDGER_MAX_BYTES`). `stats` reads the rotated file and the current file, and prints a `legacy rows` warning for pre-0.3.0 entries whose usage may be double-counted.

`normalize_usage()` accepts OpenAI/NIM (`prompt_tokens_details.cached_tokens`), DeepSeek (`prompt_cache_hit_tokens`) and Anthropic (`cache_read_input_tokens`, `cache_creation_input_tokens`) shapes. Cascades sum generator and judge usage.

Use `server.py doctor` to check the key, endpoint, catalog, ledger and worker before debugging a model failure. Use `server.py stats` to compute escalation rate, cache hit rate, model mix and p95 latency. See [`05-token-glossary.md`](05-token-glossary.md) for the terms and [`06-prompt-caching.md`](06-prompt-caching.md) for the caching mechanics.

## 6. Security model

| Risk | Control |
|---|---|
| Credentials pasted into a prompt | `redact()` strips common token shapes before egress |
| Task text exposed in the process list | `free_agent` writes the task to the worker's stdin, not argv |
| Proprietary code sent to a free cloud tier | Explicit policy: free tiers never receive `.env`, customer data, or unreleased security work; T2 handles it |
| Prompt injection through repository content | Free workers run with the same sandbox/permissions as any agent; review diffs, not prose. There is no filesystem or network isolation. |
| Silent data retention by the provider | Use a self-hosted endpoint (`NIM_BASE_URL`) when retention matters |
| Runaway worker | `free_agent` timeout, cancellation kills the process group, and the ledger makes time visible |
| Ledger privacy | Records contain metadata and `cwd`, never prompts or answers; protect the file like shell history |
| Concurrent ledger writers | POSIX `flock` plus a schema version; Windows has no lock |

Redaction is a backstop. The policy is the control. See [`SECURITY.md`](../SECURITY.md) for the full threat model.

## 7. Failure modes and responses

| Symptom | Likely cause | Action |
|---|---|---|
| `all free models failed` | catalog rotation or key invalid | run `server.py doctor`, refresh the model list, verify `NIM_API_KEY` |
| Empty answers | reasoning consumed the budget | handled by retry; raise `max_tokens` for very small budgets |
| Judge rejects everything | rubric too strict or wrong model | inspect ledger scores; lower threshold or change judge |
| `judge_independent: false` | the chain has no alternative to the generator | add a second free model to `FRUGAL_CHAIN`; do not treat that review as independent |
| Escalation rate > 30% | tasks are harder than T1, or judge is strict | re-tier the work; tighten the task prompt; keep the work paid |
| `free_agent` edits the wrong repo | missing `cwd` | the server rejects relative or missing paths; pass an absolute `cwd` |
| Latency spikes | a slow fallback is being retried | backoff and cooldown cap repeat failures; check ledger latency |
| Stats look inflated | pre-0.3.0 rows logged cascade parts separately | filter to `schema_version` 1 or delete old rows |

## 8. Extension points

- **New provider:** set `NIM_BASE_URL` to any OpenAI-compatible endpoint.
- **New judge rubric:** edit `JUDGE_SYSTEM` or pass a task-specific checklist.
- **New tier:** add a prefix or a tool; the policy lives in `AGENTS.md`.
- **New protocol revision:** add it to `SUPPORTED_PROTOCOLS` after implementing its lifecycle. The stateless `2026-07-28` revision needs a transport rewrite, not a version string.
- **Parallelism:** run `free_agent` workers in separate worktrees (path C); keep the MCP concurrency at 2–4 to respect free-tier rate limits.
