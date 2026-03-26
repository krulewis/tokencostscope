# tokencostscope

A Claude Code skill that automatically estimates Anthropic API token costs when a development plan is created, and learns from actual usage over time to improve accuracy via calibration factors.

## Repo

- GitHub: `krulewis/tokencostscope`
- Current version: 2.1.0

## Key Files

| Path | Purpose |
|------|---------|
| `SKILL.md` | Skill definition — activation rules, calculation algorithm, output template |
| `references/heuristics.md` | Token budgets, pipeline step decompositions, complexity multipliers, parallel discount parameters — all tunable parameters live here |
| `references/pricing.md` | Model pricing per million tokens, cache rates, step→model mapping |
| `references/calibration-algorithm.md` | Calibration algorithm documentation |
| `references/examples.md` | Worked estimation examples |
| `scripts/tokencostscope-learn.sh` | Stop hook — reads session JSONL at end of session, computes actuals, calls update-factors.py |
| `scripts/tokencostscope-midcheck.sh` | PreToolUse hook for mid-session cost warnings — checks spend vs pessimistic estimate |
| `scripts/update-factors.py` | Computes and persists calibration factors from completed session data |
| `scripts/sum-session-tokens.py` | Parses session JSONL to sum token costs |
| `calibration/` | Calibration data directory — gitignored; contains `active-estimate.json` and `factors.json` |
| `tests/test_pr_review_loop.py` | Tests for PR Review Loop cost modeling |
| `tests/test_parallel_agent_accounting.py` | Tests for parallel agent cost discounting |
| `tests/test_file_size_awareness.py` | Tests for file size bracket computation and auto-measurement |
| `docs/wiki/` | GitHub wiki source — Home, How-It-Works, Installation, Configuration, Calibration, Roadmap |
| `README.md` | Repo root README (not inside `.claude/skills/tokencostscope/`) |

## Test Commands

```bash
# Run all tests — use system Python 3.9 which has pytest
/usr/bin/python3 -m pytest tests/

# Run a specific test file
/usr/bin/python3 -m pytest tests/test_pr_review_loop.py

# Run with verbose output
/usr/bin/python3 -m pytest tests/ -v
```

**Do NOT use `pytest` or `python3 -m pytest` directly.** Homebrew `python3` resolves to 3.14 which does NOT have pytest. Always use `/usr/bin/python3`.

## Architecture Conventions

- **All tunable parameters live in `references/heuristics.md`** — not hardcoded in SKILL.md. This includes complexity multipliers, band multipliers, parallel discount factors, cache rate floors, review cycle defaults, decay halflife, per-signature min samples, and midcheck parameters.
- **Time-decay constants:** `DECAY_HALFLIFE_DAYS = 30` in `update-factors.py` mirrors `decay_halflife_days` in `references/heuristics.md`. `DECAY_MIN_RECORDS = 5` (cold-start guard) is hardcoded in `update-factors.py` and intentionally NOT in heuristics.md — it is a statistical invariant, not user-tunable.
- **Per-signature factors:** Pass 5 of `update-factors.py` computes per-signature factors from signature-normalized step arrays. Signatures are derived at Pass 1 read time and stored as a private `_canonical_sig` field. In `factors.json`, they live under `signature_factors` and are read with `.get('signature_factors', {})` default for backward compatibility.
- **Mid-session check:** `tokencostscope-midcheck.sh` is a PreToolUse hook. It reads `active-estimate.json` and the session JSONL to compute actual spend, then writes state to `calibration/.midcheck-state` (ephemeral, gitignored). Hook is fail-silent via `set -euo pipefail` + `|| exit 0` — failures do not interrupt your work. State file format: two lines — last-checked byte size and cooldown sentinel (`0` or `COOLDOWN:<size>`).
- **Pipeline signature derivation:** Not written to `active-estimate.json`. SKILL.md Step 3e derives it inline from the `steps` array using the same normalization formula as `learn.sh` line 38.
- **Shell injection safety** — `learn.sh` and `midcheck.sh` use `shlex.quote()` and env vars pattern to pass data to Python. Never interpolate user-derived strings directly into shell commands.
- **`active-estimate.json` is the handshake** between estimation (SKILL.md writes it at estimate time) and learning (learn.sh reads it at session end). Schema changes must be backward compatible.
- **Backward compatibility** — new fields in `active-estimate.json` and `factors.json` schemas use `.get()` defaults in Python so old files don't break newer scripts.
- **File size brackets** — when file paths are extractable from the plan and files exist on disk, tokencostscope auto-measures via batched `wc -l` (cap: 30 files). Three brackets: small (≤49 lines) = 3k/1k tokens (read/edit), medium (50–500) = 10k/2.5k, large (≥501) = 20k/5k. Fixed-count file reads in all steps use the weighted-average bracket. Override: `avg_file_lines=N`. Unmeasured files fall back to override bracket or medium default.
- **`file_brackets` in active-estimate.json** — stores aggregate bracket counts (not per-file data) for future calibration stratification. Schema: `{"small": N, "medium": N, "large": N}` or null. `null` means no paths extracted (not the same as `{"small":0,"medium":0,"large":0}` which means paths extracted but none measurable).
- **Version string must be consistent** across three places: `SKILL.md` frontmatter (`version:`), output template header (`## costscope estimate (v1.x.x)`), and `learn.sh` `VERSION` variable. Always update all three together.
- **PR Review Loop calibration** applies the factor independently to each band (not re-anchored as fixed ratios of calibrated Expected) — this preserves the decay model's per-band cycle counts.
- **Step 3.5 runs post-step-loop** — the PR Review Loop row computation happens after all individual pipeline steps complete Steps 3a–3e, not inline. Cache each constituent step's pre-discount cost during the per-step loop.
- **Parallel discount does NOT apply to PR Review Loop C value** — `C` uses undiscounted step costs even when constituent steps were modeled as parallel.

## Memory / Docs Update Paths

When completing work, the `docs-updater` agent should update:
- `docs/wiki/` — whichever wiki pages cover the changed functionality
- `MEMORY.md` at `/Users/kellyl./.claude/projects/-Volumes-Macintosh-HD2-Cowork-Projects-costscope/memory/MEMORY.md`
- `ROADMAP.md` if version or milestone status changed

## Gotchas

- **Paths with spaces** — always quote shell paths; use `-print0 | xargs -0` for `find` pipelines. The repo lives at `/Volumes/Macintosh HD2/Cowork/Projects/costscope` — the space in "Macintosh HD2" will break unquoted shell commands.
- **macOS volume path** — `/Volumes/Macintosh HD2/...` is the working directory; scripts run from there will have the space in the absolute path.
- **Worktree working directory** — if using git worktrees, the working dir differs from the main repo root. Use absolute paths.
- **README.md location** — `README.md` is in the repo root (`/Volumes/Macintosh HD2/Cowork/Projects/costscope/README.md`), not inside `.claude/skills/tokencostscope/`.
- **`calibration/` is gitignored** — do not commit calibration data. The directory may not exist on a fresh clone; scripts must handle its absence gracefully.

---

<!-- Global pipeline, workflow, agent delegation, and codebase-memory rules are in ~/.claude/CLAUDE.md — loaded automatically every session. No need to duplicate here. -->
