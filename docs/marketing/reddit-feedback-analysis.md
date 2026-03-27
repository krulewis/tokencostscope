# Reddit Feedback Analysis: Pre-Flight Cost Estimation Accuracy

*Analysis date: 2026-03-26*
*Source: Reddit comment on tokencast (paraphrased)*

---

## Section 1: Concern Analysis

### Concern 1: Output token variance kills estimate accuracy in agentic loops

**Valid?** Yes. This is the single most important technical challenge in pre-execution estimation. The commenter correctly identifies that input token estimation from a plan doc is the easy part. Output tokens and cascading tool calls are where variance explodes.

**Do we have data?** Partially. We have 5 recorded sessions. Three have clean actual/expected ratios (1.07x, 1.12x, 1.71x). Two sessions have unreliable ratios because session JSONL included pre-estimate work that could not be subtracted. The clean sessions show that the estimation model handles output variance reasonably for the workflows tested so far, but the sample size is small and concentrated on a single project (tokencast itself).

**Urgency:** Must-address-now. This is the credibility question. If we cannot show data here, nothing else in the response matters.

### Concern 2: What is the mean absolute error?

**Valid?** Yes. This is the right metric to ask for. The commenter even set a low bar ("tested on 20 runs, within 30%") which shows they understand early-stage tools.

**Do we have data?** Yes, but limited. We have 3 clean sessions with computable error. We do not have 20 runs. We should be honest about the sample size and present what we have.

**Urgency:** Must-address-now. This is the single most important data point for the response.

### Concern 3: Per-step or just total pipeline cost?

**Valid?** Yes. Per-step estimation is genuinely more actionable than total-only.

**Do we have data?** Yes -- this is a core feature. tokencast has estimated per-step costs since v1.0 and has had per-step calibration factors since v1.4. Since v1.7, actual per-agent step cost attribution exists via sidecar timeline (not just proportional allocation). This is a clear strength we should highlight.

**Urgency:** Must-address-now. Easy win -- we already have this and it differentiates us.

### Concern 4: How does it handle branching paths / different tool-use routes?

**Valid?** Partially. This is a real limitation, but the framing assumes more path-divergence than typical agentic workflows produce. Most multi-step pipelines follow a predetermined sequence of agents; the variance is within each step, not between entirely different execution paths.

**Do we have data?** We handle this through: (a) three confidence bands (optimistic at 0.6x, expected at 1.0x, pessimistic at 3.0x) that bracket the range of outcomes, (b) complexity multipliers (0.7x/1.0x/1.5x) applied at estimate time based on task analysis, (c) mid-session cost warnings when spend approaches the pessimistic bound. We do NOT dynamically re-estimate mid-execution based on tool-use patterns -- that would require a different architecture.

**Urgency:** Can-address-later. The band approach is a reasonable answer. Dynamic re-estimation is a real feature gap but not one we need to solve before shipping.

### Concern 5: What differentiates tokencast from asking Claude directly for a token estimate?

**Valid?** Yes, and this is a sharp question. "The comparison I'd actually make is against just prompting Claude directly" is the most dangerous competitive threat because it costs nothing and requires no tooling.

**Do we have data?** We have a strong structural argument but no head-to-head comparison data. The structural advantages are: (a) tokencast uses calibrated heuristics grounded in actual activity-level token budgets, not LLM intuition, (b) it learns from your actual sessions via 5-level calibration precedence, (c) it accounts for context accumulation within steps (triangular growth model), (d) it auto-measures file sizes on disk to adjust estimates, (e) Claude asked directly has no memory of your past sessions and cannot self-calibrate. However, we have not actually run a comparison.

**Urgency:** Must-address-now. This is the "why does this tool exist" question. We need to answer it convincingly but honestly -- and we should probably run the comparison.

---

## Section 2: Data We Have

### Session Cost History

We have 5 recorded calibration sessions, all on the tokencast project itself (single-project, single-developer):

| Session | Version | Actual | Expected | Ratio | Ratio Reliable? | Review Passes |
|---------|---------|--------|----------|-------|-----------------|---------------|
| 1 | v1.3.1+v1.4.0 | $20.22 | $11.84 | 1.71x | Yes | 5 (est: 2) |
| 2 | v1.5.0 | $6.68 | $6.24 | 1.07x | Yes | 4 (est: 2) |
| 3 | v1.6.0 | $11.55 | $10.33 | 1.12x | Yes | 3 (est: 2) |
| 4 | v1.7.0+v2.0.0 | $90.18 | $15.18 | 5.94x | No* | 4 (est: 2) |
| 5 | v2.1.0 | $110.74 | $5.18 | 21.38x | No* | 11 (est: 2) |

