# Installation

## Prerequisites

- Claude Code CLI installed and configured
- A project you want to instrument

---

## One-Time Setup

```bash
# 1. Clone tokencostscope (anywhere — it doesn't need to live inside your project)
git clone https://github.com/krulewis/tokencostscope.git

# 2. Install into your project
bash tokencostscope/scripts/install-hooks.sh "/path/to/your-project"
```

> **Paths with spaces:** Always wrap the project path in quotes.
> Example: `bash tokencostscope/scripts/install-hooks.sh "/Volumes/Macintosh HD2/my-project"`

The install script does three things:

1. Symlinks the skill into `<project>/.claude/skills/tokencostscope/`
2. Adds a `Stop` hook for auto-learning at session end
3. Adds a `PostToolUse` hook to nudge estimation after planning agents

Every Claude Code session in that project now has tokencostscope active.

---

## Verify Installation

Start a Claude Code session in your project. Create or describe a plan. tokencostscope should activate automatically and output a cost table.

You can also invoke manually:

```
/tokencostscope
```

Or with explicit parameters:

```
/tokencostscope size=M files=5 complexity=medium
```

---

## Uninstalling

```bash
bash /path/to/tokencostscope/scripts/disable.sh "/path/to/your-project"
```

Removes the skill symlink and hooks. Calibration data in `calibration/` is preserved.

---

## File Layout After Install

```
your-project/
  .claude/
    skills/
      tokencostscope/        ← symlink to tokencostscope repo
    settings.json            ← Stop + PostToolUse hooks added here
```

The tokencostscope repo itself can live anywhere on your filesystem — the symlink keeps your project directory clean.
