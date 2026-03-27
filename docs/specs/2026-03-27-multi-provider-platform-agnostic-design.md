# tokencast Phase 2: Multi-Provider & Platform-Agnostic Design

*Date: 2026-03-27*
*Author: Brainstorming session (Kelly + Claude)*
*Status: Draft — pending adversarial review*

---

## Problem Statement

tokencast currently couples to Anthropic models (pricing) and Claude Code (pipeline steps, session JSONL, hook system). The MCP server shipped in Phase 1 is API-agnostic, but the data layer underneath is not. Non-Anthropic users calling `estimate_cost` get wrong numbers. Non-Claude-Code users can't feed actuals back for calibration without JSONL.

## Goal

Full provider + platform agnosticism. Any MCP client, any LLM provider, any pipeline shape.

## Phasing

| Phase | What | Unlocks |
|-------|------|---------|
| **2a** | Multi-provider pricing tables + auto-fetch + `model` parameter | Users on OpenAI/Google/mixed-model workflows get accurate estimates |
| **2b** | Configurable step taxonomy + pipeline templates | Users define their own pipeline shapes and model assignments |
| **2c** | MCP-first learn path — migrate shell hooks to MCP client shims | Single code path for calibration; removes JSONL parsing dependency |

---

## Phase 2a: Multi-Provider Pricing

### Model ID Format

`"provider/model-name"` — e.g., `"openai/gpt-5.4"`, `"anthropic/claude-sonnet-4-6"`, `"google/gemini-2.5-pro"`.

**Backward compatibility:** Bare model names without a slash (e.g., `"claude-sonnet-4-6"`) resolve to `"anthropic/"` prefix automatically.

### Built-in Tier 1 Providers (day-one)

| Provider | Models |
|----------|--------|
| Anthropic | Claude Opus 4.6, Sonnet 4.6, Haiku 4.5 |
| OpenAI | GPT-5.4, o3, o4-mini |
| Google | Gemini 2.5 Pro, Gemini 2.5 Flash |

### Auto-Fetch Pricing

- **Source:** LiteLLM's `model_prices_and_context_window.json` (MIT license, community-maintained, 300+ models)
- **Trigger:** First run, or when cached pricing is >24h stale
- **Cache:** `calibration/pricing-cache.json` (gitignored)
- **Fallback:** Built-in defaults if fetch fails (offline, network error, LiteLLM unavailable)
- **Non-blocking:** Fetch never blocks estimation — use stale cache and warn in output

### Precedence Chain

```
per-call model override > calibration/pricing-overrides.json > cached fetch > built-in defaults
```

### API Changes to `estimate_cost`

New optional parameters:

| Param | Type | Description |
|-------|------|-------------|
| `default_model` | string | Override built-in default model (e.g., `"openai/gpt-5.4"`) |
| `step_models` | dict | Per-step model assignments (e.g., `{"Implementation": "openai/gpt-5.4"}`) |

Existing `steps` array format unchanged. Models resolved via: per-step override > provider profile > default_model > built-in.

### Provider Profile

Optional persistent config at `calibration/provider.json` (gitignored):

```json
{
  "default_model": "openai/gpt-5.4",
  "step_models": {
    "Implementation": "openai/gpt-5.4",
    "Research": "anthropic/claude-opus-4-6"
  }
}
```

### `pricing.py` Changes

- `MODEL_PRICES` becomes a registry lookup: check cache → check built-in → warn on unknown model
- New function: `resolve_model_price(model_id)` walks the precedence chain
- `compute_cost_from_usage(usage, model)` unchanged — just needs pricing data for the model
- New function: `fetch_pricing_cache(force=False)` handles LiteLLM fetch + local caching

### Deliverables

- `references/pricing.md` expanded with OpenAI + Google sections
- `src/tokencast/pricing.py` refactored: registry-based lookup, `resolve_model_price()`, LiteLLM fetch + cache
- `calibration/pricing-cache.json` (auto-fetched, gitignored)
- `calibration/pricing-overrides.json` (user-defined, gitignored)
- `estimate_cost` MCP tool: new optional params `default_model`, `step_models`
- `calibration/provider.json` (optional persistent config, gitignored)
- Tests for multi-provider pricing, fetch fallback, precedence chain

