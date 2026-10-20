#!/usr/bin/env python3
"""
frugal-agents — free-models MCP server.

Routes easy and medium work to free NVIDIA NIM models and keeps the paid
model for decisions and final review. Zero dependencies: Python 3 stdlib only.

Modes
-----
    server.py                     run the MCP stdio server
    server.py ask "..."           one-shot text completion
    server.py agent "..."         delegate a tool-using task to `opencode run`
    server.py cascade "..."       generate + judge, with an escalation verdict
    server.py stats [--days N]    escalation, cache hit rate and latency from the ledger
    server.py models              print the configured model chain
    server.py --version           print version

Environment
-----------
    NIM_API_KEY         NVIDIA NIM API key (https://build.nvidia.com)
    NIM_API_KEY_FILE    file containing the key (default ~/.secrets/nvidia-key)
    NIM_BASE_URL        default https://integrate.api.nvidia.com/v1
    FRUGAL_MODEL        default generator model
    FRUGAL_CHAIN        comma-separated fallback chain
    FRUGAL_JUDGE        default judge model
    FRUGAL_THRESHOLD    default judge acceptance threshold (0.8)
    FRUGAL_LEDGER       ledger path (default ~/.frugal-agents/ledger.jsonl)
    FRUGAL_TIMEOUT      HTTP timeout in seconds (default 75)
    FRUGAL_NO_LEDGER    set to 1 to disable the ledger
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from typing import Any

VERSION = "0.2.0"
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "free-models"

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = os.environ.get("FRUGAL_MODEL", "openai/gpt-oss-20b")
DEFAULT_JUDGE = os.environ.get("FRUGAL_JUDGE", "google/gemma-4-31b-it")
DEFAULT_THRESHOLD = float(os.environ.get("FRUGAL_THRESHOLD", "0.8"))

# Tried in order when the requested model is gone (NIM rotates its catalog and
# answers HTTP 410 for retired models) or returns empty content.
DEFAULT_CHAIN = [
    "openai/gpt-oss-20b",
    "google/gemma-4-31b-it",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "z-ai/glm-5.3",
    "deepseek-ai/deepseek-v4.1-flash",
]

RETRYABLE_HTTP = {404, 408, 410, 409, 425, 429, 500, 502, 503, 504}

# Models that just failed are skipped for a while so one dead entry in the
# catalog does not add a timeout to every single call.
_UNHEALTHY: dict[str, tuple[float, str]] = {}


def unhealthy(model: str) -> str | None:
    hit = _UNHEALTHY.get(model)
    if not hit:
        return None
    expires, reason = hit
    if time.time() >= expires:
        _UNHEALTHY.pop(model, None)
        return None
    return reason


def mark_unhealthy(model: str, reason: str, status: int | None) -> None:
    ttl = 900 if status in {404, 410} else 180
    _UNHEALTHY[model] = (time.time() + ttl, reason)

JUDGE_SYSTEM = """You are a strict, concrete code and writing reviewer.
Decide whether the ANSWER fully satisfies the TASK.

Return ONLY a JSON object, no markdown fence, no prose:
{"score": <number 0..1>, "verdict": "accept" | "reject", "issues": ["..."]}

Scoring guide:
1.0  correct, complete and directly usable
0.7  correct but has minor style or clarity issues
0.4  partially correct, incomplete, or ignores part of the task
0.0  wrong, unsafe, empty, or does not answer the task

Reject when the answer would need a human to repair it before use.
Be strict about correctness, but do not invent requirements that are not in the task."""


class FrugalError(RuntimeError):
    """Fatal error: bad configuration, missing key, unusable response."""


class RetryableError(RuntimeError):
    """This model failed in a way that another model may survive."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_api_key() -> str:
    key = os.environ.get("NIM_API_KEY", "").strip()
    if key:
        return key

    path = os.environ.get(
        "NIM_API_KEY_FILE", os.path.expanduser("~/.secrets/nvidia-key")
    )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            key = handle.read().strip()
    except OSError:
        key = ""

    if not key:
        raise FrugalError(
            "NIM_API_KEY is not set and no key file was found. "
            "Create a free key at https://build.nvidia.com and set NIM_API_KEY."
        )
    return key


