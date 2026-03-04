#!/usr/bin/env python3
"""Sum token usage from a Claude Code session JSONL file and compute actual dollar cost.

Usage: python3 sum-session-tokens.py <jsonl_path> [baseline_cost]

Output: JSON object with actual cost, token breakdown by model, and metadata.
baseline_cost is subtracted from total to isolate the task's cost (tokens used
before the estimate was created are not part of the task).
"""

import json
import sys
from pathlib import Path

# Prices per million tokens — update when pricing changes.
# Must match references/pricing.md.
PRICES = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
        "output": 15.00,
    },
    "claude-opus-4-6": {
        "input": 15.00,
        "cache_read": 1.50,
        "cache_write": 18.75,
        "output": 75.00,
    },
    "claude-haiku-4-5": {
        "input": 0.80,
        "cache_read": 0.08,
        "cache_write": 1.00,
        "output": 4.00,
    },
}

# Fallback for unknown models
DEFAULT_MODEL = "claude-sonnet-4-6"


def sum_session(jsonl_path: str, baseline_cost: float = 0.0) -> dict:
    totals: dict[str, dict[str, int]] = {}
    turn_count = 0

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") != "assistant":
                continue

            msg = obj.get("message", {})
            usage = msg.get("usage")
            if not usage:
                continue

            model = msg.get("model", "")

            # Skip synthetic/internal messages
            if not model or model == "<synthetic>":
                continue

            # Normalize model name (strip date suffixes like -20250514)
            model_key = model
            for known in PRICES:
                if known in model:
                    model_key = known
                    break

            if model_key not in totals:
                totals[model_key] = {
                    "input": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "output": 0,
                }

            totals[model_key]["input"] += usage.get("input_tokens", 0)
            totals[model_key]["cache_read"] += usage.get("cache_read_input_tokens", 0)
            totals[model_key]["cache_write"] += usage.get(
                "cache_creation_input_tokens", 0
            )
            totals[model_key]["output"] += usage.get("output_tokens", 0)
            turn_count += 1

    # Compute dollar cost per model
    total_cost = 0.0
    for model_key, tokens in totals.items():
        prices = PRICES.get(model_key, PRICES[DEFAULT_MODEL])
        cost = (
            tokens["input"] * prices["input"]
            + tokens["cache_read"] * prices["cache_read"]
            + tokens["cache_write"] * prices["cache_write"]
            + tokens["output"] * prices["output"]
        ) / 1_000_000
        total_cost += cost

    task_cost = max(0.0, total_cost - baseline_cost)

    return {
        "total_session_cost": round(total_cost, 4),
        "actual_cost": round(task_cost, 4),
        "baseline_cost": round(baseline_cost, 4),
        "turn_count": turn_count,
        "tokens_by_model": totals,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: sum-session-tokens.py <jsonl_path> [baseline_cost]", file=sys.stderr)
        sys.exit(1)

    jsonl_path = sys.argv[1]
    baseline_cost = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0

    if not Path(jsonl_path).exists():
        print(json.dumps({"error": f"File not found: {jsonl_path}"}))
        sys.exit(1)

    result = sum_session(jsonl_path, baseline_cost)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
