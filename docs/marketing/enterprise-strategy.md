# tokencast — Enterprise Strategy

*Written: 2026-03-22. This is a strategic planning document, not a product spec. Decisions about priorities, timing, and distribution model should be revisited as the market develops.*

---

## The Opportunity

No tool exists today that gives engineering teams predictive cost estimation for LLM agent workflows — before execution — and then learns from actual results to improve those estimates over time.

The closest things:
- **Token counters and loggers** (OpenAI usage dashboards, LangSmith, Langfuse): record what happened, no pre-execution estimates
- **Budget caps** (Anthropic's max_tokens, LiteLLM spending limits): stop runaway costs, don't predict them
- **Spreadsheet models**: manual, not integrated, don't learn

tokencast closes a gap that will only widen as agentic workloads become the norm. The question is whether to stay a personal tool or build toward the infrastructure that makes this valuable at enterprise scale.

The window is real but not permanent. Framework vendors (LangChain, CrewAI, AutoGen, Vercel AI SDK) will eventually build something like this natively. First movers who establish calibration data and user trust will be hard to displace — but only if the network effects materialize before the window closes.

---

## Current State

**v1.6.0** — personal Claude Code skill, single-user, local calibration.

What works:
- Pre-execution cost estimates with three bands (optimistic, expected, pessimistic)
- Calibration learning from completed sessions (global → size-class → per-step → per-signature factors)
- Time-decay weighting so recent sessions influence estimates more than old ones
- Mid-session cost warnings when spend approaches pessimistic threshold
- Backward compatible: old sessions don't break new features

What doesn't exist yet:
- Per-agent step actual cost attribution (v1.7 — in progress)
- Cost management dashboard with actionable recommendations (v2.0 — in progress)
- Multi-user or multi-team support
- Any backend — everything runs on local disk
- Framework support beyond Claude Code

The architecture decisions already made point toward enterprise — schema versioning, configurable agent mapping, calibration storage abstraction — but the infrastructure isn't built yet.

---

## The Path from Here to Enterprise

### Phase 1: Finish the Core Product (v1.7 + v2.0)
*Now → ~4–6 weeks*

Ship what's been planned:
- v1.7: True per-agent step actual cost attribution via hook-based sidecar timeline
- v2.0: `/tokencast status` dashboard — accuracy trends, cost attribution by step, actionable recommendations

Why this comes first: these features make tokencast a complete product for individual engineers. You can't sell a team product until the single-user product is excellent. v2.0 is also the feature that surfaces value explicitly (the dashboard), which is what converts users from "interesting experiment" to "this is part of my workflow."

At the end of Phase 1, tokencast is a complete tool for a sophisticated individual user who builds on Claude Code.

### Phase 2: Shared Calibration (v2.5 or v3.0)
*~2–4 months after Phase 1*

The core insight: calibration data is more valuable when shared. An individual engineer running 10 sessions per month gets a mediocre calibration signal. An enterprise team running 200 sessions per month gets a strong one. And different teams have different workflow profiles — a team that ships primarily bug fixes has different per-step ratios than a team doing greenfield architecture.

Shared calibration requires:
- A backend that stores history.jsonl and factors.json per team
- Authentication so team data stays scoped to the team
- A thin client that reads factors from the backend instead of local disk

The calibration_store.py abstraction (part of v1.7 design) is the seam for this swap. Today it reads local disk. In Phase 2, it reads from an API endpoint. The local client code doesn't change — only calibration_store.py.

What this unlocks:
- Bootstrap the calibration faster (team data > individual data)
- Cross-team factor sharing for similar workflow signatures (opt-in)
- Factor computation happens server-side, not in each local update-factors.py run

### Phase 3: Multi-Framework Support
*~1–2 months after Phase 2*

Claude Code is the wedge but not the limit. Enterprise engineering teams use multiple orchestration frameworks simultaneously. A team might run some workflows in Claude Code, some in LangChain, some in custom Python. To be the cost management layer across an enterprise's LLM spend, tokencast needs adapters for:

1. **LangChain / LangGraph** — callback handlers to emit sidecar events
2. **CrewAI** — agent lifecycle hooks
3. **AutoGen** — message interceptors
4. **Vercel AI SDK** — middleware or step hooks
5. **Custom Python** — a logging decorator or context manager

The sidecar event schema (schema_version=1, extensible JSON lines) was designed to be framework-agnostic. Any framework that can write JSON to a file can produce sidecar events. The analysis layer (status.py, update-factors.py) only reads the sidecar — it doesn't care how it was produced.

The effort per framework is primarily documentation and example code, not new core logic. The core doesn't change.

### Phase 4: Enterprise Tier
*~3–6 months after Phase 3*

Enterprise requirements beyond what Phase 2/3 deliver:
- **SSO and audit logging**: SAML/OIDC identity, exportable audit trail of who ran what and what it cost
- **Data residency**: EU teams need EU-region data storage; enterprise buyers block cross-border data movement
- **Cost allocation and reporting**: tie LLM spend to projects, teams, cost centers — what finance needs
- **SLA and support contracts**: uptime guarantees, dedicated support channel, security questionnaires
- **Custom calibration factor curation**: enterprise teams may want to review and approve calibration changes before they affect estimates (especially for regulated industries where "the estimate changed" has compliance implications)

None of these require rethinking the core product. They're operational capabilities layered on top of the Phase 2/3 backend.

---

## The Bootstrap Problem

Shared calibration creates network effects — but only after enough teams are contributing data. The threshold where shared calibration beats local-only calibration is roughly **50–100 active teams**, where "active" means 10+ sessions per month.

Below that threshold, shared calibration may be worse than local calibration because the shared pool contains workflow profiles that don't match yours. Above it, the shared pool becomes a strong prior that accelerates calibration for new teams.

How to get to 50–100 teams:
1. **Claude Code community first**: Engineers who are already building on Claude Code are the most receptive early adopters. They understand the problem viscerally.
2. **Open source the client**: The local-disk version (v1.7 + v2.0) is open source. This is free marketing. Engineers who adopt it and build calibration data locally are the natural upsell for shared calibration.
3. **Team invitations**: When an individual user wants to share their calibration with their team, that's the conversion moment. Make this the obvious next step after Phase 1.
4. **Starter calibration**: Publish anonymized, aggregated benchmark calibration data for common workflow patterns (e.g., "the typical 5-step feature development pipeline on Sonnet runs 1.3× the estimate"). New teams can start with these priors rather than with a blank slate.

The bootstrap problem is real but solvable with a community-first launch strategy. The key is that Phase 1 (the local product) must be genuinely excellent before asking anyone to pay for the team product.

---

## Three Distribution Options

### Option A: Open Source + Hosted Calibration Tier
**Model**: Client is fully open source (MIT or Apache 2). The calibration backend is a hosted service. Local-only use is always free. Shared calibration is the paid tier.

**Precedents**: Sentry (open source error tracking + hosted service), PostHog (open source analytics + cloud tier), Grafana (open source dashboards + Grafana Cloud).

**Advantages**:
- Maximum adoption of the open source client drives bottom-up awareness
- Engineers trust open source tools more than black boxes — they can audit the estimation algorithm
- Forking the backend is theoretically possible but practically hard (who wants to host their own calibration service?)
- Community contributions to heuristics and framework adapters

**Disadvantages**:
- Revenue comes only from teams that opt into shared calibration — free riders can stay free forever
- Enterprise buyers sometimes prefer proprietary (perceived as "more supported")
- Maintaining open source community expectations is real work

**When this works best**: When adoption by individual engineers is the primary growth driver, and enterprise sales follow community traction.

### Option B: API-First (Estimation Engine as a Service)
**Model**: The estimation engine is an API. Frameworks call the API at plan time to get cost estimates. Calibration is server-side. No local client code required — just an HTTP call.

**Precedents**: OpenAI API (call → response), Stripe (integrate via API, not client library), SendGrid (email via API).

**Advantages**:
- Framework integrations become simple (add one API call)
- Pricing is straightforward (per-request or per-seat)
- Central data — all estimation and calibration happens server-side, making iteration faster
- Works for any programming language, any framework

**Disadvantages**:
- Adds network latency to the pre-plan estimation step (acceptable if async, but noticeable if synchronous)
- Requires an always-on service from day one — no "local first" option
- Higher infrastructure cost and operational complexity earlier
- Privacy concern: teams may not want to send plan details to an external API

**When this works best**: When targeting developer platform teams who are comfortable with API integrations and want minimal local setup.

### Option C: Claude Code Skill as Wedge + Hosted Backend as Upsell (Current Path, Natural Expansion)
**Model**: The Claude Code skill remains the primary client. Phase 2 adds a backend that the skill can optionally connect to for shared calibration. Enterprise features live in the hosted tier.

**Precedents**: Linear (started as a better Jira for individuals, expanded to team features), Notion (individual use → team workspaces → enterprise).

**Advantages**:
- Natural growth path — individual adoption is already happening
- No infrastructure required until Phase 2
- Users who adopt v1.7/v2.0 as a free tool self-select for the paid tier upgrade
- Framework adapters in Phase 3 expand the reach without changing the core model

**Disadvantages**:
- Claude Code is one framework among many — limiting if enterprise buyers need multi-framework from day one
- The "skill" form factor may not fit enterprise procurement (enterprises buy products, not Claude Code plugins)
- Competitive risk: Anthropic could build this natively into Claude Code

**When this works best**: When the Claude Code user base is large enough to generate sufficient early adopters, and when the expansion to other frameworks happens before the window closes.

---

## Recommendation

**Option C is the right starting point, with Option A layered on top of it by Phase 2.**

The reasoning:

tokencast already exists as a Claude Code skill. The user base is real. Phase 1 (v1.7 + v2.0) completes the product without requiring any infrastructure decisions. This is the lowest-risk path to having a finished, excellent product.

When Phase 2 begins (shared calibration), open-source the client and introduce a hosted backend. This gives the project Option A's community dynamics — open source client builds trust and adoption, hosted backend is the revenue model — without abandoning the Claude Code install base.

By Phase 3 (multi-framework), the hosted backend is established and framework adapters are distribution channels into new user communities (LangChain users, CrewAI users, etc.). Option B (pure API) becomes an option for enterprises that don't want a local client at all.

The transition is:
```
Phase 1: Claude Code skill, local only (Option C, current path)
Phase 2: Claude Code skill + open source client + hosted calibration backend (Option A hybrid)
Phase 3: + framework adapters (Option B available to enterprises)
Phase 4: Enterprise tier on top of Phase 3
```

This path doesn't require betting on one distribution model. It starts where the users are (Claude Code), opens to the community (open source), and expands to enterprise without throwing away what's been built.

---

## Architecture Decisions Already Aligned with This Path

Several design decisions in v1.7 were made with enterprise in mind. For reference:

| Decision | Why It Matters for Enterprise |
|----------|-------------------------------|
| `calibration_store.py` storage abstraction | Swap local disk for remote API in one file |
| `agent-map.json` configurable agent mapping | Enterprise teams use non-standard agent names |
| Sidecar schema_version=1 as API contract | Downstream consumers (dashboards, CI systems) can parse stably |
| JSON output schema_version=1 in status.py | Same — stable API for enterprise integrations |
| Framework-agnostic sidecar format | Any framework can write sidecar events |
| `--json` flag in /tokencast status | Agents and CI pipelines can consume structured output |

None of these add complexity today. They're the minimum viable future-proofing — present in the code, invisible to current users, load-bearing if the enterprise path materializes.

---

## What Needs to Be True

For this strategy to succeed, a few things need to hold:

1. **Phase 1 must be excellent before Phase 2.** If the individual product doesn't deliver clear value, there's no community to grow from. Ship v1.7 + v2.0 with polish.

2. **The window must not close too fast.** If LangChain or Anthropic ships native cost prediction before Phase 2 is live, the opportunity shrinks. Monitor competitive signals.

3. **Network effects must be real, not assumed.** The hypothesis is that shared calibration across 50+ teams beats local calibration. This needs to be validated with real data before committing to the hosted backend as the core product.

4. **Open source must be genuine.** If the open source client is crippled or the license is restrictive, the community doesn't form. The estimation algorithm and local calibration must be fully open.

5. **The transition from individual to team product must feel natural.** The prompt "share your calibration with your team" has to appear at the right moment — after a user has enough calibration data to see its value, and before they've given up on it.

---

*This document captures the strategic framing as of 2026-03-22. Revisit after Phase 1 ships to validate the assumptions with real user feedback before committing to Phase 2 infrastructure investment.*
