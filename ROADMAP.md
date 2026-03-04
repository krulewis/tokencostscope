# tokencostscope — Roadmap

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

- [ ] **PR review loop modeling** — add `review_cycles` parameter (default 2), track actual cycle counts, learn the multiplier per project
- [ ] **Per-step correction factors** — tag sessions with pipeline step name, learn per-step accuracy (Research overestimated? Staff Review underestimated?)
- [ ] **File size awareness** — read actual file sizes from the plan's file list, adjust token budgets (small files < 50 lines get 3k, large files > 500 lines get 20k+)
- [ ] **Parallel agent accounting** — when steps run as parallel subagents, model overlapping context differently than sequential
- [ ] **Cache write modeling in estimates** — first turn pays cache_write price, subsequent turns pay cache_read; currently estimates only model cache reads

## v1.2 — User Experience

- [ ] **Running cost tracker** — mid-session comparison of actual spend vs estimate, warn if exceeding pessimistic band
- [ ] **`/tokencostscope status`** — show calibration health: sample count, factor stability, band accuracy (% of actuals within each band)
- [ ] **Estimate diff** — when a plan changes mid-session, show delta from previous estimate
- [ ] **Multi-session task support** — link multiple sessions to one task via a task ID, aggregate actuals across sessions
- [ ] **Quiet mode** — option to log estimates without rendering the table (for users who want learning without output noise)

## v2.0 — Cross-Project Intelligence

- [ ] **Global calibration layer** — learn factors across all installed projects, fall back to global when project-local data is sparse
- [ ] **Workflow fingerprinting** — detect the user's actual pipeline shape (maybe they skip QA, maybe they do 2 review rounds) and auto-adjust step decomposition
- [ ] **Model price auto-update** — check Anthropic pricing page on install or periodically, update references/pricing.md automatically
- [ ] **Export/import calibration** — share learned factors between machines or team members
- [ ] **Dashboard** — simple HTML report of estimate accuracy over time, cost trends, calibration drift

## v3.0 — Predictive

- [ ] **ML-based estimation** — train a lightweight model on accumulated history.jsonl data (features: file count, complexity, step count, codebase size → predicted cost)
- [ ] **Task complexity auto-classification** — infer complexity from the plan content rather than requiring explicit low/medium/high
- [ ] **Budget gates** — set a cost ceiling per task; if the estimate exceeds it, warn before proceeding
- [ ] **Anomaly detection** — flag sessions where actual/expected ratio is >3x or <0.2x as potential data quality issues (exclude from calibration)

## Future / Ideas

- [ ] **MCP server mode** — expose estimation as an MCP tool for use in other agent frameworks
- [ ] **Team calibration** — aggregate anonymized calibration data across a team to build better baseline heuristics
- [ ] **Provider-agnostic** — support OpenAI, Google, and other model pricing for non-Claude workflows
- [ ] **Cost attribution** — break down actual cost by what the tokens were spent on (tool calls, thinking, output, system prompt)
- [ ] **Integration with ccusage** — pull actual cost data from ccusage instead of raw JSONL parsing for more reliable numbers

---

*Last updated: 2026-03-03*
