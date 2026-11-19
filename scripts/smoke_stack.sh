#!/usr/bin/env bash
# Submits a REAL task through the running stack (needs Ollama with the model pulled, or set
# MODEL_KEY=litellm with SAFELOOP_LITELLM_MODEL configured). Prints the final report.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
API="${API:-http://127.0.0.1:8000}"; MODEL_KEY="${MODEL_KEY:-ollama}"
H="Authorization: Bearer ${SAFELOOP_API_TOKEN}"

echo "health:"; curl -s "$API/api/v1/health"; echo
body=$(python3 - <<PY
import json
print(json.dumps({
 "user_request": "Implement is_palindrome(s) -> bool ignoring case and non-alphanumeric characters.",
 "test_strategy": "from solution import is_palindrome\n\ndef test_a():\n    assert is_palindrome('A man, a plan, a canal: Panama')\n\ndef test_b():\n    assert not is_palindrome('hello')\n\ndef test_c():\n    assert is_palindrome('')\n",
 "maximum_attempts": 3, "maximum_cost": 0.5, "timeout_seconds": 300, "model_preferences": ["${MODEL_KEY}"]}))
PY
)
tid=$(curl -fsS -X POST "$API/api/v1/tasks" -H "$H" -H 'Content-Type: application/json' -d "$body" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["task_id"])')
echo "task: $tid"
for _ in $(seq 1 150); do
  st=$(curl -fsS "$API/api/v1/tasks/$tid" -H "$H" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status"])')
  case "$st" in PENDING|RUNNING) sleep 2 ;; *) break ;; esac
done
echo "final status: $st"
curl -fsS "$API/api/v1/tasks/$tid/report" -H "$H" | python3 -m json.tool
echo; echo "metrics sample:"; curl -s "$API/metrics" | grep -E '^safeloop_(tasks_total|repairs_total|verification_runs_total)\{' || true
