# Installation

The Claude Code plugin (recommended) delivers everything in one command. For other IDEs, use the MCP server directly. The SKILL.md skill is available as a legacy option for Claude Code users who prefer it.

---

## Claude Code (Recommended)

Install tokencast as a Claude Code plugin — delivers the MCP server, calibration hooks, and estimation skill in one command:

```
/plugin install github.com/krulewis/tokencast --scope user
```

> **Prerequisites:** [`uv`](https://docs.astral.sh/uv/) must be installed for the MCP server to function.
> Install with: `curl -LsSf https://astral.sh/uv/install.sh | sh`

This delivers:
- **MCP server** (`estimate_cost`, `get_calibration_status`, `get_cost_history`, `report_session`, `report_step_cost`)
- **Calibration hooks** (auto-learning at session end, mid-session cost warnings, agent timeline tracking)
- **SKILL.md** (estimation algorithm auto-trigger after plans)

Calibration data is stored in `~/.tokencast/calibration/` (global across projects, preserved on uninstall).

> **Scope options:** `--scope user` (recommended — installs globally for all projects) or `--scope project` (per-project only).

---

### Other IDEs (MCP Server)

### 1. Install the package

```bash
pip install tokencast
```

Or with `uvx` (no install required — runs directly from PyPI):

```bash
uvx tokencast
```

### 2. Configure your IDE

Replace `/path/to/your/project` with your actual project path in the config snippets below.

#### Cursor

Create or update `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "tokencast": {
      "command": "tokencast-mcp",
      "args": [
        "--calibration-dir", "/path/to/your/project/calibration",
        "--project-dir", "/path/to/your/project"
      ]
    }
  }
}
```

#### VS Code + GitHub Copilot

Create or update `.vscode/mcp.json` in your project root:

```json
{
  "servers": {
    "tokencast": {
      "type": "stdio",
      "command": "tokencast-mcp",
      "args": [
        "--calibration-dir", "/path/to/your/project/calibration",
        "--project-dir", "/path/to/your/project"
      ]
    }
  }
}
```

#### Windsurf

Add to your Windsurf MCP config:

```json
{
  "mcpServers": {
    "tokencast": {
      "command": "tokencast-mcp",
      "args": [
        "--calibration-dir", "/path/to/your/project/calibration",
        "--project-dir", "/path/to/your/project"
      ]
    }
  }
}
```

Full config examples are in [`docs/ide-configs/`](https://github.com/krulewis/tokencast/tree/main/docs/ide-configs).

### 3. Use the tools

Once configured, tokencast exposes five MCP tools:

| Tool | What it does |
|------|-------------|
| `estimate_cost` | Estimate API cost for a planned task before running it |
| `get_calibration_status` | Check whether your estimates are well-calibrated |
| `get_cost_history` | Browse past estimates vs actuals |
| `report_session` | Report actual cost at session end to improve calibration |
| `report_step_cost` | Record the cost of a single pipeline step during a session |

### MCP Server Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--calibration-dir PATH` | `~/.tokencast/calibration` | Where calibration data is stored |
| `--project-dir PATH` | None | Project root for file measurement |
| `--version` | | Print version and exit |

---

### Claude Code Skill (Legacy)

If you use Claude Code and prefer the SKILL.md workflow:

```bash
# 1. Clone tokencast (anywhere — it doesn't need to live inside your project)
git clone https://github.com/krulewis/tokencast.git

# 2. Install into your project
bash tokencast/scripts/install-hooks.sh "/path/to/your-project"
```

> **Paths with spaces:** Always wrap the project path in quotes.
> Example: `bash tokencast/scripts/install-hooks.sh "/Volumes/Macintosh HD2/my-project"`

The install script does three things:

1. Symlinks the skill into `<project>/.claude/skills/tokencast/`
2. Adds a `Stop` hook for auto-learning at session end
3. Adds a `PostToolUse` hook to nudge estimation after planning agents

Every Claude Code session in that project now has tokencast active.

### Verify Installation

Start a Claude Code session in your project. Create or describe a plan. tokencast should activate automatically and output a cost table.

You can also invoke manually:

```
/tokencast
```

Or with explicit parameters:

```
/tokencast size=M files=5 complexity=medium
```

### File Layout After Install

```
your-project/
  .claude/
    skills/
      tokencast/        ← symlink to tokencast repo
    settings.json            ← Stop + PostToolUse hooks added here
```

The tokencast repo itself can live anywhere on your filesystem — the symlink keeps your project directory clean.

---

## Uninstalling

**Plugin uninstall:** Run `/plugin uninstall tokencast` in Claude Code. Calibration data in `~/.tokencast/calibration/` is preserved.

**Skill mode uninstall:**

```bash
bash /path/to/tokencast/scripts/disable.sh "/path/to/your-project"
```

Removes the skill symlink and hooks. Calibration data in `calibration/` is preserved.

For the MCP server, remove the `tokencast` entry from your IDE's MCP config file.