---

## Phase 2b: Configurable Step Taxonomy

### Freeform Steps

Users pass arbitrary step names to `estimate_cost` and `report_step_cost`. tokencast treats them as opaque strings. No whitelist, no validation.

### Step Categories

For first-run heuristics (when no calibration data exists), steps are assigned a category:

| Category | Default token budget | Typical steps |
|----------|---------------------|---------------|
| `planning` | High read, low edit | Research, architecture, requirements, brainstorming |
| `execution` | High read + edit | Implementation, coding, generation |
| `review` | High read, low edit | Code review, QA, testing |
| `mechanical` | Low read + edit | Docs, formatting, cleanup |

Steps are categorized by an explicit `category` field in the step definition, or inferred from name heuristics (e.g., step name contains "review" → review category).

### Pipeline Templates

JSON files defining step sequences with categories, default models, and token budgets.

**Shipped with PyPI package** (`references/pipeline-templates/`):

| Template | Steps | Source |
|----------|-------|--------|
| `brainstorming.json` | Context Exploration → Clarifying Q&A → Approach Proposal → Design Presentation → Design Doc Write → Spec Review | Superpowers brainstorming skill |
| `tdd-cycle.json` | Test Writing → Implementation → QA | Superpowers TDD skill |
| `review-loop.json` | PR Review Loop (geometric decay) | Superpowers code review skill |
| `single-step.json` | One step, one model | Generic single-agent workflows |

**Local-only templates** (`calibration/pipeline-templates/`, gitignored):
- Users drop custom templates here
- Local templates override shipped ones with the same name

**Template usage:**
- `estimate_cost(template="brainstorming")` loads the template
- `estimate_cost(steps=[...])` overrides any template
- Templates define step names, categories, default models, and token budgets

### Backward Compatibility

- Existing `steps` array format unchanged
- Existing canonical step names ("Research Agent", "Implementation", etc.) continue to work
- `step_names.py` alias resolution extended to map template step names to canonical names
- Claude Code's SKILL.md continues to use its existing step names implicitly

### Deliverables

- `references/pipeline-templates/` with 4 shipped templates
- `calibration/pipeline-templates/` directory (gitignored) for local templates
- `estimate_cost` MCP tool: new optional param `template`
- Step category system with default budgets per category
- `step_names.py` extended for freeform names + category inference
- Tests for template loading, freeform steps, category fallback

---

## Phase 2c: MCP-First Learn Path

### Current State

Calibration learning works via one path: `learn.sh` reads Claude Code's session JSONL → `sum-session-tokens.py` parses it → writes to `history.jsonl`. This is Claude Code-only.

The MCP API already has the model-agnostic alternative: `report_step_cost()` + `report_session()`. Any MCP client can report costs directly without JSONL parsing.

### Migration: Shell Hooks Become MCP Client Shims

Instead of maintaining two parallel learn paths, migrate Claude Code's shell hooks to call MCP tools:

| Hook | Current behavior | After migration |
|------|-----------------|----------------|
| `learn.sh` (Stop) | Parse JSONL → compute actuals → write history | Call `report_session` via MCP |
| `agent-hook.sh` (PreToolUse/PostToolUse) | Write sidecar timeline JSONL | Call `report_step_cost` via MCP |
| `midcheck.sh` (PreToolUse) | Parse JSONL → compute spend → warn | Call `get_calibration_status` via MCP |

The shell scripts don't disappear — they become thin MCP client wrappers. Same hook triggers, same user experience, but business logic lives in one place (the Python API).

### Legacy Retention

- `sum-session-tokens.py` retained for reading historical calibration data only
- No migration tool needed — old `history.jsonl` records remain valid as-is
- New records gain optional `provider` field

### Deliverables

