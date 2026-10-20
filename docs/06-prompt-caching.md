# Prompt caching: the 90% discount most agent stacks leave on the table

Caching does not change model quality. It changes the invoice, sometimes by 80–90%. It is the highest-leverage cost control in an agent stack, and the easiest to break silently.

## 1. What is actually being cached

A transformer processes a prompt in two phases:

1. **Prefill** — every prompt token is projected into key and value matrices at every attention layer. This is compute-heavy and grows quadratically with prompt length.
2. **Decode** — tokens are generated one at a time, attending to the stored keys and values.

The **KV cache** is that stored state. When a new request starts with the *same prefix*, the provider can reuse the prefill instead of recomputing it. You still pay for the tokens, but at the cache-read rate.

```
request 1:  [ system prompt | tools | history | user turn ]  → full input price
request 2:  [ system prompt | tools | history | new turn  ]  → prefix cached, new tokens full price
request 3:  [ modified system prompt | ...                ]  → cache miss from the first change
```

Three consequences:

- **Only input is discounted.** Output is never cached.
- **Matching is exact and prefix-only.** One changed byte early invalidates everything after it.
- **The cache is time-limited.** A session resumed after the TTL pays full price once to warm it again.

## 2. Provider mechanics (October 2026)

| Provider | Activation | Minimum prefix | Read discount | Write cost | TTL |
|---|---|---|---|---|---|
| **OpenAI** | automatic | ~1,024 tokens | 50% legacy, **90% GPT-5.4+** | none | ~5–10 min, up to 1 h off-peak |
| **Anthropic** | explicit `cache_control` (up to 4 breakpoints) | 512–4,096 tokens | **90%** (0.1×) | 1.25× (5 min) / 2× (1 h) | 5 min, refreshed on read; 1 h optional |
| **Google Gemini** | implicit or explicit | ~32K for explicit | ~75% implicit | standard input + storage per hour | hours (explicit) |
| **DeepSeek** | automatic, disk-based, best effort | not published | up to ~90% | none | provider-managed |
| **Groq** | automatic | provider-managed | 50% | none | provider-managed |
| **NVIDIA NIM (free)** | not billed | — | n/a | n/a | latency benefit only |

Details that bite:

- **OpenAI** cached tokens still count toward your tokens-per-minute limit, even though they are cheaper.
- **Anthropic** charges the write premium on the first call; caching pays off after **one** read at 5 min, or **two** reads at 1 h. Changing extended-thinking settings can invalidate part of the prefix.
- **Google** explicit caches bill **storage per hour** whether or not you read them; a forgotten cache is a subscription.
- **DeepSeek** reports `prompt_cache_hit_tokens` and `prompt_cache_miss_tokens`; hits are extremely cheap, and off-peak prices halve the bill.
- **Batch + cache stack.** Anthropic's 0.1× read multiplier combined with the 50% batch discount can push input below 5% of list price. Output only gets the batch discount.

## 3. The only two metrics that matter

```text
hit rate       = cached_input_tokens / total_input_tokens
write:read     = cache_write_tokens / cache_read_tokens
```

Rules of thumb from production agent workloads:

- **Hit rate below 70%** on a loop with a stable system prompt means your prefix is volatile, not that caching is broken.
- **Write:read above ~10%** means you keep rewriting entries you should be reusing.
- **Savings ≠ discount.** A 90% read discount on a prompt where only 30% is cacheable is a ~27% saving.

Worked example (from the 2026 pricing literature): 100 requests/day, 50K-token prefix, $3/M input, 90% hit rate on the 5-minute tier:

```text
without cache: 100 × 50K × $3/M                    = $15.00/day
with cache:    10 writes × 50K × $3.75/M           = $1.875
               90 reads  × 50K × $0.30/M           = $1.35
                                                   = $3.225/day  (−78.5%)
```

Agent loops see less than the best case because the conversation tail is always new: independent 2026 benchmarks report **41–80%** savings across providers, with modelled 30-turn loops at ~80% (Claude), ~85% (GPT-6.1), ~89% (DeepSeek V4).

## 4. What invalidates a cache

| Trigger | Why |
|---|---|
| Reordering system prompt / tools | Prefix match breaks at the first difference |
| A timestamp, UUID, or date in the prefix | Changes every request; guarantees a miss |
| Editing `AGENTS.md` or tool schemas | New prefix from that byte onward |
| Switching model or provider | Caches are per model |
| TTL expiry | Resumed sessions pay to re-warm |
| Compaction / history rewrite | The summarized prefix is new |
| Changing reasoning settings (Anthropic) | Thinking blocks alter the prefix |

The practical rule: **static first, dynamic last, never reorder.**

## 5. How to structure an agent prompt

