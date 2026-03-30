"""Static structural tests for the plugin directory layout."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_FILES = [
    # Marketplace manifest
    ".claude-plugin/marketplace.json",
    # Plugin manifest (self-describing plugin)
    "plugin/.claude-plugin/plugin.json",
    # MCP config
    "plugin/.mcp.json",
    # Hooks
    "plugin/hooks/hooks.json",
    "plugin/hooks/tokencast-learn.sh",
    "plugin/hooks/tokencast-midcheck.sh",
    "plugin/hooks/tokencast-agent-hook.sh",
    # Skills
    "plugin/skills/tokencast/SKILL.md",
    "plugin/skills/tokencast/references/heuristics.md",
    "plugin/skills/tokencast/references/pricing.md",
    "plugin/skills/tokencast/references/examples.md",
    "plugin/skills/tokencast/references/calibration-algorithm.md",
    # Scripts
    "plugin/scripts/sum-session-tokens.py",
    "plugin/scripts/pricing.py",
    "plugin/scripts/update-factors.py",
    "plugin/scripts/calibration_store.py",
    "plugin/scripts/parse_last_estimate.py",
    "plugin/scripts/session_recorder.py",
    "plugin/scripts/tokencast-status.py",
]


def test_plugin_json_exists_and_valid():
    path = REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json"
    assert path.exists(), "plugin.json not found"
    data = json.loads(path.read_text())
    for field in ("name", "version", "description", "author"):
        assert field in data and data[field], f"plugin.json missing or empty field: {field}"
    assert data["version"] == "2.1.0", f"Expected version 2.1.0, got {data['version']}"


def test_mcp_json_exists_and_valid():
    path = REPO_ROOT / "plugin" / ".mcp.json"
    assert path.exists(), ".mcp.json not found"
    data = json.loads(path.read_text())
    tc = data["mcpServers"]["tokencast"]
    assert tc["command"] == "uvx"
    assert tc["args"] == ["tokencast"]


def test_hooks_json_exists_and_valid():
    path = REPO_ROOT / "plugin" / "hooks" / "hooks.json"
    assert path.exists(), "hooks.json not found"
    data = json.loads(path.read_text())
    hooks = data["hooks"]
    assert "Stop" in hooks and len(hooks["Stop"]) >= 1
    assert "PreToolUse" in hooks and len(hooks["PreToolUse"]) >= 2
    assert "PostToolUse" in hooks and len(hooks["PostToolUse"]) >= 1


def test_hooks_json_commands_use_plugin_root():
    path = REPO_ROOT / "plugin" / "hooks" / "hooks.json"
    data = json.loads(path.read_text())

    def _collect_commands(obj):
        if isinstance(obj, dict):
            if "command" in obj:
                yield obj["command"]
            for v in obj.values():
                yield from _collect_commands(v)
        elif isinstance(obj, list):
            for item in obj:
                yield from _collect_commands(item)

    for cmd in _collect_commands(data):
        assert "${CLAUDE_PLUGIN_ROOT}" in cmd, f"Command missing ${{CLAUDE_PLUGIN_ROOT}}: {cmd}"


def test_all_plugin_files_exist():
    for rel_path in EXPECTED_FILES:
        full = REPO_ROOT / rel_path
        assert full.exists(), f"Missing plugin file: {rel_path}"


def test_session_recorder_no_drift():
    src = REPO_ROOT / "src" / "tokencast" / "session_recorder.py"
    plugin = REPO_ROOT / "plugin" / "scripts" / "session_recorder.py"
    assert src.exists() and plugin.exists()
    assert src.read_text() == plugin.read_text(), "session_recorder.py has drifted from src/tokencast/session_recorder.py"


def test_pricing_py_no_drift():
    src = REPO_ROOT / "src" / "tokencast" / "pricing.py"
    plugin = REPO_ROOT / "plugin" / "scripts" / "pricing.py"
    assert src.exists() and plugin.exists()
    assert src.read_text() == plugin.read_text(), "pricing.py has drifted from src/tokencast/pricing.py"


def test_marketplace_json_exists_and_valid():
    path = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    assert path.exists(), "marketplace.json not found"
    data = json.loads(path.read_text())
    assert "name" in data, "marketplace.json missing 'name' field"
    assert "plugins" in data, "marketplace.json missing 'plugins' field"
    assert len(data["plugins"]) >= 1, "marketplace.json has no plugins"
    plugin_entry = data["plugins"][0]
    assert plugin_entry.get("source") == "./plugin", (
        f"Expected source './plugin', got {plugin_entry.get('source')!r}"
    )
