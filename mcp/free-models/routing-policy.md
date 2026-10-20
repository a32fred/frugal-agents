# Routing policy (drop-in `AGENTS.md` section)

Append this to your agent's `AGENTS.md`. It tells the paid model when to spend its own tokens and when to delegate to the free tier.

Keep the section stable across sessions so the cached prompt prefix stays reusable.

---

## Cost routing policy

The paid model is the **decision maker and reviewer**. Free models produce volume. Use the `free-models` MCP tools whenever the task fits a tier below.

### T0 — delegate to `free_ask`

Use for text-only, low-risk work:

- commit messages, PR descriptions, changelogs
- summaries and explanations of existing code
- regex, glob patterns, one-liners
- docstrings, comments, README prose
- JSON/YAML/config snippets
- renames and mechanical translations

### T1 — delegate to `free_agent` or `free_cascade`

Use for bounded work with a clear specification:

- implementing a change in one isolated file
- writing or extending tests from an existing pattern
- a refactor with an explicit before/after contract
- a migration with written steps
- reproducing a reported bug

Rules:

1. Pass an absolute `cwd`. Never let a worker guess the repository.
2. The task statement must contain the spec, not a reference to the conversation.
3. Review the diff yourself before accepting it. A passing judge is not a code review.

### T2 — keep on the paid model

Never delegate:

- architecture and design decisions
- ambiguous or contradictory requirements
- production debugging and incident response
- security, authentication, authorization, cryptography
- database migrations that touch production data
- **final review of anything that ships**
- anything containing secrets, customer data, or unreleased security work

### Escalation contract

When `free_cascade` returns `escalate_to_paid: true`, take the task, the free answer, and the `issues` list. Do not take the conversation, the logs, or the whole repository.

When you delegate, keep the prompt under 20 lines and ask for a compact, structured result.

### Prohibited

- Sending `.env` files, API keys, tokens, customer data, or private keys to a free tier.
- Accepting a free answer without reviewing the diff.
- Cascading T2 work: if it is ambiguous, escalate first.
- Pasting an entire transcript "for context".

### Measurement

Every free call is recorded in `~/.frugal-agents/ledger.jsonl`. Review escalation rate and free share weekly. Target: 10–20% escalation, ≥50% of tasks handled free.
