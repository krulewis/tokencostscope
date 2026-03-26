# Installation

## Prerequisites

- Claude Code CLI installed and configured
- A project you want to instrument

---

## One-Time Setup

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

---

## Verify Installation

Start a Claude Code session in your project. Create or describe a plan. tokencast should activate automatically and output a cost table.

You can also invoke manually:

```
/tokencast
```

Or with explicit parameters:

```
/tokencast size=M files=5 complexity=medium
```

---

## Uninstalling

```bash
bash /path/to/tokencast/scripts/disable.sh "/path/to/your-project"
```

Removes the skill symlink and hooks. Calibration data in `calibration/` is preserved.

---

## File Layout After Install

```
your-project/
  .claude/
    skills/
      tokencast/        ← symlink to tokencast repo
    settings.json            ← Stop + PostToolUse hooks added here
```

The tokencast repo itself can live anywhere on your filesystem — the symlink keeps your project directory clean.
