# Enterprise Strategy — Adversarial Review Questions

*Generated: 2026-03-23 from adversarial review by Sr PM, Architect, Staff Engineer, Devil's Advocate, and two research agents.*

*Context: These questions emerged from a six-agent adversarial review of `docs/enterprise-strategy.md`. The full critique report and competitive research are available in the conversation history. Your answers will be used to produce a revised enterprise strategy document.*

---

## Must-Answer (blocks the revised strategy)

### Q1: Who is building this?

How many engineers, with what skills, starting when? If the answer is "me + Claude Code," the timeline and scope must reflect that honestly. The staff engineer estimates that's 2-3 years for all four phases, not 9-18 months.

**Your answer:** it's just me and claude




---

### Q2: Have you validated that anyone will pay for pre-execution cost estimation?

Have you talked to potential customers outside your own usage? The devil's advocate frames it sharply: is the actual user need "cost estimation" or "cost control"? If engineers just want budget alerts (which v1.6 mid-session warnings already do), the enterprise thesis is solving the wrong problem. What evidence do you have of demand beyond your own experience?

**Your answer:** No there has been no outside customer validation




---

### Q3: What is the Anthropic contingency?

If Anthropic ships native cost tracking in Claude Code next quarter, what happens? Pivot to multi-framework only? Shut down? Double down on calibration accuracy as the differentiator? The competitive researcher confirmed Anthropic has the session data, model pricing, and distribution to replicate basic estimation in weeks. This needs a written plan.

**Your answer:** Multi-framework is the only moat here, so that should be the goal from the start




---

### Q4: Is shared calibration actually better than local?

Before building any backend, can you simulate this? Take your existing calibration history, split into synthetic teams with different workflow profiles, pool their factors, and measure whether pooled factors improve or degrade per-team accuracy. If mixed-profile data hurts accuracy, the Phase 2 value proposition collapses. The devil's advocate and architect both flagged this as the most dangerous untested assumption.

**Your answer:** I have no idea how we would test this, but open to ideas




---

### Q5: Why isn't MCP the primary distribution channel?

The distribution researcher rated MCP server at 9/10 viability (vs Claude Code skill at 6/10). MCP is Linux Foundation governed, adopted by OpenAI/Google/Anthropic, with 97M+ monthly SDK downloads and 5,800+ servers. It reaches ALL LLM clients from day one without platform lock-in. The current strategy doesn't mention MCP at all. Is there a reason, or is this an oversight?

**Your answer:** it seems like this should be an MCP server with a hosted backed with additoonal features. Thoughts?




---

## Should-Answer (shapes the strategy significantly)

### Q6: What's the minimum viable Phase 2?

Could it be as simple as "upload factors.json to a shared bucket, download the team's merged version"? Ugly but shippable in weeks. The architect estimates the full backend vision (auth, API, tenant isolation, factor recomputation) at 8-12 weeks with a team, far longer solo. What's the smallest thing that tests the shared-calibration hypothesis?

**Your answer:** The fastest option that I can send to other teams that is not an undue burden on those teams (the value is larger than the friction)




---

### Q7: What is the pricing hypothesis?

Per-seat? Per-estimate? Usage-based? Flat team tier? Even before building anything, you can test willingness-to-pay with a waitlist CTA in the v2.0 dashboard. Back-of-envelope: what does the infrastructure cost for 50 teams, and what price point covers it?

**Your answer:** I don't know, we'll need to have more research completed here. Maybe an agent can survey what other companies do to get an idea of current options




---

### Q8: What are the go/no-go criteria between phases?

The Sr PM recommends explicit gates, e.g.: "Proceed to Phase 2 if: (a) 500+ active installs, (b) >60% of users with 10+ sessions show accuracy within 1.5x, (c) 3+ inbound requests for team sharing." What are your measurable success criteria for each phase transition?

**Your answer:** I'd like you to propose some options here




---

## Worth Considering

### Q9: Should you insert a Phase 1.5 Market Validation Sprint?

2-4 weeks: instrument v2.0 with opt-in usage telemetry, add a "Share with team" waitlist button, do 10 user interviews with engineers who have 20+ calibrated sessions. The Sr PM argues the cost is negligible compared to building a hosted backend nobody wants.

**Your answer:** yes - we should expand on this as well - we need multiple ideas to test market validation




---

### Q10: Would a GitHub Action create a better viral loop?

A GitHub Action that posts cost estimates as PR comments would make tokencast visible to the whole team on every plan PR — no individual install required. The distribution researcher rated this 8/10 viability. The Claude Code skill is invisible to teammates; a PR comment is not.

**Your answer:** This would be fine for non-public repos, abut would have to go alongside a web-based dashboard feature 




---

## Key Research Findings (for reference while answering)

**Competitive landscape:** No production tool does pre-execution estimation for multi-step agent workflows. PreflightLLMCost (MIT, solo project) does single-call prediction. LangSmith ($1.25B valuation, 250K users, 1B traces) is the biggest threat — not for what it does today, but for what it could ship tomorrow. Portkey ($18M Series A, "AI FinOps") is the second-closest.

**Distribution research:** Claude Code has $2.5B ARR and strong developer sentiment, but the skill ecosystem is 5 months old with no evidence of third-party monetization. SKILL.md became an open standard (OpenAI adopted it for Codex CLI). MCP has 97M+ monthly SDK downloads under Linux Foundation governance. Sentry/PostHog/Grafana precedents show open-source → cloud works when the free tier is genuinely useful and the problem is universal.

**Architecture reality:** The architect estimates 19-28 weeks of engineering between current state and Phase 3 readiness. calibration_store.py is a file I/O wrapper, not a pluggable abstraction. The sidecar attribution pipeline is Claude-Code-coupled. The "already aligned with enterprise" table scores 3/6 genuinely aligned.
