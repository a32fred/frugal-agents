# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/a32fred/frugal-agents/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/a32fred/frugal-agents/releases/tag/v0.1.0
