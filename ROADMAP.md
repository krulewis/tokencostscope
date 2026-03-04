# tokencostscope — Roadmap

> **North star:** Cost-aware agent orchestration — transform tokencostscope from a visibility tool into a cost optimization engine.
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

## v1.1 — Accuracy & Calibration Refinement

**Goal:** Make estimates trustworthy enough that users (and later, automation) can act on them.

- [ ] **PR review loop modeling** — add `review_cycles` parameter (default 2), track actual cycle counts, learn the multiplier per project
- [ ] **Per-step correction factors** — tag sessions with pipeline step name, learn per-step accuracy (Research overestimated? Staff Review underestimated?)
- [ ] **File size awareness** — read actual file sizes from the plan's file list, adjust token budgets (small files < 50 lines get 3k, large files > 500 lines get 20k+)
- [ ] **Parallel agent accounting** — when steps run as parallel subagents, model overlapping context differently than sequential
- [ ] **Cache write modeling in estimates** — first turn pays cache_write price, subsequent turns pay cache_read; currently estimates only model cache reads
- [ ] **Richer input features** — project type tagging (greenfield, refactor, bug fix, migration, docs), language/framework tag, agent pipeline signature, repo size context
- [ ] **Trimmed mean for early calibration** — faster, more robust convergence with limited data
- [ ] **Decay on stale data** — down-weight sessions older than 30 days more aggressively
- [ ] **Outlier flagging** — exclude extreme actual/expected ratios from calibration, log a note
- [ ] **Per-pipeline-signature calibration** — calibrate by agent sequence, not just size class

---

## v1.2 — Observability & Mid-Session Awareness

**Goal:** See what's happening *during* a session, not just before and after. Prerequisite for mid-pipeline reallocation in v4.0.

- [ ] **Mid-session cost tracking** *(email #4)* — periodic check-ins that read partial JSONL mid-session and warn if tracking toward the pessimistic band
- [ ] **Per-agent step actuals breakdown** *(email #5)* — capture actuals per agent step (not just session-level), enabling per-agent calibration and identifying the biggest cost drivers
- [ ] **Cache efficiency score** *(email #7)* — track cache hit rate per session and over time, with tips when efficiency drops
- [ ] **Cost annotations in responses** *(email #8)* — surface cost info inline after each major agent step: `[tokencostscope: Research Agent — 18,400 tokens, $0.92, 94% cache]`
- [ ] **`/tokencostscope status`** — show calibration health: sample count, factor stability, band accuracy (% of actuals within each band)
- [ ] **Estimate diff** — when a plan changes mid-session, show delta from previous estimate
- [ ] **Quiet mode** — option to log estimates without rendering the table

---

## v2.0 — Cross-Project Intelligence & Reporting

**Goal:** Learn across projects and surface trends. Provides the data density needed for model substitution recommendations.

- [ ] **Global calibration layer** — learn factors across all installed projects, fall back to global when project-local data is sparse
- [ ] **Workflow fingerprinting** — detect the user's actual pipeline shape (skip QA? 2 review rounds?) and auto-adjust step decomposition
- [ ] **Session comparison & trend dashboard** *(email #2)* — `/tokencostscope report` generated from history.jsonl showing estimate accuracy over time, cost trends, monthly spend
- [ ] **Multi-project rollup** *(email #9)* — global summary across all instrumented projects: total monthly spend, biggest consumer, cross-project calibration sharing
- [ ] **Multi-session task support** — link multiple sessions to one task via a task ID, aggregate actuals across sessions
- [ ] **Model price auto-update** — check Anthropic pricing page on install or periodically, update references/pricing.md automatically
- [ ] **Export/import calibration** — share learned factors between machines or team members

---

## v3.0 — Predictive & Budget Controls

**Goal:** Move from descriptive to prescriptive. Budget gates and model substitution suggestions are the manual precursors to automated orchestration.

- [ ] **Pre-flight budget gate** *(email #3)* — configurable cost ceiling that pauses and prompts before proceeding; useful for expensive Opus-heavy pipelines or unattended runs
- [ ] **Model substitution suggestions** *(email #6)* — post-session: if a step ran well under its Opus budget, recommend Sonnet next time; flag Sonnet steps that hit limits as Opus candidates. *Human acceptance rates here train the v4.0 policy.*
- [ ] **Task complexity auto-classification** — infer complexity from plan content rather than requiring explicit low/medium/high
- [ ] **Anomaly detection** — flag sessions where actual/expected ratio is >3x or <0.2x as potential data quality issues (exclude from calibration)
- [ ] **ML-based estimation** — train a lightweight model on accumulated history.jsonl data (features: file count, complexity, step count, codebase size → predicted cost)
- [ ] **Export & integration** *(email #10)* — CSV/JSON export, webhook support (Slack, Discord, custom), GitHub Actions integration for PR workflows

---

## v4.0 — Cost-Aware Agent Orchestration

**Goal:** tokencostscope becomes an active participant in pipeline construction and execution. The difference between a fuel gauge and cruise control.

**Prerequisites:** v1.2 (per-agent actuals, mid-session tracking), v3.0 (budget gates, model substitution suggestions with acceptance data).

### Core capabilities

- [ ] **Budget-constrained planning** — orchestrator receives a budget and proposed pipeline, rearranges or trims to fit: collapse agents, skip non-critical steps, adjust scope
- [ ] **Dynamic model selection** — models picked at runtime based on remaining budget and per-step complexity from calibration history, rather than hardcoded assignments
- [ ] **Mid-pipeline reallocation** — if an early step overruns, later steps get downgraded: swap Opus → Sonnet, shorten QA, skip non-critical steps to stay within budget
- [ ] **Confidence-gated escalation** — if a Sonnet step returns low-confidence output, the orchestrator decides whether remaining budget justifies an Opus retry; if not, flags the output rather than silently proceeding

### Sequencing strategy

1. Ship model substitution suggestions (v3.0) and observe which substitutions developers actually accept
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

*Last updated: 2026-03-04*