*Sessions 4 and 5 have inflated ratios because the session JSONL includes substantial pre-estimate work from context compaction. The actual task cost cannot be isolated from the total session cost in these cases. This is a known measurement limitation that v2.1.0 addressed with baseline cost subtraction and continuation session reconstitution.

### Clean Session Accuracy Metrics (Sessions 1-3 only)

| Metric | Value |
|--------|-------|
| Sample count | 3 |
| Ratios | 1.71x, 1.07x, 1.12x |
| Mean ratio | 1.30x |
| Mean absolute percentage error (MAPE) | 30% |
| All within pessimistic band (3.0x)? | Yes |
| All within 2x of expected? | Yes |

**Interpretation:** Across 3 clean sessions, the expected-band estimate was within 30% of actual cost on average. The worst miss (1.71x) was driven primarily by PR review loop underestimation (5 actual passes vs 2 estimated). After that session, the project-specific `review_cycles` override was increased to 4, and subsequent sessions (1.07x, 1.12x) were markedly more accurate.

This demonstrates the calibration learning loop working as intended: the first session's overrun improves subsequent estimates.

### Per-Step Attribution Data

Since v1.7.0, tokencast records per-agent step cost attribution via a sidecar timeline (not just proportional allocation). The sidecar uses FIFO span matching to attribute actual token costs to specific pipeline steps. This means we can (and do) show which steps consumed the most cost, both estimated and actual.

Per-step calibration factors have been active since v1.4.0 (3+ samples per step required for activation). The 5-level calibration precedence chain (per-signature, per-step, size-class, global, uncalibrated) means the most specific available factor is always used.

### Calibration System Depth

| Feature | Status |
|---------|--------|
| Three confidence bands (0.6x / 1.0x / 3.0x) | Shipping since v1.0 |
| Per-step cost breakdown in estimates | Shipping since v1.0 |
| Global calibration factor | Shipping since v1.1 |
| Per-size-class factors (XS/S/M/L) | Shipping since v1.1 |
| PR review loop decay model | Shipping since v1.2 |
| Parallel agent cost discounting | Shipping since v1.3 |
| Per-step calibration factors | Shipping since v1.4 |
| File size auto-measurement | Shipping since v1.5 |
| Time-decay weighting (30-day halflife) | Shipping since v1.6 |
| Per-pipeline-signature factors | Shipping since v1.6 |
| Mid-session cost warnings (80% pessimistic) | Shipping since v1.6 |
| Per-agent step cost attribution (sidecar) | Shipping since v1.7 |
| Calibration health dashboard | Shipping since v2.0 |
| Continuation session reconstitution | Shipping since v2.1 |

---

## Section 3: Draft Reddit Response

---

Developer of tokencast here. These are sharp questions -- exactly the kind of scrutiny that helps me figure out what to publish and what to build next.

**On accuracy data:** You are right that I owe people numbers. I have 3 clean calibration sessions with reliable actual-vs-expected ratios (2 more sessions exist but their ratios are inflated by a measurement artifact that v2.1 fixed). The ratios are:

- 1.71x (first calibrated session -- PR review loop ran 5 passes vs 2 estimated)
- 1.07x (second session, after calibration adjusted)
- 1.12x (third session)

Mean absolute percentage error across those 3: ~30%. Small sample, but it shows the calibration loop working -- the first session's overrun directly improved the next two. All three landed within the pessimistic band (3.0x).

I am not going to claim "within 30% on 20 runs" because I do not have 20 runs yet. What I can say is that the architecture is designed for accuracy to improve monotonically as sessions accumulate, and the early data is consistent with that.

**On per-step estimation:** Yes, tokencast estimates per-step, not just total. Each pipeline step gets its own row with optimistic/expected/pessimistic costs. Since v1.4, per-step calibration factors activate after 3+ sessions for a given step, so the system learns which steps you consistently over- or under-estimate. Since v1.7, actual per-agent cost attribution is tracked via a sidecar timeline -- not just proportional allocation -- so you get real data on which steps consumed the most tokens.

This is exactly the "redesign the expensive steps" workflow you described. If your Staff Review step is consistently 2x the estimate, tokencast surfaces that and adjusts future estimates.

**On branching paths / tool-use route variance:** tokencast does not attempt to predict which tool-use route an agent will take. Instead, it brackets the outcome with three bands:

