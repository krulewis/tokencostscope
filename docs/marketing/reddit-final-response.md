Developer of tokencast here. These are exactly the right questions -- let me answer them with actual data instead of hand-waving.

**On accuracy:** I have 3 clean calibration sessions with reliable actual/expected ratios (2 more exist but have inflated ratios from a measurement artifact that v2.1 fixed). The numbers:

- 1.71x (first session -- PR review loop ran 5 passes vs 2 estimated)
- 1.07x (after calibration adjusted from the first overrun)
- 1.12x

Median error: 12%. Range: 7%–71%. The mean (~30%) is skewed by the first session's 1.71x miss. I know that is not "tested on 20 runs" -- the sample is small and all from one project (tokencast itself). I am not going to dress that up. The improvement is consistent with calibration adjusting (plus a project-specific review-cycle override I raised from 2→4 after session 1, which itself explains some of the correction). I am actively collecting more data points and will publish updated numbers as they accumulate.

**On per-step vs total:** Per-step, always. Each pipeline step gets its own row with optimistic/expected/pessimistic costs, mapped to the specific model (Opus/Sonnet/Haiku) that step uses. Since v1.4, per-step calibration factors activate after 3+ sessions, so if your Implementation step is consistently 1.5x while Research is 0.8x, the system learns that. Since v1.7, actual per-agent cost attribution is tracked via a sidecar timeline -- real measurement, not proportional allocation. This is exactly the "redesign the expensive steps before running" workflow you described.

**On branching paths:** You are right that this is a gap. tokencast does not predict which tool-use route the agent will take. What it does is bracket the outcome:

- **Optimistic (0.6x):** focused execution, no rework
- **Expected (1.0x):** typical run, some exploration
- **Pessimistic (3.0x):** discovery-driven rework, debugging loops

The 5x spread between optimistic and pessimistic is deliberately wide because agentic loops have fat tails. For PR review loops specifically, there is a geometric decay model that captures diminishing-findings patterns across cycles. There is also a mid-session warning at 80% of the pessimistic bound so you can bail before a runaway.

What we do NOT do is re-estimate mid-execution based on what the agent discovers. Dynamic re-estimation is on the roadmap but genuinely hard -- it requires real-time token accounting that is framework-dependent.

**On "just ask Claude":** This is the sharpest question, so let me be direct about when each approach wins.

The core difference is **calibration memory**. Claude asked directly starts from zero every time. tokencast accumulates correction factors across sessions at 5 levels of specificity (per-pipeline-signature, per-step, size-class, global, uncalibrated). On session 1, the advantage is modest -- structured decomposition and reproducibility, but roughly similar accuracy. By session 10, the calibration advantage is significant. By session 30, time-decay-weighted per-step factors are providing corrections that no stateless prompt can replicate.

The second difference is **grounded arithmetic vs. LLM reasoning**. tokencast decomposes work into measurable activities (6 file reads at 10K tokens each for a medium file, 8 edits at 2.5K, context accumulation across K activities, three-tier cache pricing per band). It also measures your actual files from disk to assign token budgets. Claude can reason about costs if given pricing data and a plan, but it has no filesystem access for file measurement, produces non-deterministic outputs (ask twice, get different numbers), and tends to make arithmetic errors on multi-term cost formulas with cache pricing tiers.

Honestly though: for a one-off estimate of an unfamiliar workflow you will never repeat, just asking Claude is probably fine. tokencast's value grows with use -- it rewards teams with repeatable pipelines.

I have not run a head-to-head comparison yet. That is a fair gap in my evidence and it is next on my list.

**Where we are vs where we are going:**

- *Now:* 3 clean accuracy data points at ~30% MAPE, all single-project. Per-step estimation and calibration are working. The system learns and improves.
- *Next:* Publishing accuracy metrics on the README (this week). Collecting cross-project data from early adopters. Running the head-to-head benchmark against Claude-direct estimates.
- *Later:* Variance-aware band tightening (users with consistent sessions get tighter bands), output token scaling by file size bracket, and the full benchmark suite with 20+ plan-to-actual pairs across multiple projects.

Repo is at [github.com/krulewis/tokencast](https://github.com/krulewis/tokencast) if you want to poke at the estimation algorithm directly. Accuracy data will be on the README once I stop being embarrassed about N=3.
