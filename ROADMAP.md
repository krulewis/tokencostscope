# tokencast — Roadmap

> **North star:** Cost-aware agent orchestration — transform tokencast from a visibility tool into a cost optimization engine.
>
> Every milestone below builds toward that goal. Earlier versions produce the data, calibration, and trust needed to make automated cost decisions reliable.

---

## v1.0 (shipped 2026-03-03)

- [x] Heuristic-based estimation with activity decomposition
- [x] Context accumulation modeling ((K+1)/2 triangular growth)
- [x] Cache-aware pricing (read/write/input/output)
- [x] 3-band confidence: Optimistic / Expected / Pessimistic
- [x] Auto-trigger after plans via `disable-model-invocation: false`
- [x] Auto-learn at session end via Stop hook + JSONL parsing
- [x] Per-project install, persists across sessions
- [x] Calibration: median → EWMA correction factors, per-size stratification
- [x] Worked examples with verified arithmetic

---

## v1.1 — Accuracy & Calibration Refinement (shipped 2026-03-03)

**Goal:** Make estimates trustworthy enough that users (and later, automation) can act on them.

- [x] **Richer input features** — project type tagging (greenfield, refactor, bug fix, migration, docs), language/framework tag, agent pipeline signature, repo size context
- [x] **Trimmed mean for early calibration** — faster, more robust convergence with limited data
- [x] **Outlier flagging** — exclude extreme actual/expected ratios from calibration, log a note

---

## v1.2 — PR Review Loop Modeling (shipped 2026-03-04)

**Goal:** Model the iterative review-fix-re-review cycle that dominates cost in quality-gated workflows.

- [x] **PR review loop modeling** — geometric-decay cost model for review-fix-re-review cycles
- [x] **`review_cycles` override** — set expected cycle count (0 = disable)
- [x] **Per-band calibration** — PR Review Loop applies calibration independently per band (not re-anchored)
- [x] **Generalized pipeline terminology** — renamed project-specific step names, added "default pipeline" framing for broader adoption (v1.2.1)

---

## v1.3 — Accuracy & Calibration Refinement (continued)

**Goal:** Continue improving estimate accuracy with finer-grained data and modeling.

- [x] **Per-step correction factors** — tag sessions with pipeline step name, learn per-step accuracy (Research overestimated? Staff Review underestimated?) (shipped as v1.4.0)
- [x] **Parallel agent accounting** — when steps run as parallel subagents, model overlapping context differently than sequential (shipped as v1.3.0)
- [x] **Cache write modeling in estimates** — first turn pays cache_write price, subsequent turns pay cache_read; currently estimates only model cache reads (shipped as v1.3.1)

---

## v1.4 — Per-Step Calibration (shipped 2026-03-20)

**Goal:** Distinguish between overestimated and underestimated pipeline steps.

- [x] **Per-step correction factors** — Distinguish Research vs. Implementation vs. QA costs (each step learns its own factor after 3+ samples)
- [x] **Step-level cost tracking** — `step_costs` field in calibration history enables per-step accuracy analysis
- [x] **5-level precedence chain** — per-step factors take priority over size-class and global factors

---

## v1.5 — File Size Awareness (shipped 2026-03-20)

**Goal:** Auto-measure file sizes and adjust token budgets accordingly.

- [x] **File size awareness** — read actual file sizes from the plan's file list via `wc -l`, three brackets (small/medium/large)
- [x] **Three-bracket model** — small (≤49 lines) = 3k, medium (50–500) = 10k, large (≥501) = 20k tokens/read
- [x] **Override support** — `avg_file_lines=N` for greenfield projects with unmeasured files
- [x] **`file_brackets` history tracking** — calibration history captures bracket distribution per estimate

---

## v1.6 — Time-Decay & Per-Signature Calibration (shipped 2026-03-21)

**Goal:** Respond to recent session patterns and calibrate by workflow signature, not just size class.