- Optimistic (0.6x): the agent stays focused, no rework
- Expected (1.0x): typical run with some exploration
- Pessimistic (3.0x): debugging loops, rework, re-reads

The 3x pessimistic multiplier is deliberately wide because agentic loops have fat tails, as you noted. For PR review loops specifically, there is a geometric decay model (each review cycle costs 60% of the prior one) that captures the "diminishing findings" pattern.

What tokencast does NOT do is re-estimate mid-execution based on what the agent discovers. It does warn you at 80% of the pessimistic bound so you can decide whether to continue. Dynamic re-estimation based on tool-use patterns is on the roadmap but is genuinely hard -- it requires real-time token accounting that is framework-dependent.

**On "just ask Claude for an estimate":** This is the right comparison to make, and I think it is the strongest argument FOR a dedicated tool rather than against one. Three reasons:

1. *Calibration memory.* Claude asked directly has zero memory of your past sessions. tokencast has a 5-level calibration chain (per-pipeline-signature, per-step, size-class, global, uncalibrated) that gets more accurate with every session. After 10+ sessions, the system has learned your specific workflow patterns -- which steps you underestimate, which pipeline shapes cost more than expected, how your project's file sizes affect token consumption.

2. *Grounded heuristics vs. LLM intuition.* tokencast uses activity-level token budgets (file reads = 10K input tokens for a medium file, code review pass = 8K input + 3K output, etc.) with context accumulation modeling (triangular growth within each step). Claude asked directly is guessing from vibes. It has no model of how context windows fill during multi-step pipelines.

3. *Automatic file measurement.* tokencast reads your actual files from disk (via `wc -l`, capped at 30 files) and assigns small/medium/large token budgets based on real line counts. "Refactor auth module" gets a different estimate depending on whether auth.py is 40 lines or 800 lines. Claude asked directly does not have access to your filesystem in a prompt-only interaction.

I have not run a head-to-head comparison yet -- that is a fair gap in my evidence and something I should do. My hypothesis is that Claude-direct is reasonable for a single estimate but diverges as you accumulate calibration data, because it cannot learn from your specific history.

**On the LangSmith/Helicone differentiation:** Agreed that the real gap is pre-execution vs. post-execution, not features. The thing I would add: post-hoc tools help you understand what happened; tokencast helps you decide whether to run something. These are complementary, not competing. If you are already using LangSmith for tracing, tokencast adds the "should I run this $15 pipeline or restructure it first?" decision point that LangSmith cannot provide.

---

## Section 4: Product Implications

### Immediate Actions (This Week)

1. **Run a head-to-head comparison: tokencast vs "ask Claude directly."** The commenter's challenge is valid and we have no data to refute it. Run 5-10 estimation scenarios through both approaches, measure accuracy against actuals. If tokencast wins (especially after calibration), publish the results. If it does not, we have a product problem to fix.

2. **Publish accuracy data.** The 3-session accuracy table should be on the README and the wiki. Even small-sample data builds more credibility than no data. Update it as sessions accumulate. Consider adding an accuracy badge to the repo.

3. **Add an accuracy section to the calibration health dashboard.** The status dashboard (v2.0) already computes accuracy metrics. Make these easy to export or screenshot for sharing.

### Short-Term (Phase 1.5 Validation)

4. **"Branching path" estimation is a real gap.** The commenter's example -- "refactor auth module might cost 2K or 40K" -- is not fully addressed by bands alone. Consider a feature where tokencast can show sensitivity analysis: "if complexity is medium, $X; if high, $Y; if the agent enters a debugging loop, $Z." This would make the bands more interpretable than raw multipliers.

5. **Collect more calibration sessions across diverse projects.** All 5 sessions are on tokencast itself. The accuracy story is weak until we have data from different codebases, languages, and team sizes. Phase 1.5 direct outreach should explicitly ask early adopters to share anonymized accuracy data.

### Positioning / Messaging Changes

6. **Lead with "learns from your history" not "estimates cost."** The commenter correctly identified that basic estimation is table stakes. The differentiator is calibration learning -- the thing that improves over time and cannot be replicated by a one-shot Claude prompt. All messaging should emphasize the learning loop, not the initial estimate.

7. **Acknowledge the "ask Claude directly" comparison explicitly.** Do not pretend it does not exist. Add a FAQ or comparison section that honestly addresses it. The honest answer is: "Claude-direct is free and reasonable for a first estimate; tokencast gets better over time because it remembers your sessions."

