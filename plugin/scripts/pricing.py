"""Pricing data for Anthropic Claude models.

Derived from references/pricing.md — update both files together.
"""

LAST_UPDATED: str = "2026-03-04"

STALENESS_WARNING_DAYS: int = 90

# Convenience aliases for model ID strings
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_OPUS = "claude-opus-4-6"
MODEL_HAIKU = "claude-haiku-4-5"

# Per-million-token prices for each model.
# Keys: "input", "cache_read", "cache_write", "output"
MODEL_PRICES: dict = {
    MODEL_SONNET: {
        "input": 3.00,
        "cache_read": 0.30,
        "cache_write": 3.75,
        "output": 15.00,
    },
    MODEL_OPUS: {
        "input": 5.00,
        "cache_read": 0.50,
        "cache_write": 6.25,
        "output": 25.00,
    },
    MODEL_HAIKU: {
        "input": 1.00,
        "cache_read": 0.10,
        "cache_write": 1.25,
        "output": 5.00,
    },
}

# Maps pipeline step name to canonical model ID string.
# For "Implementation", Sonnet is the default; Opus applies for L-size changes
# (that distinction is handled by the estimation engine, not this constant).
STEP_MODEL_MAP: dict = {
    "Research Agent": MODEL_SONNET,
    "Architect Agent": MODEL_OPUS,
    "Engineer Initial Plan": MODEL_SONNET,
    "Staff Review": MODEL_OPUS,
    "Engineer Final Plan": MODEL_SONNET,
    "Test Writing": MODEL_SONNET,
    "Implementation": MODEL_SONNET,  # Opus for L-size changes — resolved by engine
    "QA": MODEL_HAIKU,
}

# Cache hit rate fractions by confidence band.
# Applied to input tokens only; output tokens are never cached.
CACHE_HIT_RATES: dict = {
    "optimistic": 0.60,
    "expected": 0.50,
    "pessimistic": 0.30,
}

# Default model used when no model is specified or no match is found.
DEFAULT_MODEL: str = "claude-sonnet-4-6"


def compute_cost_from_usage(usage: dict, model: str = DEFAULT_MODEL) -> float:
    """Compute dollar cost from token usage counts.

    Uses the attribution protocol field names (not Claude Code JSONL field names).
    Protocol field names:
      - ``tokens_in``          — fresh input tokens (not cache hits)
      - ``tokens_out``         — output tokens
      - ``tokens_cache_read``  — cache-read input tokens
      - ``tokens_cache_write`` — cache-creation (write) input tokens

    Args:
        usage: Dict with any subset of the protocol field names above.
            Missing fields default to 0.
        model: Full model ID string (e.g. ``"claude-sonnet-4-6"``).
            Partial matching is applied internally — ``"claude-sonnet"``
            matches ``"claude-sonnet-4-6"``. Unknown model strings fall back
            to ``DEFAULT_MODEL`` pricing. Defaults to ``DEFAULT_MODEL``.

    Returns:
        Dollar cost as a float.  Always non-negative for non-negative inputs.
        Caller is responsible for validating that inputs are non-negative.
    """
    # Model resolution: exact match → partial match → DEFAULT_MODEL
    if not model:
        model = DEFAULT_MODEL
    model_key = DEFAULT_MODEL
    for known in MODEL_PRICES:
        if model in known or known in model:
            model_key = known
            break
    prices = MODEL_PRICES.get(model_key, MODEL_PRICES[DEFAULT_MODEL])
    cost = (
        usage.get("tokens_in", 0) * prices["input"]
        + usage.get("tokens_cache_read", 0) * prices["cache_read"]
        + usage.get("tokens_cache_write", 0) * prices["cache_write"]
        + usage.get("tokens_out", 0) * prices["output"]
    ) / 1_000_000
    return cost