```text
[ system prompt        ]  ← stable, cacheable
[ tool definitions     ]  ← stable, cacheable
[ project instructions ]  ← stable within a session (AGENTS.md)
[ retrieved documents  ]  ← stable per task, cacheable
[ conversation history ]  ← append-only, mostly cacheable
[ current user turn    ]  ← always new
```

Checklist:

- Put every invariant block before the first variable byte.
- Keep `AGENTS.md` short and stable; do not inject dates or per-run IDs.
- Prefer append-only history over rewriting earlier messages.
- Compact deliberately, not constantly: each compaction pays a fresh prefill.
- Keep sessions contiguous when working on one task; a pause longer than the TTL re-bills the prefix once.
- Do not chase caching on the free tier. It is already $0; optimize latency there, cost on the paid tier.

## 6. Instrumentation

Providers expose different fields. Normalize them once:

| Provider | Field |
|---|---|
| OpenAI / NIM | `usage.prompt_tokens_details.cached_tokens` |
| DeepSeek | `usage.prompt_cache_hit_tokens`, `usage.prompt_cache_miss_tokens` |
| Anthropic | `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens` (add both to `input_tokens` for the real prompt size) |
| Google | `usage_metadata` cache fields |

This repository does exactly that in `normalize_usage()` (`mcp/free-models/server.py`):

```python
prompt  = usage["prompt_tokens"] or usage["input_tokens"]
cached  = usage["prompt_tokens_details"]["cached_tokens"]      # OpenAI / NIM
cached  = usage["prompt_cache_hit_tokens"] or cached           # DeepSeek
cached  = usage["cache_read_input_tokens"] or cached           # Anthropic
hit_rate = cached / (prompt + cache_read + cache_creation)     # Anthropic-style sum
```

And aggregate it from the ledger:

```bash
python3 server.py stats            # escalation + cache hit rate + p95 latency
python3 server.py stats --days 7
python3 server.py stats --json
```

Output:

```text
calls: 42 (last 7d)
cascade: 31/38 accepted · escalation 18.4%
cache: 812,400/1,204,000 prompt tokens (67.5% hit rate) · write 12,000
latency: avg 4,210 ms · p95 11,800 ms
```

If the hit rate is low, inspect the prefix before touching the judge or the model.

## 7. Caching vs the rest of the cost stack

| Lever | Typical effect | Risk |
|---|---|---|
| Prompt caching | 50–90% off input | Prefix fragility; silent misses |
| Batch API | 50% off everything | Up to 24 h latency |
| Model routing / cascade | 40–70% of total spend | Judge quality, escalation latency |
| Context hygiene | 20–60% of input | Loses useful history |
| Output discipline | 10–30% of output | Truncated answers |
| Semantic cache | 100% off repeated queries | Serving a cached mistake |

Caching and routing are complementary: route the task to a cheap model, then cache the stable prefix on whichever model runs it. On the paid tier, caching is the first thing to fix because it requires no quality trade-off at all.

## 8. Applying it to this stack

- **Paid Codex sessions** are where cache hits turn into money. The routing policy is appended at the end of `AGENTS.md`, which keeps the earlier prefix stable. Editing that policy mid-week invalidates the cache once — acceptable, but batch edits.
- **Free NIM calls** report no cache fields today (the ledger shows a 0% hit rate). That is expected and costs nothing; do not "fix" it.
- **The judge prompt** is a module-level constant used as the system message, so every judge call shares a stable prefix. On a paid endpoint behind `NIM_BASE_URL`, that prefix caches automatically.
- **One-shot CLI calls** never benefit from caching (no repeated prefix). Only loops and sessions do.

## 9. Anti-patterns

- **Timestamps in the system prompt.** The classic silent 0% hit rate.
- **Rebuilding the tool list per request in a different order.** Same effect.
- **Caching the variable tail.** You pay write premiums for content that never repeats.
- **Assuming a 90% discount is a 90% saving.** Multiply by the cacheable share.
- **Ignoring TTL.** A long lunch can cost more than the work.
- **Semantic-caching without validation.** Wrong answers become permanent and cheap.
- **Chasing cache metrics on a free tier.** There is no invoice to reduce.

## Sources

- OpenAI, Anthropic, Google, DeepSeek, Groq prompt-caching and pricing documentation (October 2026).
- Flexera, *Prompt Caching breakdown: Cut token spend in 2026*.
- BenchLM, *Prompt Caching: What It Costs and When It Pays Off* (October 2026).
- Artificial Analysis, *Prompt Caching: Cost & Performance Analysis Across Providers*.
- Beri.net, *Prompt Caching Savings: One Timestamp Turns 80% Into 6%* (October 2026).
- CloudZero, *LLM API pricing comparison in 2026*.
- Stanford HAI, *AI Index Report 2026*; Epoch AI, *The plunging price of thought*.
