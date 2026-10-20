# Token glossary

Every billing term you meet in an agent stack, in one place. Prices move; the mechanics do not. Where a number appears it is dated and sourced at the bottom.

> **On the phrase "gold cache":** it is not an official term. In Codex and OpenCode TUIs the cached-token count is often rendered in gold, and people started calling it that. The providers call it **cached input**, **cache read**, or **cache hit** tokens. See [`06-prompt-caching.md`](06-prompt-caching.md).

## 1. Tokens and billing

| Term | Meaning | Why it matters |
|---|---|---|
| **Token** | The unit a model reads and bills. Roughly ¾ of an English word; code and non-Latin scripts tokenize worse. | Every price is per million tokens. |
| **Tokenizer** | The algorithm mapping text to token IDs (BPE and variants). Each model family has its own. | The same text can cost 20–35% more on one model than another. |
| **Input tokens** | Everything sent: system prompt, tool schemas, history, current turn. | Usually the dominant cost of an agent session. |
| **Output tokens** | Everything generated. | Typically 5–10× the input price; reasoning makes it worse. |
| **Reasoning / thinking tokens** | Hidden chain-of-thought tokens. Billed as **output**. | Can double or triple a bill while being invisible in the answer. |
| **Cached input / cache read** | Input tokens served from the provider's prefix cache. | 50–90% cheaper; the main lever for long sessions. |
| **Cache write** | Anthropic's fee for storing a prefix in the cache. | 1.25× (5 min) or 2× (1 h) the normal input rate. |
| **Batch tokens** | Asynchronous requests, usually returned within 24 h. | Flat 50% discount at OpenAI, Anthropic and Google. |
| **Priority / flex tier** | Paid latency tiers (faster or cheaper than standard). | Premium for latency, discount for patience. |
| **Long-context surcharge** | Higher rate above a context threshold. | OpenAI charges ~2× input above ~272K tokens; Anthropic does not. |
| **Off-peak pricing** | Time-of-day discount. | DeepSeek halves prices outside peak hours. |
| **Max output tokens** | Hard cap on the generated answer. | Hitting it truncates the answer, not the bill. |
| **Multimodal tokens** | Images, audio, video converted to token equivalents. | Gemini video ≈ 263 tokens/s; Claude images up to ~4.8K tokens. |
| **Billable minimum** | Some endpoints round small requests up. | Irrelevant at agent scale, relevant for tests. |

## 2. Caching

| Term | Meaning |
|---|---|
| **KV cache** | The key/value tensors a transformer stores for every token it has processed. An *infrastructure* object inside the provider, not a line item. |
| **Prefill** | The phase that processes the prompt and fills the KV cache. Compute-heavy and quadratic in prompt length. |
| **Decode** | The phase that generates tokens one at a time, reusing the KV cache. |
| **Prefix caching** | Reusing the KV state of an **identical prefix** across requests. This is what "prompt caching" sells. |
| **Cache hit** | A request whose prefix matched a stored one. Billed at the read rate. |
| **Cache miss** | No prefix match; the prompt is billed at the full input rate. |
| **Hit rate** | `cached_tokens / prompt_tokens`. Below ~70% in an agent loop means a volatile prefix. |
| **Write:read ratio** | Cache-write tokens divided by cache-read tokens. Above ~10% means you rewrite what you should reuse. |
| **TTL** | How long a cache entry lives. OpenAI ~5–10 min (up to 1 h off-peak); Anthropic 5 min (1 h optional); Gemini explicit up to hours. |
| **Breakpoint** | Anthropic's explicit marker (`cache_control`) delimiting a cacheable block; up to 4 per request. |
| **Cache write premium** | Anthropic's surcharge for creating a cache entry (OpenAI and Google implicit do not charge one). |
| **Storage fee** | Google's explicit cache charge per million tokens per hour, independent of reads. |
| **Exact cache** | Reuse keyed by an identical prompt/response pair. Safe, boring, effective for templates. |
| **Semantic cache** | Reuse keyed by embedding similarity. Saves entire calls but can **serve a cached mistake**; gate it with validation. |
| **Cold start** | The first request pays full price and the write cost; only later reads save. |

## 3. Context and architecture

| Term | Meaning |
|---|---|
| **Context window** | Maximum tokens the model can attend to in one request (e.g. 1M). |
| **Effective context** | How much of the window the model actually uses well; usually smaller. |
| **Attention** | The mechanism letting each token weigh every other token; quadratic cost in sequence length. |
| **MHA / MQA / GQA** | Multi-head, multi-query and grouped-query attention. Fewer KV heads means a smaller KV cache and cheaper long context. |
| **Sparse / sliding-window attention** | Attending to a subset of tokens to cut long-context cost. |
| **MoE (mixture of experts)** | Only a fraction of parameters activate per token. High total parameters, lower inference cost. |
| **Parameters** | Learned weights. Not a billing unit, but they drive latency and price. |
| **Quantization** | Lower-precision weights (FP8, INT4) for cheaper serving. |
| **Distillation** | Training a small model on a large one's outputs. A common source of cheap tiers. |
| **Speculative decoding** | A draft model proposes tokens; the large model verifies. Faster, same output distribution. |
| **TTFT** | Time to first token — the latency users feel. |
| **TPOT** | Time per output token — throughput after the first token. |
| **Tokens per second (TPS)** | Generation speed. |
| **FLOPs** | Compute unit used to reason about training and inference cost. |

