"""Claude Max plan quota constants and helpers.

Claude Max has hard session limits per 5-hour rolling window:
  - Max 5x:  ~88,000 tokens
  - Max 20x: ~220,000 tokens

These are not unlimited plans — they cap compute per session. This module
provides helpers to express a tokencast estimate as a percentage of the
user's session quota.
"""

from typing import Optional

# ---------------------------------------------------------------------------
# Quota constants
# ---------------------------------------------------------------------------

# Tokens-per-5-hour-window for each Claude Max plan tier
MAX_PLAN_QUOTAS: dict[str, int] = {
    "5x":  88_000,
    "20x": 220_000,
}

VALID_MAX_PLANS: frozenset = frozenset(MAX_PLAN_QUOTAS.keys())

# ---------------------------------------------------------------------------
# Token approximation
# ---------------------------------------------------------------------------

# Approximate USD cost per million tokens for a typical pipeline run.
#
# Derived from Sonnet at expected-band cache rates (~50% cache hit):
#   Input effective: $3.00 * 0.50 + $0.30 * 0.45 + $3.75 * 0.05 ≈ $1.82/M
#   Output:          $15.00/M
#   Weighted (90% input / 10% output by token count): $1.82*0.9 + $15*0.1 ≈ $3.14/M
#
# Rounded up to $3.50/M to stay conservative (display slightly higher % than
# reality, which is better than under-warning).
_APPROX_USD_PER_M_TOKENS: float = 3.50


def approx_tokens_from_cost(cost_dollars: float) -> int:
    """Approximate total tokens from an expected-band dollar estimate.

    Uses a fixed Sonnet-at-expected-band reference rate. Rough accuracy only
    (~±30%) — sufficient for quota-percentage framing.

    Args:
        cost_dollars: Expected dollar cost of the estimate.

    Returns:
        Approximate token count as an integer.
    """
    if cost_dollars <= 0:
        return 0
    return int(cost_dollars / _APPROX_USD_PER_M_TOKENS * 1_000_000)


def quota_percentage(cost_dollars: float, max_plan: str) -> Optional[float]:
    """Return the approximate percentage of the 5-hour quota consumed.

    Args:
        cost_dollars: Expected dollar cost of the estimate.
        max_plan: Plan tier string, e.g. ``"5x"`` or ``"20x"``.

    Returns:
        Percentage as a float (may exceed 100), or ``None`` if ``max_plan``
        is not a recognised tier.
    """
    quota = MAX_PLAN_QUOTAS.get(max_plan)
    if quota is None:
        return None
    tokens = approx_tokens_from_cost(cost_dollars)
    return tokens / quota * 100


def format_quota_line(cost_dollars: float, max_plan: Optional[str]) -> str:
    """Return a formatted quota-percentage line for the estimate output.

    Returns an empty string when ``max_plan`` is ``None`` or unrecognised.

    Args:
        cost_dollars: Expected dollar cost of the estimate.
        max_plan: Plan tier string or ``None``.

    Returns:
        A single-line string (no trailing newline), or ``""`` if not applicable.
    """
    if not max_plan:
        return ""
    pct = quota_percentage(cost_dollars, max_plan)
    if pct is None:
        return ""
    pct_rounded = round(pct)
    if pct > 100:
        return (
            f"**Max plan:** ⚠ This estimate (~{pct_rounded}%) may exceed your "
            f"5-hour session window (Claude Max {max_plan})."
        )
    return (
        f"**Max plan:** This estimate will use ~{pct_rounded}% of your "
        f"5-hour session window (Claude Max {max_plan})."
    )
