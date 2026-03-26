# Roadmap

> **North star:** Cost-aware agent orchestration — transform tokencast from a visibility tool into a cost optimization engine.

Full roadmap with all versions: [`ROADMAP.md`](https://github.com/krulewis/tokencast/blob/main/ROADMAP.md) in the repo root.

---

## Shipped

### v1.0
- Heuristic-based estimation with activity decomposition
- Context accumulation modeling (`(K+1)/2` triangular growth)
- Cache-aware pricing (read/write/input/output)
- 3-band confidence: Optimistic / Expected / Pessimistic
- Auto-trigger after plans; auto-learn at session end via Stop hook
- Calibration: median → EWMA correction factors, per-size stratification

### v1.1
- Richer input features (project type, language, pipeline signature)
- Trimmed mean for faster, more robust early calibration
- Outlier flagging (extreme actual/expected ratios excluded)

### v1.2
- **PR Review Loop modeling** — geometric-decay cost model for review-fix-re-review cycles
- `review_cycles=N` override; `review_cycles=0` suppresses the row
- Per-band calibration for PR Review Loop (independent scaling, preserves decay model)

### v1.2.1
- Generalized pipeline terminology — renamed project-specific step names
- "Default pipeline" framing for broader adoption

### v1.3
- **Parallel agent accounting** — detect parallel steps from plan text, apply two discounts:
  - `input_accum × 0.75` (no inherited context)
  - `cache_rate − 0.15` (no warmed prefix)
- Bracketed `┌│└` output table for parallel groups
- `parallel_groups` + `parallel_steps_detected` captured in calibration history

### v1.4
- Per-step correction factors — after 3+ sessions per step, `S:x` Cal column indicators activate
- Step-level cost tracking in calibration history
- 4-level precedence chain (per-step → size-class → global)

### v1.5
- **File size awareness** — auto-measure file line counts, three brackets (small/medium/large)
- Cache write modeling in price formula (three-term input cost)
- `avg_file_lines=` override for greenfield projects
- `file_brackets` field in calibration history

### v1.6
- **Time-decay calibration** — records older than 30 days have reduced influence (never deleted)
- Cold-start guard: decay only applies with 5+ records per stratum
- **Per-signature correction factors** — after 3+ runs of the same pipeline signature, `P:x` Cal column activates
- 5-level precedence chain (per-step → per-signature → size-class → global)
- **Mid-session cost tracking** — PreToolUse hook warns when spend approaches 80% of pessimistic estimate
- Sampling gate (~50KB) and cooldown (~200KB) to avoid verbosity

### v1.7
- **Per-agent step cost attribution** — agent-hook.sh sidecar writes timeline JSONL during session
- Per-step actuals computed via FIFO span matching; added to history records
- Storage abstraction (`calibration_store.py`) for extensible calibration I/O
- `sum_session_tokens.py` enhanced with per-step attribution logic

### v2.0 — Observability
- **Calibration health dashboard** — `/tokencast status` command
- Five sections: health, accuracy, cost attribution, outliers, recommendations
- Window modes: 30-day, 10-session, all-time, adaptive auto-select
- Per-step cost drivers and outlier flagging for manual review
- JSON output mode (`--json`, `--verbose`)

### v2.1 (shipped 2026-03-25)
- **`baseline_cost` in `last-estimate.md`** — new footer line so step 10 can compute accurate actual-vs-estimate delta after compaction
- **Continuation session calibration gap** — `parse_last_estimate.py` reconstitutes a minimal estimate from `last-estimate.md` when `active-estimate.json` is absent; captures continuation session actuals

---

## Planned

### v3.0 — Cross-Project Intelligence
- Global calibration layer (fall back when project data is sparse)
- Workflow fingerprinting (auto-detect your pipeline shape)
- Session comparison & trend dashboard

### v4.0 — Predictive & Budget Controls
- Pre-flight budget gate (configurable cost ceiling)
- Model substitution suggestions (post-session Opus→Sonnet recommendations)
- Anomaly detection

### v5.0 — Cost-Aware Orchestration
- Budget-constrained planning
- Dynamic model selection at runtime
- Mid-pipeline reallocation (if early steps overrun, later steps get downgraded)
