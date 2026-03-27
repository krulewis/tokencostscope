1# tokencast — Revised Enterprise Strategy

*Revised: 2026-03-24. Incorporates adversarial review findings from six agents (Sr PM, Architect, Staff Engineer, Devil's Advocate, competitive researcher, distribution researcher) and owner responses. Supersedes enterprise-strategy.md (2026-03-22).*

*The original strategy is preserved at `docs/enterprise-strategy.md` as a baseline.*

---

## Reality Check

This is a side project built by one developer using Claude Code, at roughly 10 hours per week. Every timeline, scope decision, and infrastructure commitment in this strategy must be honest about that constraint.

The original strategy described a 9-18 month four-phase enterprise journey. At 10hrs/week, that's 3-5 years. More importantly, a solo side project cannot operate a hosted backend with uptime SLAs, incident response, and compliance audits.

This revised strategy is designed for a solo developer who wants to build something genuinely useful, validate whether anyone else cares, and only then decide whether to invest more.

---

## The Opportunity (Revised)

No production-ready tool exists today that gives engineering teams predictive, calibrated cost estimation for multi-step LLM agent workflows — before execution — with learning from actual usage data.

The closest things:
- **Token counters and loggers** (LangSmith, Langfuse, Helicone): record what happened post-execution. LangSmith has 250K users, $1.25B valuation, 1B traces — dominant in post-hoc observability.
- **Budget caps** (Portkey, LiteLLM): stop runaway costs with hard limits, don't predict them. Portkey ($18M Series A, 650+ orgs) is closest on the governance/FinOps dimension.
- **PreflightLLMCost** (MIT, solo project): pre-execution estimation for single LLM calls, not multi-step agent workflows. Validates the concept exists independently.
- **Spreadsheet models**: manual, not integrated, don't learn.

The gap is real but narrow. It is a **feature gap** in existing observability platforms, not a product category gap. This distinction is critical: enterprise buyers will compare tokencast to Helicone's dashboard, not to the absence of a product.

### The Window

The window is real but closing. LangSmith's 1B-trace dataset is an enormous calibration advantage if they decide to ship estimation. Portkey's "AI FinOps" positioning is directionally close. Anthropic could build basic estimation into Claude Code in weeks.

The defensible position is not "first to market" (the market is too small for a land-rush). It is **calibration accuracy across frameworks** — the thing that requires accumulated data and algorithmic sophistication that a quick feature addition can't replicate.

### What Needs to Be True (That We Don't Yet Know)

1. **Do engineers want pre-execution estimates, or just budget alerts?** The v1.6 mid-session warning might be the actual product.
2. **Does shared calibration beat local calibration?** The core Phase 3 value prop is untested.
3. **Is the market large enough?** No TAM sizing exists. Agentic LLM workflows may remain niche through 2027.
4. **Will anyone pay for this?** Zero outside customer validation exists today.

These are not risks to manage — they are hypotheses to test. The strategy is structured around testing them before committing resources.

---

## Current State (v2.0.0)

What works:
- Pre-execution cost estimates with three bands (optimistic, expected, pessimistic)
- 5-level calibration learning (per-signature → per-step → size-class → global → uncalibrated)
- Per-agent step actual cost attribution via sidecar timeline
- Time-decay weighting (30-day halflife) so recent sessions dominate
- Mid-session cost warnings when spend approaches pessimistic threshold
- Status dashboard with accuracy trends, cost attribution, and actionable recommendations
- Backward compatible: old sessions don't break new features
- 441 tests passing

What doesn't exist:
- Any user beyond the developer
- Customer validation of the value proposition
- Multi-framework support (Claude Code only)
- Any backend (everything runs on local disk)
- MCP server distribution (currently SKILL.md only)
- Pricing model or revenue mechanism

### Architecture Honest Assessment

The original strategy claimed several architecture decisions were "already aligned with enterprise." The architect reviewed the actual code and scored this 3/6:

| Decision | Claimed | Reality |
|----------|---------|---------|
| `calibration_store.py` storage abstraction | "Swap local disk for remote API in one file" | 112 lines of synchronous file I/O. No interface contract, no async, no auth context, no pagination. Swapping to remote is a rewrite of the calling convention, not a file swap. |
| `agent-map.json` configurable mapping | Enterprise teams use non-standard agent names | Genuine — `_load_agent_map()` merges overrides. |
| Sidecar `schema_version=1` | Stable API contract | Schema versioning exists, but cost attribution is coupled to Claude Code JSONL line numbers. Other frameworks need a different attribution pipeline. |
| JSON `schema_version=1` in status.py | Stable integration API | Genuine — version field exists. |
| Framework-agnostic sidecar format | Any framework can write sidecar events | JSON format is agnostic; the attribution model (`_build_spans()`, `DEFAULT_AGENT_TO_STEP`) is Claude-Code-specific. |
| `--json` flag in status.py | Agents and CI can consume structured output | Genuine. |

The sidecar format is technically framework-agnostic, but the entire cost attribution pipeline assumes Claude Code's session JSONL, hook model, and agent naming conventions. A LangChain adapter would need its own attribution pipeline, not just a sidecar writer.

---

## Distribution Strategy: MCP-First

The original strategy proposed Claude Code skill as the "wedge" with multi-framework support deferred to Phase 3 (~12 months out). The adversarial review identified this as the strategy's biggest gap.

**MCP (Model Context Protocol) is already the cross-framework standard the original Phase 3 described — and it's available now.**

- Linux Foundation governed, vendor-neutral
- Adopted by OpenAI, Google DeepMind, Anthropic
- 97M+ monthly SDK downloads, 5,800+ servers
- Works with Claude Code, Cursor, Windsurf, VS Code + Copilot, and any MCP-compatible client
- One-click setup in most IDEs

The revised distribution strategy is **MCP server as primary, SKILL.md as companion, hosted features as upsell**:

```
                    ┌─────────────────────────────────────┐
                    │         tokencast MCP           │
                    │   (estimation + calibration tools)   │
                    └──────────┬──────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
        Claude Code       Cursor/VS Code    Any MCP client
        (+ SKILL.md        (+ Windsurf,     (custom agents,
         companion)         Copilot)         CI pipelines)
              │                │                │
              └────────────────┼────────────────┘
                               │
                    ┌──────────┴──────────────┐
                    │    Local calibration     │
                    │  (free, always works)    │
                    └──────────┬──────────────┘
                               │
                    ┌──────────┴──────────────┐
                    │   Hosted calibration     │
                    │  (team sharing, upsell)  │
                    └─────────────────────────┘
```

### Why MCP Over Skill-Only

| Dimension | SKILL.md only | MCP server |
|-----------|---------------|------------|
| Reach | Claude Code users | ALL LLM clients |
| Platform risk | Anthropic controls | Linux Foundation governed |
| Discoverability | No marketplace | MCP registries, one-click install |
| Multi-framework | Requires per-framework adapters | Built-in via protocol |
| Viability score | 6/10 | 9/10 |

### Additional Distribution Channels

**GitHub Action (Phase 2):** Posts cost estimates as PR comments. Makes tokencast visible to the whole team — not just the individual who installed it. Only for non-public repos initially; requires a web dashboard companion for public repos where cost data shouldn't be in PR comments.

**PyPI package (Phase 2):** `pip install tokencast` with CLI: `tokencast estimate plan.md`. Taps into PyPI's organic discoverability. Usable in CI/CD without any IDE.

**Web dashboard (Phase 3):** Lightweight hosted dashboard for viewing team calibration health. This is the visual companion to the GitHub Action and the entry point for team features.

---

## The Anthropic Contingency

If Anthropic ships native cost tracking in Claude Code, the Claude-Code-specific value of tokencast drops to near zero. This is not a tail risk — it's a likely scenario given Anthropic has the session data, model pricing, and distribution to replicate basic estimation in weeks.

**The moat is multi-framework calibration, not Claude Code integration.**

Contingency plan:
1. **MCP-first distribution means we're already multi-framework.** If Anthropic builds native estimation, tokencast remains the cross-platform option for teams using Claude + GPT + Gemini + LangChain + CrewAI.
2. **Calibration sophistication is the differentiator.** Basic estimation is easy to replicate. 5-level calibration precedence chains, time-decay weighting, per-signature factors, and cross-session learning are not. This is the moat — but only if the calibration data is deep enough to matter.
3. **If Anthropic ships AND multi-framework isn't gaining traction:** the project stays an excellent open-source tool. That's a fine outcome for a side project.

---

## The Path Forward

### Phase 1: Complete the Product + MCP Distribution (Now → 3 months)

*~10hrs/week = ~120 hours total*

**1a. Ship v2.0.0** (in progress, ~2 weeks remaining)
- PR #9 is in review. Finish it.
- This makes tokencast a complete tool for individual Claude Code users.

**1b. Build the MCP server** (~4-6 weeks)
- Expose core estimation as MCP tools: `estimate_cost(plan, metadata)`, `get_calibration_status()`, `get_cost_history()`
- Local calibration still works (MCP server reads/writes local files)
- This immediately makes tokencast available to Cursor, Windsurf, VS Code + Copilot, and any MCP client
- Publish to MCP registries for discoverability

**1c. Decouple cost attribution from Claude Code JSONL** (~2-3 weeks)
- The current attribution pipeline assumes Claude Code session JSONL line numbers
- Define a framework-agnostic attribution protocol where the MCP client reports cost data directly in tool calls (not via JSONL correlation)
- This is the prerequisite for multi-framework support being real, not aspirational

**Exit criteria for Phase 1:**
- MCP server published and installable in Cursor/VS Code
- At least 1 non-Claude-Code client can produce calibrated estimates
- SKILL.md companion still works for Claude Code users

### Phase 1.5: Market Validation Sprint (Month 3-4)

*~40 hours over 4 weeks. The most important phase in this strategy.*

No infrastructure investment until we know whether anyone else wants this. Multiple parallel validation experiments:

**Experiment 1: Opt-in usage telemetry**
- Add anonymous, opt-in telemetry to the MCP server: session count, estimate accuracy ratio, calibration depth, framework used
- Measures: how many installs, how many reach 10+ sessions, what's the retention curve

**Experiment 2: "Share with team" waitlist**
- Add a `team_sharing_interest()` MCP tool that registers interest (email + team size)
- Surface it after a user has 5+ calibrated sessions (the moment they've seen the value)
- Measures: conversion rate from individual user to team-sharing interest

**Experiment 3: Community distribution test**
- Publish to Hacker News (Show HN), r/MachineLearning, relevant Discord communities
- Write a blog post with worked examples (show the estimation table, the calibration learning curve)
- Measures: GitHub stars, MCP installs, inbound interest

**Experiment 4: Direct outreach**
- Identify 10 engineers/teams with high agentic-workflow usage (Claude Code power users, LangChain contributors, CrewAI community members)
- Offer them early access to the MCP server
- Ask: "Do you need pre-execution cost estimates, or just better post-execution visibility?"
- Measures: qualitative signal on value proposition

**Experiment 5: Shared calibration simulation**
- Take existing calibration history (your own sessions)
- Generate synthetic "team" histories with varying workflow profiles (bug fixes, greenfield, refactoring)
- Pool factors across synthetic teams; measure whether pooled factors improve or degrade per-team accuracy compared to local-only
- This tests the core Phase 3 hypothesis without building any infrastructure
- Measures: accuracy improvement (or degradation) from pooling

**Go/No-Go Gate → Phase 2:**

| Signal | Threshold | What it means |
|--------|-----------|---------------|
| MCP installs | 50+ in 60 days | There is baseline interest beyond the developer |
| Active users (10+ sessions) | 10+ | The tool retains users past initial curiosity |
| Team sharing waitlist | 5+ signups | Teams see value in shared calibration |
| Direct outreach signal | 3+ of 10 say "I'd use this" | Qualitative validation of the value prop |
| Calibration simulation | Pooled factors improve accuracy by >10% | Shared calibration hypothesis holds |

**If the gate is not met:** tokencast remains an excellent open-source tool. Revisit in 6 months when the market may have matured. This is not a failure — it's a rational outcome for a side project.

**If the gate is met:** proceed to Phase 2 with confidence that there is real demand.

### Phase 2: Team Features + Lightweight Backend (Month 5-10)

*Only if Phase 1.5 validation passes the go/no-go gate.*

**Minimum viable shared calibration:**
The architect estimated a full backend (auth, API, tenant isolation, factor recomputation) at 8-12 weeks with a team. For a solo developer at 10hrs/week, that's 8-12 months — too slow.

Instead, the minimum viable Phase 2 is:

1. **GitHub-based calibration sharing** (~3-4 weeks)
   - Teams create a private GitHub repo for shared calibration
   - MCP server can push/pull `factors.json` and anonymized history to/from the repo
   - Factor merging happens locally (each client computes from the shared history)
   - Zero infrastructure cost. GitHub handles auth, access control, and storage.
   - Friction: team member needs a GitHub account and repo access (low for engineering teams)

2. **GitHub Action for PR cost estimates** (~2-3 weeks)
   - Posts cost estimate as a PR comment when a plan file is detected
   - Makes tokencast visible to the whole team
   - Links to the shared calibration repo for context

3. **PyPI package** (~2-3 weeks)
   - `pip install tokencast` with CLI interface
   - Enables CI/CD integration without IDE dependency
   - Organic discoverability via PyPI search

**Why GitHub-based, not a hosted backend:**
- Zero infrastructure to operate (critical for a side project)
- Git handles versioning, access control, and audit trail
- Teams already use GitHub for shared config
- Migrating to a hosted backend later is straightforward (the data model is the same)

**Exit criteria for Phase 2:**
- 3+ teams actively sharing calibration via GitHub
- GitHub Action producing PR comments in at least 2 repos
- PyPI package with 100+ downloads

**Go/No-Go Gate → Phase 3:**

| Signal | Threshold | What it means |
|--------|-----------|---------------|
| Teams sharing calibration | 3+ active | Shared calibration has real-world value |
| Shared vs local accuracy | Shared is measurably better | The hypothesis is validated |
| GitHub Action adoption | 5+ repos | Team-visible distribution works |
| Inbound requests for hosted features | 3+ | Market pull toward Phase 3 |
| Revenue signal | 1+ team willing to pay for premium features | Pricing model is viable |

### Phase 3: Hosted Backend + Revenue (Month 10-18)

*Only if Phase 2 validation passes. This is where the project either becomes a product or stays a well-loved open-source tool.*

What the hosted backend adds beyond GitHub-based sharing:
- **Web dashboard**: team calibration health, accuracy trends, cost attribution visualization
- **Server-side factor computation**: faster convergence, handles large teams
- **Cross-team benchmarks**: anonymized, aggregated calibration benchmarks for common workflow patterns (opt-in)
- **API access**: programmatic estimation for CI/CD pipelines, custom integrations
- **Team management**: invite members, manage access, set team-level calibration preferences

**Infrastructure approach:**
- Serverless (Vercel Functions or equivalent) to minimize ops burden
- Neon Postgres or equivalent managed database
- Auth via GitHub OAuth (teams already authenticated via GitHub from Phase 2)
- No SLA, no 24/7 support — this is a developer tool from a solo developer, not enterprise infrastructure

**Go/No-Go Gate → Phase 4:**

| Signal | Threshold | What it means |
|--------|-----------|---------------|
| Paying teams | 10+ | Revenue model works |
| Monthly recurring revenue | Covers infrastructure + $500/mo developer time | Self-sustaining |
| Enterprise inbound | 3+ requests for SSO/compliance | Enterprise demand is real |
| Team growth rate | 2+ new teams/month | Organic growth, not just early adopters |

### Phase 4: Enterprise Features (Month 18+)

*Only if Phase 3 shows real revenue and enterprise demand. At this point, the project likely needs additional help — a co-founder, contractor, or OSS contributors.*

Enterprise capabilities (in priority order):
1. **SSO/SAML** — required for enterprise procurement
2. **Audit logging** — exportable trail of who estimated what at what cost
3. **Cost allocation** — tie estimates to projects, teams, cost centers
4. **Data residency** — EU-region storage option
5. **Custom calibration curation** — review/approve calibration changes before they affect estimates (regulated industries)

These are not "layers on top." They are foundational infrastructure that requires dedicated engineering time. A solo developer should not attempt Phase 4 without additional help.

---

## Pricing Strategy

### Competitive Pricing Landscape

| Tool | Free Tier | Paid Trigger | Price Point | Model |
|------|-----------|-------------|-------------|-------|
| LangSmith | 5K traces, 1 seat, 14-day retention | Trace volume + multi-user | $39/seat/month (Plus) | Per-seat |
| Langfuse | 50K units, 2 users (cloud); unlimited (self-hosted) | Volume or >2 users | $8/100K units overage | Usage-based |
| Helicone | 10K requests/month | Request volume or team compliance | $20/seat or $200/team flat | Hybrid |
| Portkey | 10K logged requests | Feature gating (caching, alerts) | ~$99+/month (Pro) | Feature-gated |
| Sentry | 5K errors, 1 user | Error volume + multi-user | $26/month (Team) | Volume-based |
| PostHog | 1M events, unlimited users | Volume or compliance (SSO, RBAC) | Usage-based; Enterprise $2K/month | Volume-based |

### Key Patterns From Research

1. **Every successful dev tool gives away core functionality.** Sentry, PostHog, Langfuse, Helicone, Grafana — the free tier is genuinely useful. Feature-gating the primary value kills adoption.
2. **The upgrade trigger is always one of three things:** (a) volume/quota exhaustion, (b) multi-user/team collaboration, or (c) compliance/governance (SSO, audit logs).
3. **Per-seat pricing creates adoption friction.** Sentry and PostHog moved away from it. Flat team pricing or usage-based is better for developer tools.
4. **Infrastructure cost for calibration data is minimal.** ~$5/month for dozens of teams (small JSON payloads, serverless compute). This is not a cost-driven pricing problem.

### Staged Pricing Approach

**Phase 1-2 (now → month 10): GitHub Sponsors as bridge**
- Enable GitHub Sponsors immediately ($5, $15, $50/month tiers)
- Zero infrastructure cost, zero billing complexity
- Honest framing: "Sponsoring funds team features"
- Not a permanent strategy — a signal-gathering mechanism while building Phase 3

**Phase 3 (month 10+): Flat Team Tier**

| Tier | Price | Includes |
|------|-------|---------|
| **Individual** (free forever) | $0 | Full estimation engine, local calibration learning, MCP server, SKILL.md companion, CLI, GitHub-based team sharing, status dashboard |
| **Team** | $49/month flat (unlimited seats, up to 10 projects) | Web dashboard, server-side factor computation, cross-team benchmarks (anonymized, opt-in), API access, priority GitHub issues |
| **Team+** | $99/month flat (unlimited seats, unlimited projects) | Everything in Team + cross-project rollup, custom calibration rules, export/import |

**Why flat team pricing (not per-seat):**
- $49/month is expensable by an engineering manager without procurement
- "Unlimited seats" removes adoption friction within a team
- For a team of 10, that's $4.90/engineer/month — comparable to Helicone, cheaper than LangSmith
- For a team of 50, that's $0.98/engineer/month — negligible
- Covers infrastructure costs with margin (serverless + managed DB for 50 teams ≈ $50-100/month)

**What's explicitly NOT paid:**
- The estimation engine itself — always free, always open source
- Local calibration learning — always works offline, always gets smarter
- MCP server and SKILL.md — free distribution, free forever
- GitHub-based team sharing — free tier of collaboration (Phase 2)
- CLI and PyPI package — free, no IDE dependency

**The upgrade trigger is natural:** "I want a dashboard my team can see, and I want the server to compute factors from our combined data." Not: "I want the tool to work at all."

### Billing Infrastructure

For a solo developer, billing complexity matters:
- **Lemon Squeezy or Paddle** over Stripe — handles VAT compliance automatically, lower maintenance than raw Stripe Billing
- Flat pricing eliminates metering complexity entirely
- Subscription lifecycle (create, upgrade, cancel) is minimal with only 2 paid tiers

### Pricing Validation (Phase 1.5)

Before building any paid features:
- In the "Share with team" waitlist, ask: "Would you pay $49/month for a hosted team dashboard with shared calibration?"
- In direct outreach interviews, ask: "What do you pay for LLM observability today? Would cost estimation be worth $49/month on top of that?"
- Track the delta between "I'd use this for free" and "I'd pay for this" — if they're the same group, the free tier boundary is wrong
- If 3+ waitlist signups indicate willingness to pay, the pricing hypothesis is validated enough to build

---

## Shared Calibration: Hypothesis, Not Assumption

The original strategy treated shared calibration as an axiom: "calibration data is more valuable when shared." The adversarial review identified this as the most dangerous untested assumption.

**The problem:** Calibration factors are derived from workflow-specific ratios. A team doing Rails bug fixes has calibration data that may be actively harmful to a team doing greenfield Go microservices. Per-signature factors help within a team, but cross-team pooling may require similarity matching that's significantly more complex than simple aggregation.

**How to test (Phase 1.5, Experiment 5):**

1. Take your existing calibration history (30+ sessions across v1.2-v2.0)
2. Generate 5 synthetic "teams" by varying:
   - Workflow profile (bug fix, greenfield, refactoring, documentation, mixed)
   - Pipeline shape (3-step simple, 7-step full, parallel vs sequential)
   - Model mix (Opus-heavy, Sonnet-only, mixed)
3. For each synthetic team, compute local-only factors
4. Pool all synthetic teams' histories; compute pooled factors
5. Measure per-team estimation accuracy with local-only vs pooled factors
6. Repeat with 10, 25, 50 synthetic teams

**Success criteria:** Pooled factors improve per-team accuracy by >10% for at least 60% of synthetic teams. If pooled factors degrade accuracy for >30% of teams, the shared calibration hypothesis needs revision — perhaps to per-signature sharing (only share factors for matching workflow signatures) rather than global pooling.

**If the hypothesis fails:** The team product becomes about **team visibility** (dashboard, GitHub Action, shared config) rather than shared calibration intelligence. This is still valuable — but it changes the pricing story from "our data makes your estimates better" to "our dashboard makes your estimates visible."

---

## Risk Register

| # | Risk | Probability | Impact | Mitigation |
|---|------|-------------|--------|------------|
| 1 | Anthropic ships native cost estimation | 40% | HIGH | MCP-first distribution already reduces dependency. Calibration sophistication is the differentiator. If both fail, project stays OSS. |
| 2 | LangSmith ships cost forecasting to 250K users | 30% | HIGH | Multi-framework + calibration accuracy moat. LangSmith is LangChain-coupled; tokencast via MCP is framework-agnostic. |
| 3 | Nobody wants pre-execution estimates (they want budget alerts) | 35% | CRITICAL | Phase 1.5 direct outreach tests this explicitly. If true, pivot to cost-control tooling (budget alerts, spend caps, anomaly detection). |
| 4 | Shared calibration degrades accuracy | 50% | HIGH | Phase 1.5 simulation tests this before any backend investment. Fallback: team value comes from visibility, not shared intelligence. |
| 5 | Solo developer burnout / loss of interest | 30% | CRITICAL | Go/no-go gates at every phase. No sunk-cost pressure to continue if validation fails. The project is valuable as OSS regardless of enterprise ambitions. |
| 6 | MCP ecosystem doesn't grow as expected | 15% | MEDIUM | SKILL.md companion ensures Claude Code users are served. PyPI/CLI distribution doesn't depend on MCP. |
| 7 | 10hrs/week is insufficient for Phase 2+ | 60% | MEDIUM | Phase 2 is designed for minimal infrastructure (GitHub-based, not hosted). Phase 3+ can be deferred or attract contributors. |

---

## What's Different From the Original Strategy

| Original | Revised | Why |
|----------|---------|-----|
| Claude Code skill as "wedge" | MCP server as primary distribution | 9/10 viability vs 6/10; multi-framework from day one; eliminates platform dependency |
| Multi-framework deferred to Phase 3 | Multi-framework from Phase 1b (via MCP) | Anthropic contingency; the moat is cross-framework, not Claude-Code-specific |
| 9-18 month timeline | 18-36+ month timeline | Honest about 10hrs/week side project reality |
| Shared calibration as axiom | Shared calibration as testable hypothesis | Adversarial review flagged this as most dangerous untested assumption |
| No customer validation | Phase 1.5 market validation sprint (5 experiments) | Zero outside validation exists; must test before building |
| Hosted backend in Phase 2 | GitHub-based sharing in Phase 2; hosted backend deferred to Phase 3 | Solo developer cannot operate a hosted service at Phase 2 scale |
| No pricing model | Per-team pricing hypothesis ($15-25/team/month) with validation plan | Strategy without pricing is incomplete |
| No go/no-go gates | Explicit gates with measurable thresholds at every phase transition | Prevents sunk-cost-driven commitment to a failing strategy |
| "Architecture already aligned" | Honest 3/6 alignment score | calibration_store.py is a file wrapper; attribution is Claude-Code-coupled |
| 4 distribution options (A/B/C) | 6 channels: MCP, SKILL.md, PyPI, GitHub Action, web dashboard, community | Distribution researcher identified viable channels the original missed |
| Enterprise as Phase 4 destination | Enterprise as conditional outcome requiring co-founder/contributors | Solo developer should not attempt enterprise without help |

---

## Timeline Summary

```
Phase 1: Complete Product + MCP Distribution
├── 1a: Ship v2.0.0                          Now → Month 1
├── 1b: Build MCP server                     Month 1 → Month 2.5
└── 1c: Decouple attribution from Claude     Month 2 → Month 3

Phase 1.5: Market Validation Sprint           Month 3 → Month 4
├── Exp 1: Opt-in telemetry
├── Exp 2: Team sharing waitlist
├── Exp 3: Community distribution (HN, Reddit)
├── Exp 4: Direct outreach (10 engineers)
└── Exp 5: Shared calibration simulation
    │
    ├── GATE NOT MET → Stay open source. Revisit in 6 months.
    │
    └── GATE MET → Phase 2

Phase 2: Team Features (GitHub-based)         Month 5 → Month 10
├── GitHub-based calibration sharing
├── GitHub Action for PR cost estimates
├── PyPI package + CLI
└── Pricing validation
    │
    ├── GATE NOT MET → Stay open source with team sharing.
    │
    └── GATE MET → Phase 3

Phase 3: Hosted Backend + Revenue             Month 10 → Month 18
├── Web dashboard
├── Server-side factor computation
├── Cross-team benchmarks
└── API access
    │
    ├── GATE NOT MET → Profitable niche tool. Good outcome.
    │
    └── GATE MET + CO-FOUNDER → Phase 4

Phase 4: Enterprise (requires additional help) Month 18+
├── SSO/SAML
├── Audit logging
├── Cost allocation
└── Data residency
```

---

*This strategy is designed to be validated at every step. No phase commits resources to the next phase's infrastructure. Each gate is a genuine decision point where "stop here" is a respectable outcome. The goal is not to build an enterprise product — it's to build something useful, find out if others agree, and let the market pull the product forward.*
