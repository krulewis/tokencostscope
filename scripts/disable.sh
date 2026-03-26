#!/usr/bin/env bash
# disable.sh — Remove tokencast from a project
#
# Usage: bash disable.sh <project_root>
#
# Removes the skill symlink and hooks. Preserves calibration data
# in the source directory so it can be reused if reinstalled.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_SOURCE="$(dirname "$SCRIPT_DIR")"

if [ $# -lt 1 ]; then
    echo "Usage: disable.sh <project_root>"
    exit 1
fi

PROJECT_ROOT="$(cd "$1" && pwd)"
CLAUDE_DIR="$PROJECT_ROOT/.claude"
SKILL_DEST="$CLAUDE_DIR/skills/tokencast"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"

echo "Disabling tokencast in: $PROJECT_ROOT"

# 1. Remove skill symlink
if [ -e "$SKILL_DEST" ]; then
    rm -rf "$SKILL_DEST"
    echo "  Removed skill: $SKILL_DEST"
else
    echo "  Skill not found at $SKILL_DEST (already removed?)"
fi

# 2. Remove hooks from settings.json
if [ -f "$SETTINGS_FILE" ]; then
    LEARN_SCRIPT="$SKILL_SOURCE/scripts/tokencast-learn.sh"
    TRACK_SCRIPT="$SKILL_SOURCE/scripts/tokencast-track.sh"
    MIDCHECK_SCRIPT="$SKILL_SOURCE/scripts/tokencast-midcheck.sh"

    python3 - "$SETTINGS_FILE" "$LEARN_SCRIPT" "$TRACK_SCRIPT" "$MIDCHECK_SCRIPT" <<'PYEOF'
import json
import sys

settings_path = sys.argv[1]
learn_script = sys.argv[2]
track_script = sys.argv[3]
midcheck_script = sys.argv[4]

with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.get("hooks", {})

# Remove Stop hook entries containing our script
if "Stop" in hooks:
    hooks["Stop"] = [h for h in hooks["Stop"] if learn_script not in json.dumps(h)]
    if not hooks["Stop"]:
        del hooks["Stop"]

# Remove PostToolUse hook entries containing our script
if "PostToolUse" in hooks:
    hooks["PostToolUse"] = [h for h in hooks["PostToolUse"] if track_script not in json.dumps(h)]
    if not hooks["PostToolUse"]:
        del hooks["PostToolUse"]

# Remove PreToolUse hook entries containing our script
if "PreToolUse" in hooks:
    hooks["PreToolUse"] = [h for h in hooks["PreToolUse"] if midcheck_script not in json.dumps(h)]
    if not hooks["PreToolUse"]:
        del hooks["PreToolUse"]

if not hooks:
    settings.pop("hooks", None)

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print(f"  Hooks removed from {settings_path}")
PYEOF
fi

echo ""
echo "tokencast disabled."
echo "  Calibration data preserved in: $SKILL_SOURCE/calibration/"
echo "  To reinstall: bash $SKILL_SOURCE/scripts/install-hooks.sh $PROJECT_ROOT"
