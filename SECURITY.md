# Security policy

## Supported versions

| Version | Supported |
|---|---|
| `0.3.x` | yes |
| `0.2.x` and older | no |

Security fixes land on `main` and in the latest release. There are no long-term support branches.

## Reporting a vulnerability

Use GitHub's **private vulnerability reporting** on this repository (Security → Advisories → Report a vulnerability). Do not open a public issue containing a key, customer data, or a working exploit.

Include:

- affected version (`server.py --version`),
- the client and protocol revision,
- reproduction steps or a minimal prompt,
- impact: credential disclosure, unintended file write, denial of service, or sandbox escape.

This is a volunteer project; there is no bug bounty. Reports are acknowledged on a best-effort basis.

## What this software is

A local stdio MCP server that:

1. sends prompts to a free, external, OpenAI-compatible model endpoint;
2. optionally spawns `opencode run` as a tool-using worker in a directory you choose;
3. appends metadata-only records to a local JSONL ledger.

It is **not** a sandbox, a secrets manager, a policy engine, or a multi-tenant service. It runs with your user's permissions and inherits your network access.

## Threat model

| Threat | Control | Residual risk |
|---|---|---|
| A credential pasted into a prompt reaches a free tier | `redact()` strips common key shapes before egress; the routing policy forbids secrets in free prompts | Redaction is pattern-based. Unusual formats, short secrets, and secrets inside code can pass. Do not rely on it as a DLP system. |
| Proprietary code or customer data reaches a third party | Explicit tier policy plus human review; `NIM_BASE_URL` supports a self-hosted endpoint | The server cannot classify data sensitivity. That is a policy and process control. |
| Prompt injection in repository content makes the worker edit or exfiltrate files | `free_agent` runs with the same permissions as the calling user; approval and diff review are expected | There is no isolated filesystem, network policy, or syscall filter. Treat the worker as untrusted code execution. |
| The worker edits the wrong repository | `cwd` is required and should be absolute; the tool is marked `destructiveHint: true` | A wrong `cwd` or symlink is still possible. Review diffs. |
| A malicious or compromised model endpoint returns instructions or huge output | Output is returned as text inside a tool result; the caller decides what to do | The server does not execute model output. `free_agent` output is produced by the worker, which does execute tools. |
| Ledger privacy | The ledger stores model ids, token counts, latency, scores, `cwd` and timestamps, not prompts or answers | Paths and usage patterns are still sensitive. Protect `~/.frugal-agents/ledger.jsonl` like shell history. |
| Ledger corruption under concurrent workers | `fcntl` lock, append-only writes, schema version, rotation at 10 MB | Locking is POSIX-only; Windows has no lock. Rotated files are kept as `.1` only. |
| A free model disappears or rate-limits the caller | Fallback chain, health cooldown, backoff with jitter | A full outage surfaces as a tool error; it does not silently spend paid tokens. |
| Supply chain | Server has zero runtime dependencies; CI pins GitHub Actions by major version | The optional SDK compatibility test installs `mcp` from PyPI. Pin or vendor it if that matters to you. |

## Deployment guidance

- Run as a normal, unprivileged user. Do not run the MCP server as root.
- Use a dedicated free-tier key with the lowest available quota and rotate it if a prompt is suspected of leaking it.
- Keep the repository and the worker `cwd` separate from `~/.ssh`, cloud credentials, and customer data.
- Leave `free_agent` behind an approval prompt unless you have reviewed the worker's permissions. For non-interactive Codex runs, `approval_mode = "approve"` is required, which means you have accepted unattended file edits.
- Prefer a self-hosted endpoint (`NIM_BASE_URL`) when data must not leave the host.
- Set `FRUGAL_NO_LEDGER=1` if local metadata retention is unacceptable.
- Audit `opencode` and any other worker binary you put in `PATH`; the server does not verify it.

## Security-relevant defaults

| Default | Value | Why |
|---|---|---|
| Redaction | on | best-effort credential stripping before egress |
| Task transport | stdin | avoids exposing task text in `ps`/argv |
| Worker cancellation | process group | prevents orphaned children after a timeout |
| HTTP retries | 2 per model, jittered | survives 429/5xx without a tight loop |
| Concurrency | 4 | limits accidental load and rate-limit storms |
| Ledger lock | POSIX `flock` | prevents interleaved JSONL writes |
| Ledger content | metadata only | reduces the blast radius of a local read |

## Non-goals

- Sandboxing the worker.
- Preventing a determined user from sending secrets to a model.
- Guaranteeing the quality or safety of model output.
- Protecting against a compromised local account or a malicious `opencode` binary.
