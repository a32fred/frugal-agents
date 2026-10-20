#!/usr/bin/env bash
# Live smoke test against the free NIM tier.
#
# Requires a key in NIM_API_KEY or NIM_API_KEY_FILE (default ~/.secrets/nvidia-key).
# Exits 0 when skipped so CI without secrets stays green.

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${NIM_API_KEY:-}" ]]; then
  default_file="${NIM_API_KEY_FILE:-$HOME/.secrets/nvidia-key}"
  if [[ ! -s "$default_file" ]]; then
    echo "SKIP: no NIM API key found (set NIM_API_KEY or NIM_API_KEY_FILE)"
    exit 0
  fi
  export NIM_API_KEY_FILE="$default_file"
fi

echo "== models =="
python3 server.py models

echo "== free_ask =="
answer="$(python3 server.py ask "Reply with exactly: SMOKE_OK" 2>/dev/null)"
if [[ "$answer" != *"SMOKE_OK"* ]]; then
  echo "FAIL: free_ask did not return SMOKE_OK (got: ${answer:0:120})"
  exit 1
fi
echo "ok"

echo "== free_cascade =="
python3 - <<'PY'
import json
import os
import sys

sys.path.insert(0, os.getcwd())
import server  # noqa: E402

result = server.free_cascade(
    "Write a one-line shell command that prints the current git branch. "
    "Reply with the command only.",
    max_tokens=512,
)
print(json.dumps({k: v for k, v in result.items() if k != "answer"}, indent=2))
assert result["answer"].strip(), "empty cascade answer"
assert "accepted" in result and "escalate_to_paid" in result
PY

echo "SMOKE TEST PASSED"