- Shell hooks migrated to MCP client shims
- `sum-session-tokens.py` marked as legacy (historical data reader only)
- Documentation: integration guides for non-Claude-Code platforms
- Example MCP client integration showing the full lifecycle
- Validation: MCP learn path produces identical calibration records to shell path

---

## Backward Compatibility

**Zero breaking changes.** Every existing API call, calibration file, and hook continues to work unchanged.

- Bare model names (`"claude-sonnet-4-6"`) resolve to `"anthropic/claude-sonnet-4-6"` automatically
- Existing `history.jsonl` records remain valid — new records add optional `provider` field
- `factors.json` structure unchanged — per-step factors keyed by canonical step names
- `active-estimate.json` gains optional `provider_profile` field (`.get()` default for old files)
- SKILL.md continues to work as-is for Claude Code users — it implicitly uses Anthropic defaults
- Existing shell hooks continue to function during migration period (Phase 2c)

---

## What We're NOT Building

- No web dashboard or hosted service
- No real-time pricing API (fetch is cached daily, not live)
- No automatic platform detection (users specify their setup)
- No Tier 2 provider pricing at launch (xAI, DeepSeek, Mistral — backlog)
- No platform-specific adapters beyond Claude Code (Cursor, Windsurf — only when demand exists)
- No migration tool for existing calibration data (old records stay valid as-is)

---

## Backlog (Not in Phase 2)

### Tier 2 Provider Pricing
- xAI (Grok 3)
- DeepSeek (V3, R1)
- Mistral (Large, Medium)
- Amazon Bedrock (cross-provider routing)
- Azure OpenAI (same models, different pricing)

### Platform-Specific Learn Adapters
- Cursor cost log parser
- Windsurf session data adapter
- VS Code Copilot usage API adapter
- Generic webhook adapter (accept cost data via HTTP POST)

### Pipeline Template Expansion
- Templates contributed by community (Cursor workflows, Windsurf workflows)
- Template marketplace / registry
- Auto-detect pipeline shape from session history (workflow fingerprinting — v3.0 roadmap)

### Pricing Intelligence
- Price change notifications (alert when a model's pricing changes)
- Cost comparison across providers for the same task ("this would cost $X on OpenAI vs $Y on Anthropic")
- Automatic model substitution suggestions based on price/quality ratio (v4.0 roadmap)

### Advanced Calibration
- Cross-provider calibration transfer (if you switch from Claude to GPT, transfer learned step-level factors)
- Provider-specific cache efficiency tracking (different providers have different caching behavior)
- Multi-model session support (single session uses multiple models — track costs per model)

### MCP Server Enhancements
- `get_supported_models` tool — list all models with known pricing
- `get_pricing` tool — query current pricing for a specific model
- `compare_estimates` tool — estimate same task across multiple providers
- `suggest_models` tool — recommend models for a pipeline based on budget + quality requirements

### Community & Ecosystem
- Published pricing registry (npm package or hosted JSON) for other tools to consume
- OpenTelemetry integration for cost attribution
- CI/CD cost budgets (fail builds that exceed estimated cost thresholds)
- GitHub Actions integration for PR cost annotations

---

## Open Questions for Adversarial Review

1. **LiteLLM dependency risk** — What if LiteLLM changes license, goes unmaintained, or changes JSON schema? Is the fallback-to-built-in strategy sufficient, or do we need a second pricing source?

2. **Fetch privacy** — Auto-fetching from GitHub on first run sends a network request. Should this be opt-in? Some enterprise environments block outbound requests.

3. **Step category inference** — Inferring category from step name ("review" → review) is heuristic. How wrong can this get? Should we require explicit categories for non-template steps?

4. **MCP migration risk** — Converting learn.sh to an MCP client shim means the MCP server must be running for calibration to work. Currently learn.sh is self-contained. Is this an acceptable tradeoff?

5. **Pricing staleness** — 24h cache TTL means prices could be wrong for up to a day after a provider changes pricing. Is this acceptable? Should users be able to configure the TTL?

6. **Template proliferation** — How do we prevent template sprawl? Should templates be versioned?
