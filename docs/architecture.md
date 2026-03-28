# Architecture Reference

## Python Package Design

- **Dict-based routing layer**: Public API functions (`estimate_cost`, `report_session`, `report_step_cost`, `get_calibration_status`, `get_cost_history`) accept and return dicts matching MCP tool schemas. MCP tools are thin wrappers that call these functions. CI/CD users import the same functions, ensuring no API drift.
- **Eager `__init__.py` with importlib bypass**: `__init__.py` uses eager imports (fine for normal package usage). Scripts like `learn.sh` and `sum-session-tokens.py` use importlib to load individual modules (`session_recorder.py`, `pricing.py`) directly, bypassing `__init__.py` to avoid pulling the full dependency tree in subprocess contexts.
- **No business logic in MCP layer**: `src/tokencast_mcp/tools/` handlers are thin wrappers — they call `api.py` functions, format results, and raise `ValueError` on errors for the server to return `CallToolResult(isError=True)`.
- **Error handling pattern**: API functions return `{"error": "...", "message": "..."}` dicts on failure. MCP handlers check `if "error" in result` and raise `ValueError`. Server catches and formats as error response.
- **Package exports requirement**: `estimate_cost` and `report_session` must be importable from `tokencast/__init__.py` to support CI/CD usage without the MCP layer (`from tokencast import estimate_cost, report_session`).

## Estimation Algorithm

- **All tunable parameters live in `references/heuristics.md`** — not hardcoded in SKILL.md. This includes complexity multipliers, band multipliers, parallel discount factors, cache rate floors, review cycle defaults, decay halflife, per-signature min samples, and midcheck parameters.
- **Mid-session check:** `tokencast-midcheck.sh` is a PreToolUse hook. It reads `active-estimate.json` and the session JSONL to compute actual spend, then writes state to `calibration/.midcheck-state` (ephemeral, gitignored). Hook is fail-silent via `set -euo pipefail` + `|| exit 0` — failures do not interrupt your work. State file format: two lines — last-checked byte size and cooldown sentinel (`0` or `COOLDOWN:<size>`).
- **Pipeline signature derivation:** Not written to `active-estimate.json`. SKILL.md Step 3e derives it inline from the `steps` array using the same normalization formula as `learn.sh` line 38.
- **`active-estimate.json` is the handshake** between estimation (SKILL.md writes it at estimate time) and learning (learn.sh reads it at session end). Schema changes must be backward compatible.
- **Backward compatibility** — new fields in `active-estimate.json` and `factors.json` schemas use `.get()` defaults in Python so old files don't break newer scripts.
- **File size brackets** — when file paths are extractable from the plan and files exist on disk, tokencast auto-measures via batched `wc -l` (cap: 30 files). Three brackets: small (≤49 lines) = 3k/1k tokens (read/edit), medium (50–500) = 10k/2.5k, large (≥501) = 20k/5k. Fixed-count file reads in all steps use the weighted-average bracket. Override: `avg_file_lines=N`. Unmeasured files fall back to override bracket or medium default.
- **`file_brackets` in active-estimate.json** — stores aggregate bracket counts (not per-file data) for future calibration stratification. Schema: `{"small": N, "medium": N, "large": N}` or null. `null` means no paths extracted (not the same as `{"small":0,"medium":0,"large":0}` which means paths extracted but none measurable).
- **PR Review Loop calibration** applies the factor independently to each band (not re-anchored as fixed ratios of calibrated Expected) — this preserves the decay model's per-band cycle counts.
- **Step 3.5 runs post-step-loop** — the PR Review Loop row computation happens after all individual pipeline steps complete Steps 3a–3e, not inline. Cache each constituent step's pre-discount cost during the per-step loop.
- **Parallel discount does NOT apply to PR Review Loop C value** — `C` uses undiscounted step costs even when constituent steps were modeled as parallel.

## Session Recording & Calibration

- **Time-decay constants:** `DECAY_HALFLIFE_DAYS = 30` in `update-factors.py` mirrors `decay_halflife_days` in `references/heuristics.md`. `DECAY_MIN_RECORDS = 5` (cold-start guard) is hardcoded in `update-factors.py` and intentionally NOT in heuristics.md — it is a statistical invariant, not user-tunable.
- **Per-signature factors:** Pass 5 of `update-factors.py` computes per-signature factors from signature-normalized step arrays. Signatures are derived at Pass 1 read time and stored as a private `_canonical_sig` field. In `factors.json`, they live under `signature_factors` and are read with `.get('signature_factors', {})` default for backward compatibility.
- **Session recorder API**: `build_history_record(estimate, actual_cost, ..., attribution=dict)` accepts source-specific data (`step_actuals_mcp`, `step_actuals_sidecar`, etc.) in the `attribution` dict. All three attribution paths (`"mcp"`, `"sidecar"`, `"proportional"`) produce records with identical schema. The `attribution_method` field in history records distinguishes them.
- **Step-cost accumulator**: `report_step_cost` persists accumulated costs to `calibration/{hash}-step-accumulator.json` (atomic rename pattern). Hash is the first 12 chars of MD5 of the `active-estimate.json` absolute path — same hash used by `agent-hook.sh`. Cleared when `report_session` completes or when a new `estimate_cost` call is made.
- **Graceful degradation**: All API functions degrade gracefully on missing/corrupted calibration data. Missing `calibration_dir` is not an error — uses defaults. Corrupted `factors.json` or `history.jsonl` is caught and handled (partial data returned, status='collecting').

