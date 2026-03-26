# Cost Attribution

tokencast tracks actual API cost against estimates to improve accuracy over time. The method used depends on your client environment.

---

## Two Attribution Paths

### Claude Code (Sidecar + JSONL)

When running tokencast inside Claude Code, attribution happens automatically via hooks and session logs:

1. A `PreToolUse`/`PostToolUse` hook (`tokencast-agent-hook.sh`) fires on every Agent tool invocation. It records span start/stop events — including the JSONL file's current line count — to a sidecar timeline file (`calibration/{hash}-timeline.jsonl`).
2. At session end, the `Stop` hook (`tokencast-learn.sh`) reads the Claude Code session JSONL and the sidecar timeline together.
3. FIFO span matching correlates JSONL line ranges to named agent spans, attributing token costs to specific pipeline steps.
4. History records include `step_actuals: {step_name: float}` with per-step dollar costs.

This path requires no configuration and no action from you. It produces the highest-accuracy calibration data.

### MCP Clients (Tool-Call-Based)

For clients that don't produce Claude Code JSONL — Cursor, VS Code with Copilot, CI/CD pipelines, custom agents — tokencast uses a framework-agnostic attribution protocol based on MCP tool calls.

The client reports cost data directly to the tokencast MCP server using two tools:

- `report_step_cost` — report the cost of an individual pipeline step as it completes
- `report_session` — close the session and write a calibration history record

No JSONL. No hooks. No Claude Code required.

See [attribution-protocol.md](../attribution-protocol.md) for the full protocol specification.

---

## Tier 1 vs Tier 2

MCP clients choose a tier based on their ability to report per-step cost data.

### Tier 1 — Session-Level (always available)

```
estimate_cost(plan_params)
  [work happens in client framework]
report_session(actual_cost=X)
```

- The client reports only the total session cost at the end.
- tokencast uses proportional attribution: the session-level ratio (actual/expected) is applied to all steps uniformly.
- Attribution method in history records: `"proportional"`.
- Same accuracy as tokencast pre-v1.7. Calibration works — global and size-class factors update.
- Works with any MCP client regardless of whether it exposes per-step token data.

### Tier 2 — Step-Level (requires client cooperation)

```
estimate_cost(plan_params)
  [research agent completes]
report_step_cost("Research Agent", cost=1.20)
  [implementation agent completes]
report_step_cost("Implementation", cost=4.50)
  [session ends]
report_session(actual_cost=7.20)
```

- The client reports cost for each pipeline step as it completes.
- tokencast records actual per-step costs, identical in schema to Claude Code sidecar attribution.
- Attribution method in history records: `"mcp"`.
- Per-step calibration factors activate after 3+ sessions with step data for a given step.
- Requires the client to have access to per-step token counts or dollar costs.

### Accuracy Comparison

| Attribute | Claude Code Sidecar | Tier 2 MCP | Tier 1 MCP |
|-----------|---------------------|------------|------------|
| Per-step actuals | Yes | Yes | No |
| Per-step calibration factors | Yes | Yes | No |
| Global + size-class calibration | Yes | Yes | Yes |
| Setup required | Auto (hooks) | Client must call tools | Client must call `report_session` |
| Attribution method in history | `"sidecar"` | `"mcp"` | `"proportional"` |

Tier 1 and Tier 2 both produce valid calibration data. Tier 2 matches the per-step granularity of the Claude Code sidecar path.

---

## MCP Client Support

### Cursor

Cursor's agent mode exposes token usage per message via its extension API. A tokencast Cursor extension can read per-step token counts and call `report_step_cost` automatically, enabling Tier 2 attribution.

**Cursor configuration example:**

In your Cursor settings (`.cursor/mcp.json` or workspace settings), register the tokencast MCP server:

```json
{
  "mcpServers": {
    "tokencast": {
      "command": "python3",
      "args": ["-m", "tokencast_mcp"],
      "cwd": "/path/to/tokencast"
    }
  }
}
```

Once registered, call `estimate_cost` at the start of a planning session and `report_session` at the end. For Tier 2, call `report_step_cost` after each agent step using Cursor's token usage data.

**Tier 2 step reporting in a Cursor workflow:**

