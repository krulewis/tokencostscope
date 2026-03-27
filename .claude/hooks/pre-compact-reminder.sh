#!/usr/bin/env bash
# pre-compact-reminder.sh — PreCompact hook
#
# Fires before context compaction to remind the orchestrator about pipeline
# enforcement rules, which are easy to miss after losing conversation context.
#
# Exit 0 always (PreCompact cannot block; this is advisory).

# Emergency bypass — respect SKIP_GATE even for advisory hooks
if [ "${TOKENCAST_SKIP_GATE:-}" = "1" ]; then
  exit 0
fi

cat <<'MSG'
CONTEXT COMPACTED — Pipeline enforcement reminder:
- calibration/active-estimate.json MUST exist before dispatching implementer/qa/debugger agents
- If the estimate file is missing or stale (>24h), run /tokencast on the final plan
- All multi-file work (3+ files) MUST be dispatched to implementer/debugger agents
- XS exception (inline ok): single file, <5 tool calls total
- Do NOT edit src/, tests/, or scripts/ files directly if scope is S/M/L
- If working on an M/L story, verify the full planning pipeline is complete (PP-1 through PP-7)
- Direct commits to main are blocked by branch-guard.sh — always use a feature branch
- Resume by re-reading the compaction summary, then dispatch agents as needed
MSG

exit 0