## Data Modules

- **Python data modules**: `src/tokencast/pricing.py`, `src/tokencast/heuristics.py` — plain Python literals, no imports beyond stdlib, no logic, no I/O, no side effects. Markdown files (`references/pricing.md`, `references/heuristics.md`) remain the human-editable source of truth. Python modules are derived artifacts kept in sync by drift tests.
- **Cross-module band key invariant**: `set(pricing.CACHE_HIT_RATES.keys()) == set(heuristics.BAND_MULTIPLIERS.keys())` — enforced by `test_cross_module_band_keys`.
- **Pricing module signature**: `compute_cost_from_usage(usage: dict, model: str) -> float` — framework-agnostic cost function, used by `sum-session-tokens.py` (JSONL path) and `report_step_cost` (MCP path).
- **JSONL adapter**: `compute_line_cost()` in `sum-session-tokens.py` extracts usage from Claude Code JSONL format and delegates to `compute_cost_from_usage()`. This is the integration point between the JSONL parsing path (learn.sh) and the pricing module.

## MCP Layer & Attribution

- **Attribution protocol (v1)**: `docs/attribution-protocol.md` is the source of truth for the MCP attribution wire format. Version field is `attribution_protocol_version: 1`. Minor additions (new optional fields) do not require a version bump. Removing or renaming required fields does.
- **MCP tools are thin wrappers**: `src/tokencast_mcp/` exposes `estimate_cost`, `report_step_cost`, and `report_session` as MCP tools. Each delegates to the corresponding function in `src/tokencast/`. No business logic lives in the MCP layer.
- **Schema backward compatibility**: New fields in `active-estimate.json`, `factors.json`, and history records use `.get()` defaults so old files don't break.
- **Attribution protocol versioning**: v1 allows new optional fields without version bump. Only removing or renaming required fields increments the version.
- **Independent PyPI versioning**: PyPI package versions independently of SKILL.md (v2.1.0) — prevents coupling release cadences.

## File Size Awareness

- **Three brackets**: small (≤49 lines) = 3k read/1k edit tokens, medium (50–500 lines) = 10k/2.5k, large (≥501 lines) = 20k/5k.
- **N-scaling vs fixed-count**: Implementation and Test Writing use per-bracket sums. Research, Engineer, QA use weighted-average read tokens × fixed multiplier.

## Coding Conventions

- **Version string consistency**: Must be consistent across three places: `SKILL.md` frontmatter (`version:`), output template header (`## tokencast estimate (v1.x.x)`), and `learn.sh` `VERSION` variable. Always update all three together.
- **Shell injection safety**: `learn.sh` and `midcheck.sh` use `shlex.quote()` and env vars pattern to pass data to Python. Never interpolate user-derived strings directly into shell commands.
- **Hook placement**: Enforcement hooks live in `.claude/hooks/` (not `scripts/`). Core tokencast functionality remains in `scripts/`. Hook commands in `settings.json` use `git rev-parse --show-toplevel` for portable path resolution.
- **Package exports**: `estimate_cost` and `report_session` must be importable from `tokencast/__init__.py` for CI/CD usage without MCP layer.
- **GNU vs BSD xargs**: Never use `find | xargs -0 ls -t` — GNU xargs runs `ls` with no args on empty stdin. Use `find -exec ls -t {} +` instead. See PR #14 for the full root cause analysis.
- **CI portability — REPO_ROOT**: Use `Path(__file__).resolve().parent.parent.parent` consistently across all Python modules; never use relative paths. Ensures paths work in both subprocess (learn.sh) and in-process (tests) contexts.
- **CI portability — sys.executable**: Always use `sys.executable` instead of bare `python3` when spawning subprocesses from tests. Ensures the same Python version runs the subprocess.
- **CI portability — error logging**: Capture stderr from Python subprocesses and log before exiting; reduce `2>/dev/null` redirections so failures surface in CI.
