# Harness compatibility

Last updated: 2026-10-20 · server `0.3.1`

This page separates what has actually been run from what merely looks compatible. Most MCP clients implement the same stdio JSON-RPC transport, but they differ in protocol revision, approval behaviour, environment inheritance and how they render tool results.

## 1. Protocol revisions

The server negotiates the session revision during `initialize`. It accepts the client's revision when it is in the list below; otherwise it replies with its newest supported revision.

| Revision | Status | Notes |
|---|---|---|
| `2024-11-05` | supported | original session protocol; broadest client support |
| `2025-03-26` | supported | tool annotations are understood by newer clients |
| `2025-06-18` | supported | the server does not emit `outputSchema` / `structuredContent` yet |
| `2025-11-25` | supported | newest revision compiled into this server; default when the client asks for something unknown |
| `2026-07-28` | **not supported** | stateless transport, header routing and MRTR are not implemented |

When a client asks for an unsupported revision, the server logs one line to stderr:

```text
free-models: client requested MCP 2026-07-28; replying with 2025-11-25
```

A client that *requires* the stateless revision will reject that reply. This is the largest known compatibility gap; see [`04-roadmap.md`](04-roadmap.md).

## 2. Tested matrix

| Client | Transport | Status | Evidence |
|---|---|---|---|
| Codex CLI | stdio | **tested end-to-end** | `free_ask` and `free_cascade` called live through the MCP server; non-interactive `codex exec` also verified |
| Raw JSON-RPC client | stdio | **tested** | unit and concurrency tests in `mcp/free-models/tests/test_server.py` |
| Official MCP Python SDK | stdio | **tested in CI** | `tests/mcp_sdk_smoke.py` installs the SDK and performs `initialize` + `tools/list` |
| Claude Code | stdio | untested | configuration below is unverified |
| Cursor | stdio | untested | configuration below is unverified |
| OpenCode (as MCP client) | stdio | untested | OpenCode is tested as the **worker** behind `free_agent`, not as an MCP client |
| Zed | stdio | untested | configuration below is unverified |
| Cline | stdio | untested | configuration below is unverified |
| Gemini CLI | stdio | untested | configuration below is unverified |

If you test one of the untested rows, open an issue with the client version, the negotiated protocol, and the failing request. That is more useful than a general "works/doesn't work" report.

## 3. Client configuration

All examples assume the repository is cloned at `/absolute/path/to/frugal-agents` and the key lives at `/absolute/path/to/nvidia-key`. Replace both placeholders. Prefer `NIM_API_KEY_FILE` over putting a key in a config file.

### Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.free-models]
command = "python3"
args = ["/absolute/path/to/frugal-agents/mcp/free-models/server.py"]
env = { NIM_API_KEY_FILE = "/absolute/path/to/nvidia-key" }
startup_timeout_sec = 20
tool_timeout_sec = 900

[mcp_servers.free-models.tools.free_ask]
approval_mode = "approve"

[mcp_servers.free-models.tools.free_agent]
approval_mode = "approve"

[mcp_servers.free-models.tools.free_cascade]
approval_mode = "approve"
```

`approval_mode = "approve"` is required for `codex exec` and CI, which cannot answer an approval prompt. Remove those blocks if you want an interactive prompt. `tool_timeout_sec` must cover the whole fallback chain, not one HTTP request; 900s is deliberate.

### Claude Code

Project `.mcp.json` or the equivalent user-level config:

```json
{
  "mcpServers": {
    "free-models": {
      "command": "python3",
      "args": ["/absolute/path/to/frugal-agents/mcp/free-models/server.py"],
      "env": {
        "NIM_API_KEY_FILE": "/absolute/path/to/nvidia-key"
      }
    }
  }
}
```

CLI form:

```bash
claude mcp add free-models -- python3 /absolute/path/to/frugal-agents/mcp/free-models/server.py
```

### Cursor

`~/.cursor/mcp.json` (or `.cursor/mcp.json` per project):

```json
{
  "mcpServers": {
    "free-models": {
      "command": "python3",
      "args": ["/absolute/path/to/frugal-agents/mcp/free-models/server.py"],
      "env": {
        "NIM_API_KEY_FILE": "/absolute/path/to/nvidia-key"
      }
    }
  }
}
```

### OpenCode (MCP client)

`opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "free-models": {
      "type": "local",
      "command": [
        "python3",
        "/absolute/path/to/frugal-agents/mcp/free-models/server.py"
      ],
      "environment": {
        "NIM_API_KEY_FILE": "/absolute/path/to/nvidia-key"
      },
      "enabled": true
    }
  }
}
```

The key is under `environment` here, not `env`. Check your OpenCode version's schema before copying.

### Zed

`~/.config/zed/settings.json`:

```json
{
  "context_servers": {
    "free-models": {
      "command": {
        "path": "python3",
        "args": ["/absolute/path/to/frugal-agents/mcp/free-models/server.py"],
        "env": {
          "NIM_API_KEY_FILE": "/absolute/path/to/nvidia-key"
        }
      }
    }
  }
}
```

### Cline

`cline_mcp_settings.json`:

```json
{
  "mcpServers": {
    "free-models": {
      "command": "python3",
      "args": ["/absolute/path/to/frugal-agents/mcp/free-models/server.py"],
      "env": {
        "NIM_API_KEY_FILE": "/absolute/path/to/nvidia-key"
      },
      "disabled": false,
      "autoApprove": ["free_ask", "free_cascade"]
    }
  }
}
```

Do **not** auto-approve `free_agent`: it can edit files. The server marks it `destructiveHint: true` for the same reason.

### Gemini CLI

`~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "free-models": {
      "command": "python3",
      "args": ["/absolute/path/to/frugal-agents/mcp/free-models/server.py"],
      "env": {
        "NIM_API_KEY_FILE": "/absolute/path/to/nvidia-key"
      }
    }
  }
}
```

## 4. Behaviour the client must tolerate

### Tool results are JSON inside a text block

The server replies with the legacy content shape:

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\n  \"answer\": \"...\",\n  \"model\": \"...\"\n}"
    }
  ],
  "isError": false
}
```

