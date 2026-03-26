#!/usr/bin/env bash
# tokencast-agent-hook.sh — PreToolUse / PostToolUse hook for Agent tool
#
# Expected hook payload fields (update this comment if Claude Code payload format changes):
#   hookEventName: "PreToolUse" | "PostToolUse"
#   toolName / tool_name: "Agent" (we self-filter on this)
#   tool_input.name: agent name string
#   session_id: session identifier (may be absent — see fallback below)
#
# SCHEMA CONTRACT (E3): The sidecar event schema (schema_version=1) is a versioned API
# contract. Fields may be ADDED to v1 events without breaking readers (additive-only).
# Removing or renaming existing fields, or changing their types, requires bumping
# schema_version to 2 and updating all readers (sum-session-tokens.py, tests).
# Do not break v1 without a migration plan.

set -euo pipefail

{
    # F5: Capture stdin to variable first — same pattern as midcheck.sh
    STDIN_JSON=$(cat)

    # Field extraction via python3 inline (pass STDIN_JSON via env var — shell-injection-safe)
    HOOK_FIELDS=$(HOOK_STDIN="$STDIN_JSON" python3 -c "
import json, os, shlex
try:
    d = json.loads(os.environ['HOOK_STDIN'])
except Exception:
    print('HOOK_EVENT_NAME=')
    print('TOOL_NAME=')
    print('AGENT_NAME=')
    print('SESSION_ID=')
    raise SystemExit(0)
print(f'HOOK_EVENT_NAME={shlex.quote(str(d.get(\"hookEventName\", \"\")))}')
print(f'TOOL_NAME={shlex.quote(str(d.get(\"toolName\", d.get(\"tool_name\", \"\"))))}')
ti = d.get('tool_input') or {}
print(f'AGENT_NAME={shlex.quote(str(ti.get(\"name\", \"\")).lower().strip())}')
print(f'SESSION_ID={shlex.quote(str(d.get(\"session_id\", \"\")))}')
" 2>/dev/null) || exit 0
    eval "$HOOK_FIELDS"

    # Self-filter: only handle Agent tool events (F1 — no parent_agent needed; hook records
    # top-level events only; nesting is inferred at read time from chronological span order)
    [ "$TOOL_NAME" = "Agent" ] || exit 0
    [ -n "$AGENT_NAME" ] || exit 0

    # Path setup
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    SKILL_DIR="$(dirname "$SCRIPT_DIR")"
    CALIBRATION_DIR="$SKILL_DIR/calibration"
    mkdir -p "$CALIBRATION_DIR"

    # Session ID — use payload value; fall back to deterministic hash (F6: printf '%s', cross-platform)
    if [ -z "$SESSION_ID" ]; then
        HASH_INPUT="$CALIBRATION_DIR/active-estimate.json"
        if command -v md5 >/dev/null 2>&1; then
            SESSION_ID=$(printf '%s' "$HASH_INPUT" | md5 | cut -c1-12)
        elif command -v md5sum >/dev/null 2>&1; then
            SESSION_ID=$(printf '%s' "$HASH_INPUT" | md5sum | cut -c1-12)
        else
            SESSION_ID="unknown"
        fi
    fi

    SIDECAR_FILE="$CALIBRATION_DIR/${SESSION_ID}-timeline.jsonl"

    # JSONL line count — used for span attribution in sum_session_by_agent()
    ESTIMATE_FILE="$CALIBRATION_DIR/active-estimate.json"
    if [ -f "$ESTIMATE_FILE" ]; then
        # TODO: cache the discovered JSONL path in a sibling state file (alongside
        # span-counter) to avoid repeating this find call on every hook invocation.
        JSONL_PATH=$(find "$HOME/.claude/projects/" -name "*.jsonl" -type f \
            -newer "$ESTIMATE_FILE" -print0 2>/dev/null | \
            xargs -0 ls -t 2>/dev/null | head -1 || echo "")
        if [ -n "$JSONL_PATH" ] && [ -f "$JSONL_PATH" ]; then
            JSONL_LINE_COUNT=$(wc -l < "$JSONL_PATH" 2>/dev/null || echo 0)
        else
            JSONL_LINE_COUNT=0
        fi
    else
        JSONL_LINE_COUNT=0
    fi

    # span_id: global sequence counter used for chronological ordering and FIFO
    # deduplication in _build_spans(). Each hook invocation (PreToolUse AND PostToolUse)
    # gets its own span_id — start and stop events for the same agent invocation will
    # have consecutive values (e.g., start=3, stop=4). The FIFO matching in
    # sum_session_by_agent() uses start event span_id for open_spans removal, not
    # start/stop matching by span_id value.
    # span_id counter (F2 — incrementing per-file counter for unambiguous FIFO matching)
    COUNTER_FILE="$CALIBRATION_DIR/${SESSION_ID}-span-counter"
    SPAN_ID=1
    if [ -f "$COUNTER_FILE" ]; then
        PREV=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
        SPAN_ID=$(( PREV + 1 ))
    fi
    echo "$SPAN_ID" > "$COUNTER_FILE"

    # Event type
    EVENT_TYPE="agent_start"
    [ "$HOOK_EVENT_NAME" = "PostToolUse" ] && EVENT_TYPE="agent_stop"

    # Timestamp (UTC, ISO 8601)
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.000Z")

    # Build EVENT_JSON via python3 using env vars (shlex-safe — no user strings interpolated)
    EVENT_JSON=$(TYPE_ENV="$EVENT_TYPE" TS_ENV="$TIMESTAMP" AGENT_ENV="$AGENT_NAME" \
        SID_ENV="$SESSION_ID" LC_ENV="$JSONL_LINE_COUNT" SPID_ENV="$SPAN_ID" python3 -c "
import json, os
print(json.dumps({
    'schema_version': 1,
    'type': os.environ['TYPE_ENV'],
    'timestamp': os.environ['TS_ENV'],
    'agent_name': os.environ['AGENT_ENV'],
    'session_id': os.environ['SID_ENV'],
    'jsonl_line_count': int(os.environ['LC_ENV']),
    'span_id': int(os.environ['SPID_ENV']),
    'metadata': {}
}))
" 2>/dev/null) || exit 0

    # NOTE (F1): No parent_agent field. Nesting is inferred in sum_session_by_agent() from the
    # chronological order of open spans: when an agent_start fires while another agent's span is
    # still open (no matching agent_stop yet), the open agent is treated as parent. This is purely
    # a read-time computation — no parent tracking at write time.

    # Atomic append (POSIX guarantees atomicity for appends < PIPE_BUF ~4096 bytes; each event ~300 bytes)
    echo "$EVENT_JSON" >> "$SIDECAR_FILE"

} || exit 0