```python
# After each agent step completes, report its cost.
# Cursor exposes usage data in the AgentStep completion event.
mcp.call("report_step_cost", {
    "step_name": "Research Agent",
    "tokens_in": step.usage.input_tokens,
    "tokens_out": step.usage.output_tokens,
    "tokens_cache_read": step.usage.cache_read_input_tokens,
    "model": step.model
})
```

### VS Code

VS Code with the GitHub Copilot extension does not currently expose per-step token counts to extension tools. Use Tier 1 for VS Code workflows.

**VS Code configuration example** (`.vscode/mcp.json`):

```json
{
  "servers": {
    "tokencast": {
      "type": "stdio",
      "command": "python3",
      "args": ["-m", "tokencast_mcp"],
      "cwd": "/path/to/tokencast"
    }
  }
}
```

**Tier 1 workflow for VS Code:**

```python
# Start of session
mcp.call("estimate_cost", {"size": "M", "files": 5, "complexity": "medium"})

# [work happens]

# End of session — report total cost from Anthropic usage dashboard or billing API
mcp.call("report_session", {"actual_cost": 4.20, "turn_count": 87})
```

---

## Manual Cost Reporting

If you ran a session without tokencast active, you can record the cost afterward using `report_session` directly. No active estimate is required.

```python
# Read actual cost from your Anthropic usage dashboard.
# Call report_session with no prior estimate_cost call.
mcp.call("report_session", {
    "actual_cost": 5.40,
    "turn_count": 201,
    "review_cycles_actual": 3
})
```

The server checks for a recent `calibration/last-estimate.md` (within 48 hours) and reconstitutes an estimate if available. If no estimate can be found, a minimal history record is written with `size: "unknown"`. Either way, the record contributes to global calibration.

The response includes `warning: "no_active_estimate"` to indicate reconstitution was used or no estimate was found.

---

## Step Names

When calling `report_step_cost`, use canonical step names for the best calibration accuracy:

| Canonical Name | Aliases |
|----------------|---------|
| Research Agent | researcher, research |
| Architect Agent | architect |
| Engineer Initial Plan | engineer-initial |
| Engineer Final Plan | engineer-final |
| Staff Review | staff-reviewer, staff_reviewer |
| Implementation | implementer, implement |
| QA | qa |
| Frontend Designer | frontend-designer, frontend_designer |
| Docs Updater | docs-updater, docs_updater |

Non-canonical names are accepted and stored as-is. They appear in `step_actuals` in history records but do not accumulate per-step calibration factors — they contribute only to the global factor.

Custom aliases can be added to `calibration/agent-map.json`. See the [attribution protocol](../attribution-protocol.md#section-9) for the full alias resolution rules.

---

## Token-to-Cost Conversion

If your client provides token counts rather than a dollar cost, `report_step_cost` computes the cost automatically:

```python
report_step_cost({
    "step_name": "Implementation",
    "tokens_in": 150000,
    "tokens_out": 25000,
    "tokens_cache_read": 80000,
    "tokens_cache_write": 20000,
    "model": "claude-sonnet-4-6"
})
# Server computes:
#   150000 * $3.00 + 25000 * $15.00 + 80000 * $0.30 + 20000 * $3.75  (per million)
#   = $0.450 + $0.375 + $0.024 + $0.075 = $0.924
```

When `cost` is provided alongside token fields, `cost` takes precedence. When `model` is omitted, the server uses the step's default model from `references/pricing.md`, falling back to `claude-sonnet-4-6`.

For full token field definitions and model resolution order, see the [attribution protocol](../attribution-protocol.md#section-7).

---

## Migration Guide: SKILL.md to MCP

If you previously used tokencast as a Claude Code skill (via `SKILL.md`) and want to use the MCP server instead:

1. **Install the MCP server.** Register `tokencast_mcp` in your client's MCP config (see examples above).
2. **Remove or keep the Claude Code hooks** — they can coexist. The sidecar path and MCP path produce identical history record schemas. Both write to the same `calibration/history.jsonl`.
3. **Choose a tier.** If your client exposes per-step token data, use Tier 2 for the same accuracy as the Claude Code sidecar path. Otherwise, start with Tier 1.
4. **Existing calibration data is reused.** `factors.json` and `history.jsonl` from your Claude Code sessions are read by the MCP server. No migration needed.

The only difference you will see in history records is `attribution_method: "mcp"` or `"proportional"` instead of `"sidecar"`. The factor computation in `update-factors.py` treats all three methods identically.