It does not emit `structuredContent` yet. Clients that render only text are fine; clients that expect a schema-validated structured result must parse the text or wait for the structured-output work.

### Progress is opt-in

If the client sends `_meta.progressToken` with `tools/call`, the server emits two `notifications/progress` messages: `0/1` at start and `1/1` at finish. It does not stream intermediate tokens. Clients that ignore progress notifications lose nothing.

### Cancellation is cooperative

On `notifications/cancelled` the server sets a per-request event and replies with JSON-RPC error `-32800` (`request cancelled`).

- HTTP calls cannot be aborted while a socket read is in progress. The server checks before the request, after the response, and between retries.
- `free_agent` polls every 100 ms and terminates the worker's process group.
- A cancelled request may still have produced a ledger record; the record describes the paid/free usage that actually happened.

### Concurrency

Tool calls run on a thread pool, so `ping`, cancellation and progress keep working while a long `free_agent` runs. The default is 4 workers; override with `FRUGAL_MAX_CONCURRENCY`. Free tiers enforce requests-per-minute limits, so raising this usually makes things worse.

### Timeouts

| Setting | Default | Scope |
|---|---|---|
| `FRUGAL_TIMEOUT` | 75s | one HTTP request, per attempt |
| `free_agent` `timeout` argument | 600s | one worker process |
| client `tool_timeout_sec` | client-specific | whole tool call, including fallbacks and retries |

The client timeout must be larger than the worst case: chain length × (timeout + backoff) or the worker timeout.

### Approvals and side effects

| Tool | Hints | Real effect |
|---|---|---|
| `free_ask` | `readOnlyHint: true`, `openWorldHint: true` | sends one prompt to a cloud model |
| `free_cascade` | `readOnlyHint: true`, `openWorldHint: true` | sends a prompt and an answer to two cloud models |
| `free_agent` | `readOnlyHint: false`, `destructiveHint: true` | runs a tool-using worker that can edit files |

Hints are advisory. A harness may still prompt; a non-interactive harness may refuse. The Codex `approval_mode` block above is the workaround for that client.

## 5. Platform notes

- **Python:** 3.10+; CI covers 3.10, 3.12 and 3.13.
- **Linux/macOS:** tested path. `fcntl` locks the ledger; `start_new_session` + `killpg` cancels the worker tree.
- **Windows:** untested. The code has fallbacks (`proc.terminate()`, no ledger lock), but neither path is in CI. Use WSL for now.
- **NIM key:** the server reads `NIM_API_KEY` first, then the file named by `NIM_API_KEY_FILE`. Set that variable explicitly to an absolute path outside the repository. A missing key is reported by `server.py doctor`.
- **`opencode`:** required only for `free_agent`; `doctor` reports whether it is in `PATH`.

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `MCP tool call requires approval, but approval policy is never` | non-interactive client cannot answer the prompt | add `approval_mode = "approve"` for the three tools |
| Server starts, tool list is empty | `initialize` failed or protocol rejected | check stderr for the negotiated revision; upgrade the client or use a legacy revision |
| `the opencode CLI was not found in PATH` | worker binary missing | install OpenCode or stop using `free_agent` |
| Every call times out | client timeout shorter than the chain | raise `tool_timeout_sec`; lower `FRUGAL_TIMEOUT`; reduce `FRUGAL_CHAIN` |
| `all free models failed` | key invalid, quota exhausted, or catalog rotated | run `server.py doctor`; refresh `FRUGAL_CHAIN` |
| Free work edits the wrong repository | relative or missing `cwd` | pass an absolute `cwd`; keep `free_agent` behind approval |
| Stats look inflated | pre-0.3.0 ledger entries recorded generator and judge separately | old entries are not rewritten; `stats` prints a legacy-row count; compare entries with `schema_version` 1 |

## 7. What would make this page stronger

- A reproducible test script per client, pinned to a client version.
- A CI job that runs the server under the official SDK at the newest legacy revision and at `2024-11-05`.
- A stateless `2026-07-28` transport implementation, or a documented decision to remain legacy-only.
- Confirmation from each client that `notifications/cancelled` and progress tokens are actually delivered.