- [x] **Exponential time-decay weighting** — records older than 30 days have reduced influence (50% at 30 days, 25% at 60 days). Never deletes records.
- [x] **Cold-start guard** — decay only applies when 5+ records exist in a calibration stratum (statistical invariant)
- [x] **Per-signature correction factors** — after 3+ runs of the same pipeline signature, a `P:x` Cal column factor activates
- [x] **Per-signature Pass 5** — dedicated calibration phase for signature-based factors in `update-factors.py`
- [x] **Mid-session cost tracking** — PreToolUse hook `tokencast-midcheck.sh` warns when spend approaches 80% of pessimistic estimate
- [x] **Sampling & cooldown** — ~50KB sampling gate and ~200KB cooldown to avoid warning spam

---

## v1.7 — Per-Agent Step Actuals (planned)

**Goal:** Break down actual costs by agent step, not just session-level summary.

**Blocker:** Requires JSONL-level step tagging by agent frameworks. Deferred pending framework support.

- [ ] **Per-agent step actuals breakdown** *(Item D from v1.6)* — capture actuals per agent step (not just session-level), enabling per-agent calibration and identifying the biggest cost drivers

---

## v2.0 — Observability & Mid-Session Awareness

**Goal:** See what's happening *during* a session, not just before and after. Prerequisite for mid-pipeline reallocation in v5.0.

