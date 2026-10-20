# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `bench/`: a dependency-free free-model benchmark with 10 deterministic T0 text tasks, 6 real T1 code-repair mutations in `server.py`, and 4 T2 policy screens.
- `bench/run.py --validate-only`: proves clean tests pass, every mutation fails the shipped suite, the visible regression test fails, and the reference patch repairs it.
- T1 grading via a hidden 39-test suite; the worker sees only the mutated function and a small visible test, so test edits cannot change the grade.
- Judge calibration metrics (precision, recall, false-accept rate), Wilson confidence intervals, paired T0 outcomes, and generated Markdown/JSON reports.
- `bench/PREREGISTRATION.md` with frozen hypotheses, sampling plan, analysis rules, and deviation log.
- Harness unit tests (12) in `bench/tests/` and a CI validation job that needs no API key.
- `bench/run.py --resume`: interrupted free-tier runs can be continued without overwriting completed rows.
- `bench/b_run.py`: Bench B runner for the paid vs routed trade-off on the subscription-backed `codex` CLI (`--validate-only` proves each task is broken and solvable without any model call).
- `bench/BENCH_B.md`: Bench B pre-registration with the operator's real paid tiering as the baseline.

### Results

- First full run completed in `bench/results/2026-10-20-pilot/` after a
  superseded budget-limited attempt was archived in
  `bench/results/2026-10-20-pilot-budget-limited/`.
- Headline: 54/66 calls succeeded; T1 12/12, T0 40/46, T2 2/8; judge
  precision 94.4% and false-accept 5.0% on the 20 core T0 cascade decisions.

### Changed

- `free_cascade` now returns the merged generator+judge `usage` object, so callers can report tokens without reading the ledger.
- README roadmap and maturity table link the free pilot and keep the paid savings claim explicitly blocked.

## [0.3.1] - 2026-10-20

### Added

- `stats` reports the number of legacy pre-0.3.0 ledger rows whose usage may be double-counted, instead of silently mixing them with schema-versioned rows.

## [0.3.0] - 2026-10-20

Hardening release. No new routing features; the goal was to remove defects that made unattended use unreliable.

### Added

- `server.py doctor`: checks Python, key, NIM endpoint, model catalog, ledger writability, `opencode` and judge independence.
- Concurrent MCP server: tool calls run on a bounded thread pool (`FRUGAL_MAX_CONCURRENCY`, default 4), so `ping`, progress and cancellation work during long calls.
- `notifications/cancelled` support with JSON-RPC error `-32800`; `free_agent` terminates the worker's process group.
- Progress notifications when the client supplies `_meta.progressToken`.
- Protocol negotiation for `2024-11-05`, `2025-03-26`, `2025-06-18` and `2025-11-25`.
- Tool annotations: read-only hints for `free_ask`/`free_cascade`, destructive hint for `free_agent`.
- Exponential backoff with jitter for 429/5xx, capped at 8 seconds, before falling through the chain.
- Ledger `schema_version`, POSIX `flock`, 10 MB rotation to `.1`, and reads that include the rotated file.
- `tests/mcp_sdk_smoke.py` plus a CI job that installs the official MCP Python SDK and performs `initialize` + `tools/list`.
- `docs/07-harness-compatibility.md`: protocol revisions, tested/untested client matrix, configs for six clients, cancellation/timeout behaviour.
- `SECURITY.md`: threat model, deployment guidance and explicit non-goals.
- README "Status & maturity" section and v1.0 gates.
- 14 new unit tests (39 total).

### Changed

- `free_agent` writes the task to the worker's stdin instead of argv, so it no longer appears in the process list.
- `free_agent` rejects relative or missing `cwd` paths before spawning the worker.
- `free_cascade` writes one ledger record with merged usage; internal generator and judge calls no longer emit separate `free_ask` rows.
- The judge call excludes the generator model; results carry `judge_independent` and `warnings`.
- `parse_verdict()` accepts and applies the per-call threshold.
- README headline, savings table and FAQ now separate published research from unbenchmarked hypotheses.
- Architecture and playbook document concurrency, cancellation, backoff, judge independence and ledger rotation.
- Key-file examples use `/absolute/path/to/nvidia-key` placeholders.

### Fixed

- Judge could silently be the same model as the generator after a fallback.
- A per-call threshold was ignored when the judge omitted an explicit verdict.
- A long `free_agent` call blocked `ping` and cancellation on the stdio server.
- Concurrent processes could interleave ledger writes, and the ledger grew without bound.
- `stats` double-counted cascade usage because generator and judge calls were also logged as `free_ask`.
- 429/5xx responses moved to the next model without a retry.

## [0.2.0] - 2026-10-20

### Added

- `docs/05-token-glossary.md`: billing, caching, architecture, agent and benchmark terms, provider cheat sheet, commonly confused pairs, and institutional sources.
- `docs/06-prompt-caching.md`: KV-cache mechanics, provider-by-provider pricing, hit-rate and write:read math, invalidation triggers, instrumentation, and anti-patterns.
- `normalize_usage()`: maps OpenAI/NIM, DeepSeek and Anthropic usage fields (cached tokens, cache writes, reasoning tokens) onto one shape.
- `merge_usage()`: sums generator and judge calls so the ledger reflects the real cost of a cascade.
- `server.py stats [--days N] [--json]`: escalation rate, cache hit rate, model mix, average and p95 latency from the ledger.
- Unit tests for usage normalization, merging, and statistics (25 tests total).

### Changed

- Ledger entries now store normalized usage, including `cached_tokens`, `cache_write_tokens`, `reasoning_tokens` and `cache_hit_rate`.
- `free_cascade` logs the judge's usage and the `escalate_to_paid` verdict.
- README documents the caching layer, the two metrics that decide it, and the `stats` command.

## [0.1.0] - 2026-10-20

### Added

- Research review: FrugalGPT, RouteLLM, AutoMix/cascades, cluster-route-escalate, gateway routing, and the Codex/NIM protocol mismatch.
- Architecture document with the T0/T1/T2 tier policy, cascade design, and four implementation paths.
- Playbook of 12 failure modes with mitigations, a calibration loop, and anti-patterns.
- Roadmap with phases, acceptance criteria, success metrics, and rollback steps.
- Zero-dependency MCP stdio server (`mcp/free-models/server.py`):
  - `free_ask` — text completion over the free NIM chain.
  - `free_agent` — tool-using delegation through `opencode run`.
  - `free_cascade` — free generator plus an independent free judge, with `escalate_to_paid`.
  - Ordered fallback chain and per-model health cooldown.
  - Retry with a larger budget for reasoning models that return empty content.
  - Credential redaction before any text leaves the host.
  - JSONL ledger for cost and escalation measurement.
- Codex CLI and OpenCode configuration examples.
- Drop-in routing policy for `AGENTS.md`.
- Unit tests (no key required) and a live smoke test.
- GitHub Actions CI across Python 3.10, 3.12, and 3.13.

[Unreleased]: https://github.com/a32fred/frugal-agents/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/a32fred/frugal-agents/releases/tag/v0.3.1
[0.3.0]: https://github.com/a32fred/frugal-agents/releases/tag/v0.3.0
[0.2.0]: https://github.com/a32fred/frugal-agents/releases/tag/v0.2.0
[0.1.0]: https://github.com/a32fred/frugal-agents/releases/tag/v0.1.0