def base_url() -> str:
    return os.environ.get("NIM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def chain_for(model: str | None) -> list[str]:
    """Requested model first, then the configured fallback chain, deduplicated."""
    candidates = [model] if model else [DEFAULT_MODEL]
    candidates += env_list("FRUGAL_CHAIN") or DEFAULT_CHAIN

    seen: set[str] = set()
    ordered: list[str] = []
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


# --------------------------------------------------------------------------- #
# Secret redaction
# --------------------------------------------------------------------------- #

SECRET_PATTERNS = [
    r"nvapi-[A-Za-z0-9_\-]{16,}",
    r"sk-[A-Za-z0-9_\-]{16,}",
    r"gh[pousr]_[A-Za-z0-9]{16,}",
    r"github_pat_[A-Za-z0-9_]{16,}",
    r"AKIA[0-9A-Z]{16}",
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    r"\b(api[_-]?key|secret|token|password|passwd|client[_-]?secret)\b"
    r"\s*[:=]\s*(['\"]?)[^\s'\"]{6,}\2",
]

SECRET_RE = re.compile(
    "|".join(f"(?:{pattern})" for pattern in SECRET_PATTERNS), re.IGNORECASE
)


def redact(text: str) -> str:
    """Strip the most common credential shapes before any text leaves the host."""
    if not text:
        return text
    return SECRET_RE.sub("[REDACTED]", text)


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #


def ledger_path() -> str:
    return os.environ.get(
        "FRUGAL_LEDGER", os.path.expanduser("~/.frugal-agents/ledger.jsonl")
    )


def record(entry: dict[str, Any]) -> None:
    if os.environ.get("FRUGAL_NO_LEDGER") == "1":
        return
    try:
        path = ledger_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry["ts"] = round(time.time(), 3)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # The ledger is observability, never a reason to fail a request.
        pass


def read_ledger(path: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with open(path or ledger_path(), "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def compute_stats(
    rows: list[dict[str, Any]], days: int | None = None
) -> dict[str, Any]:
    """Cascade escalation rate, cache hit rate and latency from the ledger."""
    if days:
        cutoff = time.time() - days * 86400
        rows = [row for row in rows if _int(row.get("ts")) >= cutoff]

    by_tool = Counter(str(row.get("tool", "unknown")) for row in rows)
    models = Counter(
        str(row.get("model", "unknown")) for row in rows if row.get("model")
    )

    cascades = [row for row in rows if row.get("tool") == "free_cascade"]
    accepted = sum(1 for row in cascades if row.get("accepted") is True)
    escalated = len(cascades) - accepted

    prompt_tokens = cached_tokens = cache_write_tokens = completion_tokens = 0
    for row in rows:
        usage = row.get("usage") or {}
        prompt_tokens += _int(usage.get("prompt_tokens"))
        cached_tokens += _int(usage.get("cached_tokens"))
        cache_write_tokens += _int(usage.get("cache_write_tokens"))
        completion_tokens += _int(usage.get("completion_tokens"))

    latencies = sorted(_int(row.get("latency_ms")) for row in rows if row.get("latency_ms"))
    p95 = (
        latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else 0
    )

    return {
        "calls": len(rows),
        "by_tool": dict(by_tool),
        "models": dict(models),
        "cascade": {
            "total": len(cascades),
            "accepted": accepted,
            "escalated": escalated,
            "escalation_rate": round(escalated / len(cascades), 4) if cascades else 0.0,
        },
        "cache": {
            "prompt_tokens": prompt_tokens,
            "cached_tokens": cached_tokens,
            "cache_write_tokens": cache_write_tokens,
            "completion_tokens": completion_tokens,
            "hit_rate": round(cached_tokens / prompt_tokens, 4) if prompt_tokens else 0.0,
        },
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies)) if latencies else 0,
            "p95": p95,
        },
    }


