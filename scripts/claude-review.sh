#!/usr/bin/env bash
set -euo pipefail

# Invocation-level opt-in. Check it before
# dependency probes, stdin reads, state creation, or model/queue requests.
if [[ "${1:-}" != "--claude-review" ]]; then
  echo "ERROR: Claude review is disabled. Explicitly pass --claude-review as the first argument to enable it." >&2
  exit 18
fi
shift
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
if [[ "$SCRIPT_DIR" == "${BASH_SOURCE[0]}" ]]; then SCRIPT_DIR=.; fi
SCRIPT_DIR="$(cd "$SCRIPT_DIR" && pwd -P)"

# Keep the original synchronous interface; lifecycle options use the supervisor.
if (( $# > 1 )) || [[ "${1:-}" == --* ]] || [[ "${1:-}" == -h ]] ||
   [[ "${1:-}" =~ ^(inspect|resume|stop|retry-callback|cleanup|__worker)$ ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: required command 'python3' not found in PATH." >&2
    exit 20
  fi
  exec python3 "$SCRIPT_DIR/review_runner.py" "$@"
fi

MODE="${1:-final}"
# Use the same executable selection as skills/claude/scripts/claude-stage.ts.
CCB_BIN="${CLAUDE_CCB_BIN:-ccb}"

case "$MODE" in
  commitment|final) ;;
  *)
    echo "ERROR: invalid review mode: $MODE (must be commitment or final)" >&2
    exit 19
    ;;
esac

for cmd in "$CCB_BIN" git python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: required command '$cmd' not found in PATH." >&2
    exit 20
  fi
done

if [[ "$(git rev-parse --is-inside-work-tree 2>/dev/null)" != "true" ]]; then
  echo "ERROR: external review requires a Git working tree." >&2
  exit 21
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

MODEL="${CLAUDE_REVIEW_MODEL:-opus}"
EFFORT="${CLAUDE_REVIEW_EFFORT:-high}"
if [[ -z "${MODEL//[[:space:]]/}" || "$MODEL" == -* ]] ||
   [[ ! "$EFFORT" =~ ^(low|medium|high|xhigh|max)$ ]]; then
  echo "ERROR: invalid CCB model or effort configuration." >&2
  exit 22
fi

review_session="${MAGENT_REVIEW_SESSION_ID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
if [[ ! "$review_session" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
  echo "ERROR: invalid persistent CCB session ID." >&2
  exit 22
fi
session_args=(--session-id "$review_session")
if [[ "${MAGENT_REVIEW_RESUME:-0}" == 1 ]]; then
  session_args=(--resume "$review_session")
fi

review_packet="$(cat)"
if [[ -z "${review_packet//[[:space:]]/}" ]]; then
  echo "ERROR: empty review packet." >&2
  exit 22
fi

repo_fingerprint() {
  python3 "$SCRIPT_DIR/review_support.py"
}

if ! before_fingerprint="$(repo_fingerprint)"; then
  echo "ERROR: could not capture repository state before review." >&2
  exit 28
fi

# The reviewer has no Bash tool. Supply Git evidence independently, including
# staged changes, and let Read/Glob/Grep inspect the actual surrounding sources.
git_context() {
  printf '%s\n' '=== GIT STATUS ==='
  git status --porcelain=v1 -uall || return $?
  printf '%s\n' '=== STAGED DIFF ==='
  git diff --cached --binary --no-ext-diff --no-textconv -- || return $?
  printf '%s\n' '=== UNSTAGED DIFF ==='
  git diff --binary --no-ext-diff --no-textconv -- || return $?
}
if ! review_context="$(git_context)"; then
  echo "ERROR: could not capture Git review context." >&2
  exit 24
fi

schema='{
  "type": "object",
  "properties": {
    "verdict": { "type": "string", "enum": ["ship", "fix-first", "rethink"] },
    "summary": { "type": "string" },
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "severity": { "type": "string", "enum": ["critical", "high", "medium", "low"] },
          "file": { "type": "string" },
          "line": { "type": ["integer", "null"] },
          "issue": { "type": "string" },
          "recommendation": { "type": "string" }
        },
        "required": ["severity", "file", "line", "issue", "recommendation"],
        "additionalProperties": false
      }
    }
  },
  "required": ["verdict", "summary", "findings"],
  "additionalProperties": false
}'

if [[ "$MODE" == "commitment" ]]; then
  review_purpose="This is a COMMITMENT-BOUNDARY review. Assess the proposed architecture, API, and migration plan before code is written. Return ship if direction is sound, fix-first if adjustments are required, rethink if fundamentally flawed."
else
  review_purpose="This is a FINAL IMPLEMENTATION review. Inspect accumulated git diff and surrounding code against objectives and constraints. Return ship if ready, fix-first for localized bugs, rethink for structural design flaws."
fi

prompt="You are an independent, read-only code reviewer.
You did NOT implement this change.
$review_purpose

INDEPENDENCE & CONSTRAINTS:
1. You MUST NOT modify, create, or delete any files.
2. Inspect the supplied Git evidence and independently read surrounding source using Read, Glob and Grep.
3. Ignore cosmetic/formatting nitpicks; focus on correctness, regressions, edge cases, and security.
4. Output structured output adhering to the required JSON schema."

review_status=0
if raw_result="$(
  printf '%s\n\n%s\n' "$review_packet" "$review_context" |
  "$CCB_BIN" -p "$prompt" \
    --model "$MODEL" \
    --effort "$EFFORT" \
    "${session_args[@]}" \
    --progress \
    --disable-slash-commands \
    --no-chrome \
    --strict-mcp-config \
    --mcp-config '{"mcpServers":{}}' \
    --setting-sources user \
    --permission-mode dontAsk \
    --tools "Read,Glob,Grep" \
    --allowed-tools \
      "Read" \
      "Glob" \
      "Grep" \
    --disallowed-tools \
      "Edit" \
      "Write" \
      "NotebookEdit" \
      "Bash" \
      "WebFetch" \
      "WebSearch" \
      "Task" \
      "mcp__*" \
    --settings '{"sandbox":{"enabled":true,"failIfUnavailable":true,"autoAllowBashIfSandboxed":false,"allowUnsandboxedCommands":false,"network":{"allowedDomains":[]}}}' \
    --output-format json \
    --json-schema "$schema"
)"; then
  review_status=0
else
  review_status=$?
  echo "ERROR: CCB review failed with exit status $review_status." >&2
fi

# Check repository state even when the CLI failed. A mutation takes precedence
# over a CLI failure; neither path may emit an acceptance verdict.
if ! after_fingerprint="$(repo_fingerprint)"; then
  echo "ERROR: could not capture repository state after review." >&2
  exit 29
fi

if [[ "$before_fingerprint" != "$after_fingerprint" ]]; then
  echo "ERROR: repository state changed during external Claude review! Aborting." >&2
  exit 30
fi

if (( review_status != 0 )); then
  exit "$review_status"
fi

# Python enforces the CCB envelope, exact schema and verdict consistency
# on all platforms, without an additional jq dependency.
printf '%s' "$raw_result" | python3 "$SCRIPT_DIR/review_result.py"