- [x] **Mid-session cost tracking** — warn if trending toward the pessimistic band (shipped in v1.6)
- [ ] **Per-agent step actuals breakdown** — capture actuals per agent step (blocked on framework support, see v1.7)
- [ ] **Cache efficiency score** *(email #7)* — track cache hit rate per session and over time, with tips when efficiency drops
- [ ] **Cost annotations in responses** *(email #8)* — surface cost info inline after each major agent step: `[tokencast: Research Agent — 18,400 tokens, $0.92, 94% cache]`
- [ ] **`/tokencast status`** — show calibration health: sample count, factor stability, band accuracy (% of actuals within each band)
- [ ] **Estimate diff** — when a plan changes mid-session, show delta from previous estimate
- [ ] **Quiet mode** — option to log estimates without rendering the table
- [ ] **Project-level heuristics overrides** — allow `calibration/heuristics-overrides.json` (or similar) to shadow specific values from `references/heuristics.md` without modifying the shared file. Primary use case: `review_cycles_default` varies by project (this project averages 4–5; the shared default of 2 is too low). Would replace the manual `review_cycles=4` override documented in `CLAUDE.md`.

---

## v2.1 — Compaction & Continuation Session Fixes (shipped 2026-03-25)

**Goal:** Close two calibration gaps that surface when sessions compact or continue across multiple sessions.

- [x] **`baseline_cost` in `last-estimate.md`** — add `baseline_cost` to the compaction-safe summary so step 10 can compute an accurate actual-vs-estimate delta even after compaction or in a continuation session. Currently `last-estimate.md` omits it, inflating the reported ratio.
- [x] **Continuation session calibration gap** — when session A ends and `learn.sh` consumes `active-estimate.json`, session B (continuation) has no estimate to calibrate against and its work goes untracked. Fix: if `active-estimate.json` is absent but `last-estimate.md` is recent (< 48h), reconstitute a minimal estimate so `learn.sh` can capture session B's actuals.

---

## v3.0 — Cross-Project Intelligence & Reporting

**Goal:** Learn across projects and surface trends. Provides the data density needed for model substitution recommendations.

- [ ] **Global calibration layer** — learn factors across all installed projects, fall back to global when project-local data is sparse
- [ ] **Workflow fingerprinting** — detect the user's actual pipeline shape (skip QA? 2 review rounds?) and auto-adjust step decomposition
- [ ] **Session comparison & trend dashboard** *(email #2)* — `/tokencast report` generated from history.jsonl showing estimate accuracy over time, cost trends, monthly spend
- [ ] **Multi-project rollup** *(email #9)* — global summary across all instrumented projects: total monthly spend, biggest consumer, cross-project calibration sharing
- [ ] **Multi-session task support** — link multiple sessions to one task via a task ID, aggregate actuals across sessions
- [ ] **Model price auto-update** — check Anthropic pricing page on install or periodically, update references/pricing.md automatically
- [ ] **Export/import calibration** — share learned factors between machines or team members

---

## v4.0 — Predictive & Budget Controls

**Goal:** Move from descriptive to prescriptive. Budget gates and model substitution suggestions are the manual precursors to automated orchestration.

- [ ] **Pre-flight budget gate** *(email #3)* — configurable cost ceiling that pauses and prompts before proceeding; useful for expensive Opus-heavy pipelines or unattended runs
- [ ] **Model substitution suggestions** *(email #6)* — post-session: if a step ran well under its Opus budget, recommend Sonnet next time; flag Sonnet steps that hit limits as Opus candidates. *Human acceptance rates here train the v5.0 policy.*
- [ ] **Task complexity auto-classification** — infer complexity from plan content rather than requiring explicit low/medium/high
- [ ] **Anomaly detection** — flag sessions where actual/expected ratio is >3x or <0.2x as potential data quality issues (exclude from calibration)
- [ ] **ML-based estimation** — train a lightweight model on accumulated history.jsonl data (features: file count, complexity, step count, codebase size → predicted cost)
- [ ] **Export & integration** *(email #10)* — CSV/JSON export, webhook support (Slack, Discord, custom), GitHub Actions integration for PR workflows

---

## v5.0 — Cost-Aware Agent Orchestration

**Goal:** tokencast becomes an active participant in pipeline construction and execution. The difference between a fuel gauge and cruise control.

**Prerequisites:** v2.0 (per-agent actuals, mid-session tracking), v4.0 (budget gates, model substitution suggestions with acceptance data).

### Core capabilities

- [ ] **Budget-constrained planning** — orchestrator receives a budget and proposed pipeline, rearranges or trims to fit: collapse agents, skip non-critical steps, adjust scope
- [ ] **Dynamic model selection** — models picked at runtime based on remaining budget and per-step complexity from calibration history, rather than hardcoded assignments
- [ ] **Mid-pipeline reallocation** — if an early step overruns, later steps get downgraded: swap Opus → Sonnet, shorten QA, skip non-critical steps to stay within budget
- [ ] **Confidence-gated escalation** — if a Sonnet step returns low-confidence output, the orchestrator decides whether remaining budget justifies an Opus retry; if not, flags the output rather than silently proceeding

### Sequencing strategy

1. Ship model substitution suggestions (v4.0) and observe which substitutions developers actually accept
2. Use acceptance patterns to train the automated substitution policy
3. Start with budget-constrained planning (advisory mode — suggest changes, human approves)
4. Graduate to automatic reallocation once advisory mode achieves >80% acceptance rate

### Open challenges

- Integration with Claude Code's agent planner (not a public API surface today)
- Policy layer for reallocation decisions — rules-based first, ML later
- Calibrating developer trust in automated substitutions
- Defining fallback behavior when orchestration is overridden

---

## Future / Ideas

- [ ] **MCP server mode** — expose estimation as an MCP tool for use in other agent frameworks
- [ ] **Team calibration** — aggregate anonymized calibration data across a team to build better baseline heuristics
- [ ] **Provider-agnostic** — support OpenAI, Google, and other model pricing for non-Claude workflows
- [ ] **Cost attribution** — break down actual cost by what the tokens were spent on (tool calls, thinking, output, system prompt)
- [ ] **Integration with ccusage** — pull actual cost data from ccusage instead of raw JSONL parsing for more reliable numbers
- [ ] **Interactive estimate editor** — adjust assumptions pre-run and see costs update live
- [ ] **Team mode** — shared calibration factors so new members benefit from accumulated team history immediately

---

*Last updated: 2026-03-25*
