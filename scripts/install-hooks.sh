#!/usr/bin/env bash
# install-hooks.sh — One-time setup for tokencostscope
#
# Installs the skill into a project's .claude/skills/ directory and
# merges the required hooks into the project's .claude/settings.json.
#
# Usage:
#   bash install-hooks.sh <project_root>
#   bash install-hooks.sh .                # current directory
#
# After running, tokencostscope will:
# - Auto-estimate after plans are created (via skill auto-trigger)
# - Auto-learn at session end (via Stop hook)
# - Nudge Claude after Agent tool returns a plan (via PostToolUse hook)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_SOURCE="$(dirname "$SCRIPT_DIR")"

# Require project root as argument
if [ $# -lt 1 ]; then
    echo "Usage: install-hooks.sh <project_root>"
    echo "  Example: bash install-hooks.sh /path/to/my-project"
    echo "  Example: bash install-hooks.sh ."
    exit 1
fi

PROJECT_ROOT="$(cd "$1" && pwd)"
CLAUDE_DIR="$PROJECT_ROOT/.claude"
SKILLS_DIR="$CLAUDE_DIR/skills"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"

echo "Installing tokencostscope into: $PROJECT_ROOT"

# 1. Create .claude/skills/tokencostscope/ and symlink the skill
mkdir -p "$SKILLS_DIR"
SKILL_DEST="$SKILLS_DIR/tokencostscope"

if [ -e "$SKILL_DEST" ]; then
    echo "  Skill directory already exists at $SKILL_DEST"
    echo "  Remove it first if you want to reinstall: rm -rf $SKILL_DEST"
    exit 1
fi

ln -s "$SKILL_SOURCE" "$SKILL_DEST"
echo "  Symlinked skill: $SKILL_DEST -> $SKILL_SOURCE"

# 2. Ensure calibration directory exists
mkdir -p "$SKILL_SOURCE/calibration"

# 3. Merge hooks into project settings.json
mkdir -p "$CLAUDE_DIR"

if [ ! -f "$SETTINGS_FILE" ]; then
    echo '{}' > "$SETTINGS_FILE"
fi

LEARN_SCRIPT="$SKILL_SOURCE/scripts/tokencostscope-learn.sh"
TRACK_SCRIPT="$SKILL_SOURCE/scripts/tokencostscope-track.sh"

# Use python3 for reliable JSON merging (jq may not be installed)
python3 - "$SETTINGS_FILE" "$LEARN_SCRIPT" "$TRACK_SCRIPT" <<'PYEOF'
import json
import sys

settings_path = sys.argv[1]
learn_script = sys.argv[2]
track_script = sys.argv[3]

with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.setdefault("hooks", {})

# Add Stop hook for auto-learning
stop_hooks = hooks.setdefault("Stop", [])
learn_entry = {"hooks": [{"type": "command", "command": learn_script}]}
# Check if already installed
if not any(learn_script in json.dumps(h) for h in stop_hooks):
    stop_hooks.append(learn_entry)

# Add PostToolUse hook for plan detection
post_hooks = hooks.setdefault("PostToolUse", [])
track_entry = {"matcher": "Agent", "hooks": [{"type": "command", "command": track_script}]}
if not any(track_script in json.dumps(h) for h in post_hooks):
    post_hooks.append(track_entry)

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print(f"  Hooks merged into {settings_path}")
PYEOF

# 4. Make scripts executable
chmod +x "$LEARN_SCRIPT" "$TRACK_SCRIPT"

echo ""
echo "tokencostscope installed successfully."
echo ""
echo "What happens now:"
echo "  - Every session in this project will auto-estimate plans"
echo "  - Actual costs are captured automatically at session end"
echo "  - Estimates improve over time as calibration data accumulates"
echo ""
echo "To disable: bash $SKILL_SOURCE/scripts/disable.sh $PROJECT_ROOT"