## 4. Agents

| Term | Meaning |
|---|---|
| **Tool / function calling** | A structured call the model emits for the harness to execute. |
| **MCP** | Model Context Protocol — standard transport for tools and resources (this repository's server). |
| **System prompt** | The static instruction block at the front of every request; the best caching candidate. |
| **`AGENTS.md`** | Repository/agent instruction file read into the system context. Stability here directly affects cache hits. |
| **Compaction** | Summarizing or truncating history when the context fills. Rewrites the prefix and therefore invalidates the cache. |
| **RAG** | Retrieval-augmented generation: fetch documents, inject them into the prompt. |
| **Embeddings** | Vectors representing text, used for retrieval and semantic caching. |
| **Reranker** | A model that reorders retrieved candidates by relevance. |
| **Orchestrator** | The agent that decomposes work and dispatches subagents. |
| **Subagent / worker** | A separate agent session, often in its own worktree, with its own context and model. |
| **Worktree** | An isolated checkout of a repository, letting parallel agents edit without collisions. |
| **Cascade** | Cheap model first, verify, escalate to an expensive model on failure. |
| **Router** | Chooses a model before generation, usually by difficulty. |
| **Generator–critic** | One model produces, a different one reviews against a rubric. |

## 5. Economics and benchmarks

| Term | Meaning |
|---|---|
| **Cost per token** | List price per million input/output tokens. |
| **Cost per task** | Total tokens × price for a completed unit of work. The number that matters. |
| **Price-performance** | Quality achieved per dollar; the axis on which cheap models compete. |
| **Escalation rate** | Share of cascaded tasks rejected by the judge and sent to the paid model. Target 10–20%. |
| **Free share** | Share of tasks completed without paid tokens. Target ≥50%. |
| **Cache hit rate** | See above. Target >70% on agent loops. |
| **Benchmark contamination** | Test data leaked into training, inflating scores. |
| **SWE-bench / SWE-bench Verified** | Repository-level coding benchmark. |
| **MMLU / GPQA / AIME** | Knowledge, science and math benchmarks. |
| **Arena Elo** | Human-preference ranking from pairwise comparisons. |
| **AI Index** | Stanford HAI's annual state-of-AI report. |
| **Epoch AI** | Research group tracking compute, prices and model trends. |

## 6. Commonly confused pairs

| Pair | Difference |
|---|---|
| KV cache vs prompt cache | KV cache is the provider's internal state; prompt caching is the billing product that reuses it. |
| Prompt cache vs semantic cache | Prefix cache requires an **identical prefix**; semantic cache matches **meaning** and can be wrong. |
| Cached input vs batch | Cache discounts repeated **input**; batch discounts **everything** by 50% for latency tolerance. They stack. |
| Reasoning tokens vs output tokens | Reasoning is billed as output but usually stripped from later turns. |
| Context window vs max output | Window is what goes in; max output is what comes out. |
| Cache write vs cache read | Write creates the entry (sometimes at a premium); read reuses it (discounted). |
| Cache hit rate vs savings | Hit rate is a ratio; savings depend on price multipliers and how much of the prompt is cacheable. |

## 7. Provider cheat sheet (October 2026)

| Provider | Caching | Read price | Write price | Notable |
|---|---|---|---|---|
| OpenAI | automatic, ≥~1,024-token prefix | 50% off legacy, **90% off GPT-5.4+** | none | cached tokens still count against TPM; ~5–10 min TTL |
| Anthropic | explicit, up to 4 breakpoints | **0.1×** (90% off) | 1.25× (5 min) / 2× (1 h) | break-even: 1 read (5 min) or 2 reads (1 h) |
| Google | implicit or explicit | ~75% off implicit (no guarantee) | standard input + hourly storage | explicit minimum ~32K tokens |
| DeepSeek | automatic disk cache, best effort | up to ~90% off; hits ≈ $0.0036/M on V4 Pro | none | peak/off-peak pricing halves rates |
| Groq | automatic | 50% off | none | no opt-out |
| NVIDIA NIM (free tier) | not a billing concern | $0 | $0 | cache fields often absent; latency still benefits |

## 8. Institutions worth following

- **Stanford HAI — AI Index** (annual, April): the field's shared scoreboard.
- **Epoch AI**: inference price trends, compute, model economics.
- **US: NIST AI** (standards/evaluation), **White House CEA** (economic analysis), **DARPA** (early research programs).
- **China: DeepSeek**, **Moonshot/Kimi**, **Alibaba Qwen**, **Zhipu/Z.ai (GLM)**, **MiniMax** — the labs driving the price floor and open-weight competition.
- **Neutral trackers**: Artificial Analysis (price/latency/caching comparisons), LMArena (preferences).

## Sources

- OpenAI, Anthropic, Google, DeepSeek and Groq API pricing and prompt-caching documentation (checked October 2026).
- Artificial Analysis, *Prompt Caching: Cost & Performance Analysis Across Providers*.
- Flexera, *Prompt Caching breakdown* (2026).
- BenchLM, *Prompt Caching: What It Costs and When It Pays Off* (Oct 2026).
- Stanford HAI, *AI Index Report 2026*.
- Epoch AI, *The plunging price of thought*.
- US Council of Economic Advisers, *Artificial Intelligence and the Great Divergence* (Jan 2026).

> Prices and model names change monthly. Treat every number here as a snapshot and verify against the provider's pricing page before budgeting.