8. **Frame the pessimistic band as a feature, not a limitation.** The 3x pessimistic multiplier exists because agentic loops have fat tails. This is a design choice that acknowledges the variance the commenter described, not a failure to be more precise. Message it as: "We give you the range because pretending we know the exact cost would be dishonest."

### Deferred / Future Consideration

9. **Dynamic mid-execution re-estimation.** The commenter's branching-path concern points toward a feature where tokencast updates its estimate as the agent executes, based on actual token consumption so far. This is architecturally complex (requires real-time token accounting per step) but would be genuinely differentiated. Defer to post-Phase-1.5 based on demand signal.

10. **Benchmark dataset.** Once we have 20+ sessions across multiple projects, publish an anonymized benchmark dataset showing estimation accuracy by project type, size class, and calibration depth. This is the ultimate credibility artifact and would be difficult for competitors to replicate quickly.

---

## Section 5: Review of Architect's Technical Assessment

*Reviewed: 2026-03-26. Source: `/Volumes/Macintosh HD2/Cowork/Projects/costscope/docs/reddit-technical-response.md`*

### 5.1 Assessment of the "Just Ask Claude" Differentiation Story

The architect identified seven structural advantages tokencast has over a direct Claude prompt. The analysis is technically sound but has a messaging problem: it reads like an engineer explaining architecture to another engineer. The Reddit audience needs a simpler story.

**What works in the architect's framing:**
- The calibration memory argument (point 2) is the strongest differentiator and the architect correctly identifies it as such. The framing "on session 1, marginally better; by session 10, significantly better; by session 30, uncatchable" is exactly the right narrative arc.
- The reproducibility argument (point 4) is underrated. For teams that need to compare estimates across sessions or track cost trends, deterministic output matters. LLMs produce different numbers every time you ask.
- The "when IS just-ask-Claude good enough" section is honest and builds credibility. Admitting the tool has no advantage for one-off unfamiliar workflows is more persuasive than overclaiming.

**What needs sharpening:**
- The architect lists seven advantages. That is too many for a Reddit response. The response should lead with exactly two: calibration memory and grounded arithmetic. Everything else is supporting evidence, not a headline.
- The "grounded heuristics vs. LLM intuition" argument (point 1) is true but abstract. It would be more compelling with a concrete example: "tokencast knows that a 6-step pipeline with 8 files at an average of 300 lines each will cost $X because it decomposes the work into 6 file reads at 10K tokens, 8 file edits at 2.5K tokens, context accumulation across K activities, and three-tier cache pricing. Claude guessing at the same task will produce a number, but it has no grounded model of how context windows fill or how cache pricing works across steps."
- The architect does not address a subtle weakness in the calibration argument: calibration only helps if the user runs similar workflows repeatedly. A team that does a different kind of task every session will accumulate calibration data that averages out to the global factor, which is not much better than a heuristic. The honest answer is that calibration rewards consistency -- teams with repeatable pipelines benefit most.

**PM recommendation on differentiation story:** The Reddit response (Section 3 above) should be revised to lead with two points, not three:

1. **Calibration memory** -- the tool gets smarter with every session; Claude does not. This is the "why install anything" answer.
2. **Deterministic, grounded arithmetic** -- tokencast decomposes the plan into measurable activities with known token budgets and real file sizes from disk. It does not guess. This is the "why not just ask Claude" answer.

Drop the file measurement argument from the headline position. It is a supporting detail for point 2, not a standalone differentiator.

**Do we need the head-to-head benchmark?** Yes, absolutely. The architect is right that we do not have one and that it is a gap. But the benchmark should be designed carefully to show the divergence over time, not just a single comparison. The compelling result is: "On session 1, tokencast and Claude-direct are within 15% of each other. By session 5, tokencast is 2x more accurate." If that result does not materialize, we have a product problem, not a messaging problem.

### 5.2 Prioritization of Architect's Proposed Improvements

The architect proposed five improvements (Section 5 of their report). Here is the PM priority stack, reordered by what moves the needle on the specific concerns this feedback raised.