def format_stats(stats: dict[str, Any], days: int | None = None) -> str:
    period = f"last {days}d" if days else "all time"
    lines = [f"calls: {stats['calls']} ({period})"]
    if not stats["calls"]:
        return lines[0] + "\nno ledger entries yet"

    tools = ", ".join(f"{name}={count}" for name, count in stats["by_tool"].items())
    lines.append(f"tools: {tools}")

    if stats["models"]:
        models = ", ".join(
            f"{name}={count}"
            for name, count in sorted(
                stats["models"].items(), key=lambda item: item[1], reverse=True
            )[:5]
        )
        lines.append(f"models: {models}")

    cascade = stats["cascade"]
    if cascade["total"]:
        lines.append(
            f"cascade: {cascade['accepted']}/{cascade['total']} accepted · "
            f"escalation {cascade['escalation_rate']:.1%}"
        )
    else:
        lines.append("cascade: no calls yet")

    cache = stats["cache"]
    lines.append(
        f"cache: {cache['cached_tokens']:,}/{cache['prompt_tokens']:,} prompt tokens "
        f"({cache['hit_rate']:.1%} hit rate) · write {cache['cache_write_tokens']:,}"
    )
    lines.append(
        f"latency: avg {stats['latency_ms']['avg']:,} ms · "
        f"p95 {stats['latency_ms']['p95']:,} ms"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# NIM client
# --------------------------------------------------------------------------- #


def timeout_seconds() -> float:
    try:
        return float(os.environ.get("FRUGAL_TIMEOUT", "75"))
    except ValueError:
        return 120.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Map provider-specific usage fields onto one billing shape.

    OpenAI / NIM   prompt_tokens_details.cached_tokens
    DeepSeek       prompt_cache_hit_tokens / prompt_cache_miss_tokens
    Anthropic      cache_read_input_tokens / cache_creation_input_tokens
                   (input_tokens excludes both, so the cacheable prompt is the sum)

    Cache hit rate is cached tokens divided by the total prompt tokens. A low
    rate on an agent loop means the prefix is volatile, not that caching is off.
    """
    usage = usage or {}

    prompt = _int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion = _int(usage.get("completion_tokens") or usage.get("output_tokens"))

    details = usage.get("prompt_tokens_details") or {}
    cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
    cached = _int(usage.get("prompt_cache_hit_tokens")) or cached  # DeepSeek
    cached = _int(usage.get("cache_read_input_tokens")) or cached  # Anthropic
    cache_write = _int(usage.get("cache_creation_input_tokens"))  # Anthropic

    anthropic_style = (
        "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage
    )
    if anthropic_style:
        prompt += cached + cache_write

    completion_details = usage.get("completion_tokens_details") or {}
    reasoning = (
        _int(completion_details.get("reasoning_tokens"))
        if isinstance(completion_details, dict)
        else 0
    )
    reasoning = _int(usage.get("reasoning_tokens")) or reasoning

    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
        "cache_hit_rate": round(cached / prompt, 4) if prompt else 0.0,
        "total_tokens": _int(usage.get("total_tokens")) or prompt + completion,
    }


def merge_usage(*usages: dict[str, Any] | None) -> dict[str, Any]:
    """Sum several calls (for example a generator plus its judge)."""
    merged = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
    }
    for usage in usages:
        if not usage:
            continue
        for key in merged:
            merged[key] += _int(usage.get(key))
    merged["cache_hit_rate"] = (
        round(merged["cached_tokens"] / merged["prompt_tokens"], 4)
        if merged["prompt_tokens"]
        else 0.0
    )
    return merged


def chat(
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 2048,
    temperature: float = 0.2,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort

    request = urllib.request.Request(
        f"{base_url()}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {load_api_key()}",
            "User-Agent": f"frugal-agents/{VERSION}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds()) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        message = f"HTTP {error.code} from {model}: {detail}"
        if error.code in RETRYABLE_HTTP:
            raise RetryableError(message, error.code) from error
        raise FrugalError(message) from error
    except TimeoutError as error:
        raise RetryableError(f"timeout from {model}", None) from error
    except urllib.error.URLError as error:
        raise FrugalError(f"cannot reach NIM: {error.reason}") from error
    except OSError as error:
        raise RetryableError(f"connection error from {model}: {error}", None) from error
    except json.JSONDecodeError as error:
        raise RetryableError(f"invalid JSON from {model}: {error}", None) from error

    try:
        choice = data["choices"][0]
        message = choice.get("message") or {}
    except (KeyError, IndexError, TypeError) as error:
        raise RetryableError(f"unexpected response shape from {model}", None) from error

    content = message.get("content") or ""
    if isinstance(content, list):  # some gateways return content parts
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""

    return {
        "text": content.strip(),
        "reasoning": reasoning.strip() if isinstance(reasoning, str) else "",
        "finish_reason": choice.get("finish_reason"),
        "usage": normalize_usage(data.get("usage")),
        "model": data.get("model") or model,
    }


def complete(
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Call the free chain until one model returns usable text."""
    prompt = redact(prompt)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": redact(system)})
    messages.append({"role": "user", "content": prompt})

    failures: list[str] = []
    started = time.time()

    candidates = chain_for(model)
    healthy = [item for item in candidates if not unhealthy(item)]
    if healthy:
        candidates = healthy

    for candidate in candidates:
        try:
            result = chat(
                candidate,
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
            )
            # Reasoning models can spend the whole budget thinking and return
            # content: null. Retry once with a larger budget before giving up.
            if not result["text"] and result["reasoning"]:
                result = chat(
                    candidate,
                    messages,
                    max_tokens=max(max_tokens * 2, 4096),
                    temperature=temperature,
                    reasoning_effort=reasoning_effort,
                )
            if not result["text"]:
                failures.append(f"{candidate}: empty content ({result['finish_reason']})")
                continue

            result.update(
                {
                    "requested_model": model or DEFAULT_MODEL,
                    "latency_ms": int((time.time() - started) * 1000),
                    "fallbacks": len(failures),
                }
            )
            record(
                {
                    "tool": "free_ask",
                    "model": result["model"],
                    "usage": result["usage"],
                    "latency_ms": result["latency_ms"],
                    "fallbacks": len(failures),
                }
            )
            return result
        except RetryableError as error:
            failures.append(str(error))
            mark_unhealthy(candidate, str(error), error.status)
        except FrugalError:
            raise

    raise FrugalError("all free models failed:\n- " + "\n- ".join(failures))


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


def free_ask(
    prompt: str,
    model: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.2,
) -> dict[str, Any]:
    result = complete(
        prompt,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return {
        "answer": result["text"],
        "model": result["model"],
        "usage": result["usage"],
        "latency_ms": result["latency_ms"],
        "fallbacks": result["fallbacks"],
    }


def parse_verdict(raw: str) -> dict[str, Any]:
    """Extract the judge JSON even when the model wraps it in prose or fences."""
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {"score": None, "verdict": "reject", "issues": ["unparsable verdict"]}

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"score": None, "verdict": "reject", "issues": ["invalid verdict JSON"]}

    score = data.get("score")
    try:
        score = max(0.0, min(1.0, float(score)))
    except (TypeError, ValueError):
        score = None

    verdict = str(data.get("verdict", "")).lower().strip()
    if verdict not in {"accept", "reject"}:
        verdict = "accept" if score is not None and score >= DEFAULT_THRESHOLD else "reject"

    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = [str(issues)]

    return {"score": score, "verdict": verdict, "issues": [str(i) for i in issues]}


def free_cascade(
    task: str,
    generator: str | None = None,
    judge: str | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Generate with a free model, then have a different free model review it."""
    started = time.time()
    generated = complete(task, model=generator, max_tokens=max_tokens)

    judge_prompt = (
        f"TASK:\n{task}\n\n"
        f"ANSWER:\n{generated['text']}\n\n"
        "Review the answer against the task and return the JSON verdict."
    )
    review = complete(
        judge_prompt,
        model=judge or DEFAULT_JUDGE,
        system=JUDGE_SYSTEM,
        max_tokens=700,
        temperature=0.0,
    )
    verdict = parse_verdict(review["text"])
    accepted = verdict["verdict"] == "accept" and (
        verdict["score"] is None or verdict["score"] >= threshold
    )

    result = {
        "accepted": accepted,
        "score": verdict["score"],
        "issues": verdict["issues"],
        "answer": generated["text"],
        "generator": generated["model"],
        "judge": review["model"],
        "escalate_to_paid": not accepted,
        "latency_ms": int((time.time() - started) * 1000),
    }
    record(
        {
            "tool": "free_cascade",
            "model": generated["model"],
            "judge": review["model"],
            "usage": merge_usage(generated["usage"], review["usage"]),
            "score": verdict["score"],
            "accepted": accepted,
            "escalate_to_paid": not accepted,
            "latency_ms": result["latency_ms"],
        }
    )
    return result


def free_agent(
    task: str,
    model: str | None = None,
    cwd: str | None = None,
    extra_args: list[str] | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    """Delegate a task that needs file tools to an `opencode run` worker."""
    selected = model or DEFAULT_MODEL
    command = [
        "opencode",
        "run",
        "-m",
        f"nim/{selected}",
        "--format",
        "json",
        *(extra_args or []),
        redact(task),
    ]

    try:
        proc = subprocess.run(
            command,
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise FrugalError(
            "the `opencode` CLI was not found in PATH; install it from "
            "https://opencode.ai to use free_agent"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise FrugalError(f"free_agent timed out after {timeout}s") from error

    texts: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "text" and part.get("type") == "text":
            texts.append(part.get("text", ""))

    output = "\n".join(texts).strip()
    if proc.returncode != 0 and not output:
        raise FrugalError(
            f"opencode exited with {proc.returncode}: {proc.stderr.strip()[:400]}"
        )

    record(
        {
            "tool": "free_agent",
            "model": selected,
            "cwd": cwd,
            "returncode": proc.returncode,
            "chars": len(output),
        }
    )
    return {
        "answer": output,
        "model": selected,
        "returncode": proc.returncode,
        "stderr": proc.stderr.strip()[-400:],
    }


# --------------------------------------------------------------------------- #
# MCP plumbing
# --------------------------------------------------------------------------- #

TOOLS = [
    {
        "name": "free_ask",
        "description": (
            "Ask a free model (NVIDIA NIM) for text: commit messages, summaries, "
            "regex, docstrings, explanations, small refactors. Never send secrets. "
            "Falls back across the free model chain automatically."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The full task text."},
                "model": {
                    "type": "string",
                    "description": "Optional NIM model id, e.g. z-ai/glm-5.3.",
                },
                "max_tokens": {"type": "integer", "minimum": 64, "default": 2048},
                "temperature": {"type": "number", "minimum": 0, "maximum": 2},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "free_agent",
        "description": (
            "Delegate a task that needs file tools to an OpenCode worker running a "
            "free NIM model. Use for isolated implementations, tests, and small "
            "refactors. Pass an absolute cwd so the worker edits the right project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What the worker must do."},
                "model": {"type": "string", "description": "Optional NIM model id."},
                "cwd": {"type": "string", "description": "Absolute project directory."},
                "extra_args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra `opencode run` arguments.",
                },
                "timeout": {"type": "integer", "minimum": 30, "default": 600},
            },
            "required": ["task", "cwd"],
        },
    },
    {
        "name": "free_cascade",
        "description": (
            "Generate with one free model and review with a different free model "
            "using a strict checklist. Returns accepted/score/issues and an "
            "escalate_to_paid flag. Use before spending paid tokens."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task to solve."},
                "generator": {"type": "string", "description": "Optional generator model id."},
                "judge": {"type": "string", "description": "Optional judge model id."},
                "threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": DEFAULT_THRESHOLD,
                },
                "max_tokens": {"type": "integer", "minimum": 64, "default": 2048},
            },
            "required": ["task"],
        },
    },
]


def tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "free_ask":
        return free_ask(
            str(arguments["prompt"]),
            model=arguments.get("model"),
            max_tokens=int(arguments.get("max_tokens", 2048)),
            temperature=float(arguments.get("temperature", 0.2)),
        )
    if name == "free_agent":
        return free_agent(
            str(arguments["task"]),
            model=arguments.get("model"),
            cwd=arguments.get("cwd"),
            extra_args=arguments.get("extra_args"),
            timeout=int(arguments.get("timeout", 600)),
        )
    if name == "free_cascade":
        return free_cascade(
            str(arguments["task"]),
            generator=arguments.get("generator"),
            judge=arguments.get("judge"),
            threshold=float(arguments.get("threshold", DEFAULT_THRESHOLD)),
            max_tokens=int(arguments.get("max_tokens", 2048)),
        )
    raise FrugalError(f"unknown tool: {name}")


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(request_id: Any, result: Any) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def reply_error(request_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def handle(request: dict[str, Any]) -> None:
    request_id = request.get("id")
    method = request.get("method", "")

    # Notifications carry no id and must not be answered.
    if request_id is None:
        return

    if method == "initialize":
        reply(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": VERSION},
            },
        )
        return

    if method == "ping":
        reply(request_id, {})
        return

    if method == "tools/list":
        reply(request_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            result = tool_call(name, arguments)
            reply(
                request_id,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ],
                    "isError": False,
                },
            )
        except FrugalError as error:
            reply(
                request_id,
                {
                    "content": [{"type": "text", "text": f"error: {error}"}],
                    "isError": True,
                },
            )
        except Exception as error:  # never kill the server on one bad call
            reply(
                request_id,
                {
                    "content": [{"type": "text", "text": f"error: {type(error).__name__}: {error}"}],
                    "isError": True,
                },
            )
        return

    reply_error(request_id, -32601, f"method not found: {method}")


def serve() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            reply_error(None, -32700, "parse error")
            continue
        handle(request)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def usage() -> str:
    return __doc__ or ""


def main(argv: list[str]) -> int:
    if not argv:
        return serve()

    command = argv[0]
    if command in {"--version", "-V"}:
        print(VERSION)
        return 0
    if command in {"--help", "-h", "help"}:
        print(usage())
        return 0
    if command == "models":
        print(f"generator: {DEFAULT_MODEL}")
        print(f"judge:     {DEFAULT_JUDGE}")
        print(f"chain:     {', '.join(chain_for(None))}")
        return 0
    if command == "stats":
        days: int | None = None
        if "--days" in argv:
            index = argv.index("--days")
            if index + 1 >= len(argv) or not argv[index + 1].isdigit():
                print("error: --days needs a number", file=sys.stderr)
                return 2
            days = int(argv[index + 1])
        stats = compute_stats(read_ledger(), days=days)
        if "--json" in argv:
            print(json.dumps(stats, indent=2))
        else:
            print(format_stats(stats, days))
        return 0

    text = " ".join(argv[1:]).strip()
    if not text:
        print(f"missing task text\n\n{usage()}", file=sys.stderr)
        return 2

    try:
        if command == "ask":
            result = free_ask(text)
            print(result["answer"])
            print(
                f"\n[model={result['model']} latency={result['latency_ms']}ms "
                f"fallbacks={result['fallbacks']}]",
                file=sys.stderr,
            )
            return 0
        if command == "agent":
            result = free_agent(text, cwd=os.getcwd())
            print(result["answer"])
            return 0
        if command == "cascade":
            result = free_cascade(text)
            print(result["answer"])
            print(json.dumps({k: v for k, v in result.items() if k != "answer"}, indent=2), file=sys.stderr)
            return 0 if result["accepted"] else 3
    except FrugalError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"unknown command: {command}\n\n{usage()}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
