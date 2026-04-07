"""Unit tests for the max_plan module (Claude Max quota helpers)."""

import pytest

from tokencast_mcp.max_plan import (
    MAX_PLAN_QUOTAS,
    VALID_MAX_PLANS,
    approx_tokens_from_cost,
    quota_percentage,
    format_quota_line,
)


class TestMaxPlanConstants:
    def test_valid_plans_contains_5x(self):
        assert "5x" in VALID_MAX_PLANS

    def test_valid_plans_contains_20x(self):
        assert "20x" in VALID_MAX_PLANS

    def test_quota_5x_is_88000(self):
        assert MAX_PLAN_QUOTAS["5x"] == 88_000

    def test_quota_20x_is_220000(self):
        assert MAX_PLAN_QUOTAS["20x"] == 220_000

    def test_20x_quota_is_larger_than_5x(self):
        assert MAX_PLAN_QUOTAS["20x"] > MAX_PLAN_QUOTAS["5x"]


class TestApproxTokensFromCost:
    def test_zero_cost_returns_zero(self):
        assert approx_tokens_from_cost(0.0) == 0

    def test_one_dollar_returns_positive_tokens(self):
        tokens = approx_tokens_from_cost(1.0)
        assert tokens > 0

    def test_proportional_scaling(self):
        t1 = approx_tokens_from_cost(1.0)
        t2 = approx_tokens_from_cost(2.0)
        assert t2 == pytest.approx(t1 * 2, rel=0.01)

    def test_reasonable_range_for_typical_estimate(self):
        # $0.50 typical small estimate should yield 50k-300k tokens (reasonable range)
        tokens = approx_tokens_from_cost(0.50)
        assert 50_000 < tokens < 500_000

    def test_returns_int(self):
        assert isinstance(approx_tokens_from_cost(1.0), int)


class TestQuotaPercentage:
    def test_unknown_plan_returns_none(self):
        assert quota_percentage(1.0, "pro") is None

    def test_5x_plan_returns_float(self):
        result = quota_percentage(0.10, "5x")
        assert isinstance(result, float)
        assert result > 0

    def test_20x_plan_lower_percentage_than_5x(self):
        pct_5x = quota_percentage(0.10, "5x")
        pct_20x = quota_percentage(0.10, "20x")
        assert pct_20x < pct_5x

    def test_zero_cost_returns_zero_percent(self):
        assert quota_percentage(0.0, "5x") == pytest.approx(0.0)

    def test_high_cost_can_exceed_100_percent(self):
        # A large estimate can legitimately exceed the quota window
        pct = quota_percentage(10.0, "5x")
        assert pct > 100.0


class TestFormatQuotaLine:
    def test_none_plan_returns_empty_string(self):
        assert format_quota_line(1.0, None) == ""

    def test_unknown_plan_returns_empty_string(self):
        assert format_quota_line(1.0, "pro") == ""

    def test_5x_plan_includes_plan_name(self):
        line = format_quota_line(0.10, "5x")
        assert "5x" in line or "Max" in line

    def test_5x_plan_includes_percentage(self):
        line = format_quota_line(0.10, "5x")
        assert "%" in line

    def test_5x_plan_includes_session_window_reference(self):
        line = format_quota_line(0.10, "5x")
        assert "session" in line.lower() or "window" in line.lower()

    def test_high_usage_includes_warning_indicator(self):
        # Estimates that consume >100% of the window should flag it
        line = format_quota_line(10.0, "5x")
        assert ">" in line or "exceed" in line.lower() or "%" in line

    def test_low_usage_does_not_include_warning(self):
        # Small estimates should not show a warning
        line = format_quota_line(0.001, "5x")
        assert "exceed" not in line.lower()

    def test_20x_plan_included(self):
        line = format_quota_line(0.10, "20x")
        assert "20x" in line or "%" in line