**Priority 1: Publish Accuracy Metrics (Architect's 5.1) -- Do This Week**

This is the single highest-leverage action available to us right now. It requires zero code changes. The status dashboard already computes the metrics. What we need is:
- An "Accuracy" section on the README with the 3-session table, honest about sample size
- The same data on the wiki How-It-Works page
- A standing invitation for early adopters to share anonymized accuracy data
- A commitment to update the numbers as sessions accumulate

This directly addresses Concern 2 (mean absolute error) and builds trust for every other claim we make. Every week we delay publishing accuracy data is a week where potential users see claims without evidence.

**Priority 2: Historical Accuracy Benchmark Suite (Architect's 5.5) -- Next Version (v2.2 or v3.0)**

This is the "tested on 20 runs" artifact the commenter asked for. But it requires work the architect underestimated:
- We need 20+ plan-to-actual pairs, which means we need either (a) 20 more sessions on our own projects, or (b) early adopter data from Phase 1.5 outreach. Neither is available this week.
- The benchmark should include the head-to-head comparison against Claude-direct estimates. Running both estimators on the same plans and measuring accuracy against the same actuals is the only way to prove the differentiation story.
- The benchmark should be reproducible and version-tagged. Each tokencast release should re-run the suite and report whether accuracy improved or regressed.

This is a Phase 1.5 deliverable. It aligns with the enterprise strategy's validation experiments (Experiment 3: community distribution) because the benchmark results are the content for the blog post and Show HN submission. Target: have 10+ plan-to-actual pairs by end of Phase 1, 20+ by end of Phase 1.5.

**Priority 3: Output Token Scaling by Task Type (Architect's 5.2) -- Next Version**

The architect's proposal to scale output token budgets by file bracket (small file edit = 800 output, large = 3,000) is a targeted accuracy improvement that directly addresses Concern 1 (output variance). It uses existing infrastructure (file brackets already measured) and the implementation is contained to heuristics.md and the estimation engine.

However, this should be validated against actual data before shipping. If our per-step actuals show that output token variance correlates with file size brackets, the improvement is justified. If the correlation is weak, the added complexity is not worth it. Check the sidecar data from existing sessions before committing to build.

**Priority 4: Variance-Aware Calibration (Architect's 5.3) -- Defer to v3.0+**

This is the right long-term answer to Concern 1 (output variance) and Concern 4 (branching paths). Tightening bands for consistent users and widening them for high-variance users would make the bands genuinely informative rather than static multipliers.

But the implementation is complex (changes to update-factors.py, factors.json schema, estimation engine, and all downstream consumers), and we do not have enough data yet to validate the variance thresholds. The architect proposed low (<0.3) and high (>0.8) variance cutoffs with no empirical basis. We need 20+ sessions to know what "normal variance" looks like, and we need multi-project data to know whether variance is a per-user property or a per-project property.

This belongs on the roadmap (v3.0, after cross-project intelligence) but should not be built until the benchmark suite provides the data to set the thresholds correctly.

**Priority 5: Conditional Step Modeling (Architect's 5.4) -- Defer Indefinitely**

The architect's proposal for probability-weighted steps is intellectually clean but practically premature. Three reasons to defer:

1. **No user has asked for this.** The Reddit commenter raised branching paths as a theoretical concern. Nobody has said "I need to annotate steps with probability weights."
2. **The bands already serve this function.** The pessimistic band assumes everything that could run does run. The optimistic band assumes the short path. Probability weights would improve precision but add complexity to both the input format and the mental model.
3. **The learning pipeline implications are significant.** If a step was estimated at 0.3 probability but actually ran, what is the "correct" actual/expected ratio for calibration? The calibration algorithm would need to handle partial-execution steps, which is a meaningful change to the learning loop.

If demand emerges from Phase 1.5 outreach, reconsider. Otherwise, leave it in the "Future / Ideas" section of the roadmap.

### 5.3 Messaging Improvements Based on Architect's Analysis

The architect's report contains several insights that should change how we talk about tokencast in the README and docs.

**Change 1: Add a "How Accurate Is It?" section to the README.**

Current README leads with installation instructions. It should lead with the value proposition and include accuracy data early. Proposed structure:

```
# tokencast
Pre-execution cost estimation that learns from your history.

## How Accurate Is It?
[3-session accuracy table]
[Honest note about sample size]
[Link to calibration algorithm docs for depth]

## How It Works
[Brief: estimate -> execute -> learn -> better estimate]

## Installation
[Current content]
```

This directly addresses the commenter's concern that accuracy data is not published.

**Change 2: Add a "Why Not Just Ask Claude?" FAQ entry.**

The architect's honest "when is just-ask-Claude good enough" framing should become a FAQ entry. Be direct:

- "For a one-off estimate of an unfamiliar workflow, just asking Claude is probably fine."
- "For repeated workflows where you want estimates to improve over time, tokencast's calibration system produces estimates that no stateless prompt can match after 5-10 sessions."
- "tokencast also gives you deterministic, reproducible estimates -- Claude gives a different number every time."

This turns a potential objection into a trust-building moment.

**Change 3: Rewrite the tagline.**

Current: "Pre-execution cost estimation for LLM agent workflows."

This describes the mechanism, not the value. The architect's insight -- that calibration is the moat -- suggests a different framing.

Proposed: "Cost estimation for LLM agent workflows that gets smarter with every session."

The phrase "gets smarter" communicates the learning loop in four words. It also sets the expectation that session 1 is a starting point, not the final answer, which preempts the "what if the first estimate is wrong" objection.

**Change 4: Add a "Calibration Learning Curve" visual to the wiki.**

The architect noted that the value divergence between tokencast and Claude-direct grows over time. This should be visualized. A chart showing estimated accuracy (y-axis) vs. session count (x-axis) with two lines -- tokencast (improving) and Claude-direct (flat) -- would communicate the core value proposition faster than any paragraph of text. Even a schematic version (not real data) would be effective, labeled as "conceptual" until we have the benchmark data to make it empirical.

### 5.4 Should We Run the Accuracy Benchmark?

**Yes. This is the single most important thing we can do in response to this feedback.**

The commenter set the bar: "tested on 20 runs, within 30%." We have 3 clean runs at ~30% MAPE. We are not far from the bar -- we just need more data points.

**How to structure the benchmark:**

1. **Phase A (this week, zero code changes):** Publish the 3-session accuracy table on the README and wiki. Be transparent about the sample size. Add a note: "We are actively collecting more data and will update these numbers as sessions accumulate."

2. **Phase B (next 2-4 weeks, as sessions accumulate):** Every tokencast development session produces a calibration data point. We are building tokencast using tokencast -- each PR adds to the benchmark. By the end of Phase 1 (MCP server shipped), we should have 8-12 sessions.

3. **Phase C (Phase 1.5, with early adopters):** Ask the 10 direct-outreach engineers to share anonymized accuracy data (actual/expected ratios, session count, pipeline signature). This gives us cross-project diversity. Target: 20+ data points across 3+ projects.

4. **Phase D (Phase 1.5, head-to-head):** Take 10 historical plans (from our own sessions and early adopters). For each plan, run both tokencast and a direct Claude prompt. Compare both estimates against the known actual cost. Publish the results as a blog post and the basis for the Show HN submission.

**What to do if the benchmark shows tokencast is NOT meaningfully better than Claude-direct:**

This is the scenario we must plan for honestly. If the head-to-head shows Claude-direct within 10% of tokencast's accuracy even after calibration, then the calibration story is marketing, not substance. In that case:

- The value proposition shifts to operational features: mid-session warnings, per-step attribution, reproducibility, automated learning loop (convenience, not accuracy)
- The pricing story changes: we cannot charge for accuracy that is available for free
- The roadmap priority shifts: variance-aware calibration and the benchmark suite become urgent because we need the accuracy gap to be real before Phase 1.5

This is not a disaster -- operational tooling has value even without an accuracy advantage. But it changes the story fundamentally, and we should know before we write the blog post.

### 5.5 Summary: Ordered Action Items

| Priority | Action | Owner | Timeline | Depends On |
|----------|--------|-------|----------|------------|
| P0 | Publish 3-session accuracy table on README + wiki | PM / docs-updater | This week | Nothing |
| P0 | Revise Reddit response per Section 5.1 feedback (two-point differentiation) | PM | This week | Nothing |
| P1 | Rewrite README tagline to lead with calibration learning | PM | This week | Nothing |
| P1 | Add "Why Not Just Ask Claude?" FAQ to wiki | PM / docs-updater | This week | Nothing |
| P2 | Run head-to-head benchmark (Phase D above) on next 5 sessions | Engineer | Next 4 weeks | 5+ more sessions |
| P2 | Output token scaling by file bracket (architect's 5.2) | Engineer | Next version | Sidecar data validation |
| P3 | Historical accuracy benchmark suite (architect's 5.5) | Engineer / QA | Phase 1.5 | 20+ plan-to-actual pairs |
| P3 | Calibration learning curve visual for wiki | PM / frontend-designer | Phase 1.5 | Benchmark data |
| P4 | Variance-aware calibration (architect's 5.3) | Engineer | v3.0+ | Benchmark suite validation |
| Defer | Conditional step modeling (architect's 5.4) | -- | Indefinite | Demand signal |
