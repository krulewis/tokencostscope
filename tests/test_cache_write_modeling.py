"""Tests for cache write modeling (v1.4.0).

Tests the three-term input cost formula that splits cached tokens into a
write portion (priced at price_cw, fraction=1/K) and a read portion
(priced at price_cr, fraction=(K-1)/K). All TestDocumentContent tests must
fail before implementation (SKILL.md still has the old two-term formula and
v1.3.0 version strings). All other tests verify the formula helper itself and
will pass as written since they test the helper defined in this file — but the
document content tests are the canonical TDD gate.
"""

# pytest runner: always use /usr/bin/python3 -m pytest (system Python 3.9 has pytest)
# Do NOT use `pytest` or `python3 -m pytest` — Homebrew python3 resolves to 3.14 without pytest.

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "SKILL.md"
LEARN_SH = REPO_ROOT / "scripts" / "tokencostscope-learn.sh"

# ---------------------------------------------------------------------------
# Pricing constants (from references/pricing.md)
# ---------------------------------------------------------------------------

# Sonnet
SONNET_IN, SONNET_CW, SONNET_CR = 3.00, 3.75, 0.30
# Opus
OPUS_IN, OPUS_CW, OPUS_CR = 5.00, 6.25, 0.50
# Haiku
HAIKU_IN, HAIKU_CW, HAIKU_CR = 1.00, 1.25, 0.10


# ---------------------------------------------------------------------------
# Module-level helper — three-term input cost formula from SKILL.md Step 3d
# ---------------------------------------------------------------------------

def compute_input_cost(input_accum, cache_rate, K, price_in, price_cw, price_cr):
    """Three-term input cost formula as specified in SKILL.md Step 3d."""
    cwf = 1.0 / K
    uncached = input_accum * (1 - cache_rate) * price_in
    cache_write = input_accum * cache_rate * cwf * price_cw
    cache_read  = input_accum * cache_rate * (1 - cwf) * price_cr
    return (uncached + cache_write + cache_read) / 1_000_000


# ---------------------------------------------------------------------------
# Class 1: TestCacheWriteFraction — verifies 1/K write fraction values
# ---------------------------------------------------------------------------

class TestCacheWriteFraction:
    """Verifies that cache_write_fraction = 1/K for representative K values."""

    def test_write_fraction_k3(self):
        """K=3 gives fraction 1/3 ≈ 0.3333."""
        assert abs(1.0 / 3 - 1 / 3) < 1e-9
        assert abs(1.0 / 3 - 0.3333) < 0.0001

    def test_write_fraction_k4(self):
        """K=4 gives fraction 0.25 (Architect Agent)."""
        assert abs(1.0 / 4 - 0.25) < 1e-9

    def test_write_fraction_k5(self):
        """K=5 gives fraction 0.20 (Engineer Final Plan)."""
        assert abs(1.0 / 5 - 0.20) < 1e-9

    def test_write_fraction_k9(self):
        """K=9 gives fraction ≈ 0.1111 (Engineer Initial Plan)."""
        assert abs(1.0 / 9 - 0.1111) < 0.0001

    def test_write_fraction_k11(self):
        """K=11 gives fraction ≈ 0.0909 (Test Writing)."""
        assert abs(1.0 / 11 - 0.0909) < 0.0001

    def test_write_fraction_k14(self):
        """K=14 gives fraction ≈ 0.0714 (Research Agent, Implementation)."""
        assert abs(1.0 / 14 - 0.0714) < 0.0001

    def test_write_fraction_k1(self):
        """K=1 gives fraction 1.0 (edge case: single-turn step, all writes)."""
        assert abs(1.0 / 1 - 1.0) < 1e-9

    def test_write_fraction_k100(self):
        """K=100 gives fraction 0.01 (large K approaches 0)."""
        assert abs(1.0 / 100 - 0.01) < 1e-9


# ---------------------------------------------------------------------------
# Class 2: TestThreeTermFormula — verifies correct three-term formula values
# ---------------------------------------------------------------------------

class TestThreeTermFormula:
    """Verifies the three-term formula produces correct values for specific scenarios."""

    def test_basic_three_term_correctness(self):
        """Sonnet Expected band, K=14 (Research Agent): result ≈ $1.0639."""
        # input_accum=600_000, cache_rate=0.50, K=14
        # cwf = 1/14
        # uncached     = 600_000 × 0.50 × 3.00           = 900_000
        # cache_write  = 600_000 × 0.50 × (1/14) × 3.75  ≈  80_357
        # cache_read   = 600_000 × 0.50 × (13/14) × 0.30 ≈  83_571
        # total = (900_000 + 80_357 + 83_571) / 1e6 ≈ $1.0639
        result = compute_input_cost(600_000, 0.50, 14, SONNET_IN, SONNET_CW, SONNET_CR)
        assert abs(result - 1.0639) < 0.0001

    def test_zero_cache_rate_no_write_cost(self):
        """cache_rate=0 means no cached tokens; formula reduces to input_accum × price_in / 1e6."""
        input_accum = 500_000
        result = compute_input_cost(input_accum, 0.0, 14, SONNET_IN, SONNET_CW, SONNET_CR)
        expected = input_accum * SONNET_IN / 1_000_000
        assert abs(result - expected) < 0.0001

    def test_full_cache_rate_splits_entirely(self):
        """cache_rate=1.0: all tokens go through write/read split; no uncached term."""
        input_accum = 500_000
        K = 5
        cwf = 1.0 / K
        expected = (input_accum * cwf * SONNET_CW + input_accum * (1 - cwf) * SONNET_CR) / 1_000_000
        result = compute_input_cost(input_accum, 1.0, K, SONNET_IN, SONNET_CW, SONNET_CR)
        assert abs(result - expected) < 0.0001

    def test_k1_all_cache_write(self):
        """K=1, cache_rate=0.50: entire cached portion priced at price_cw only (no read term).

        When K=1, cache_write_fraction=1.0, so all cached tokens are priced at price_cw.
        For Sonnet, price_cw=$3.75 > price_in=$3.00, meaning a K=1 step with cache_rate>0
        costs MORE than with no caching at all. This is correct behavior (writing to cache
        is inherently more expensive than fresh input), not a bug. No pipeline step currently
        has K=1 in practice.
        """
        input_accum = 500_000
        cache_rate = 0.50
        result = compute_input_cost(input_accum, cache_rate, 1, SONNET_IN, SONNET_CW, SONNET_CR)
        # K=1 → cwf=1.0, no cache_read term
        expected = (input_accum * (1 - cache_rate) * SONNET_IN
                    + input_accum * cache_rate * 1.0 * SONNET_CW) / 1_000_000
        assert abs(result - expected) < 0.0001

    def test_higher_than_old_formula(self):
        """For any K < infinity and price_cw > price_cr, new formula > old all-reads formula."""
        input_accum = 600_000
        cache_rate = 0.50
        K = 14
        new_cost = compute_input_cost(input_accum, cache_rate, K, SONNET_IN, SONNET_CW, SONNET_CR)
        # Old two-term formula: all cached tokens priced at price_cr
        old_cost = (input_accum * (1 - cache_rate) * SONNET_IN
                    + input_accum * cache_rate * SONNET_CR) / 1_000_000
        assert new_cost > old_cost


# ---------------------------------------------------------------------------
# Class 3: TestPerModelPriceCorrectness — verifies correct per-model pricing
# ---------------------------------------------------------------------------

class TestPerModelPriceCorrectness:
    """Verifies correct per-model price application for Sonnet, Opus, and Haiku."""

    INPUT_ACCUM = 100_000
    CACHE_RATE = 0.50
    K = 5

    def test_sonnet_prices(self):
        """Sonnet: price_in=3.00, price_cw=3.75, price_cr=0.30. K=5, cache_rate=0.50."""
        cwf = 1.0 / self.K
        expected = (
            self.INPUT_ACCUM * (1 - self.CACHE_RATE) * SONNET_IN
            + self.INPUT_ACCUM * self.CACHE_RATE * cwf * SONNET_CW
            + self.INPUT_ACCUM * self.CACHE_RATE * (1 - cwf) * SONNET_CR
        ) / 1_000_000
        result = compute_input_cost(self.INPUT_ACCUM, self.CACHE_RATE, self.K, SONNET_IN, SONNET_CW, SONNET_CR)
        assert abs(result - expected) < 0.0001

    def test_opus_prices(self):
        """Opus: price_in=5.00, price_cw=6.25, price_cr=0.50. Higher absolute cost than Sonnet."""
        result_opus = compute_input_cost(self.INPUT_ACCUM, self.CACHE_RATE, self.K, OPUS_IN, OPUS_CW, OPUS_CR)
        result_sonnet = compute_input_cost(self.INPUT_ACCUM, self.CACHE_RATE, self.K, SONNET_IN, SONNET_CW, SONNET_CR)
        assert result_opus > result_sonnet

    def test_haiku_prices(self):
        """Haiku: price_in=1.00, price_cw=1.25, price_cr=0.10. Lower absolute cost than Sonnet."""
        result_haiku = compute_input_cost(self.INPUT_ACCUM, self.CACHE_RATE, self.K, HAIKU_IN, HAIKU_CW, HAIKU_CR)
        result_sonnet = compute_input_cost(self.INPUT_ACCUM, self.CACHE_RATE, self.K, SONNET_IN, SONNET_CW, SONNET_CR)
        assert result_haiku < result_sonnet

    def test_cw_cr_ratio_12_5x_all_models(self):
        """For all three models, price_cw / price_cr == 12.5 (confirms pricing table consistency)."""
        assert abs(SONNET_CW / SONNET_CR - 12.5) < 1e-9  # 3.75 / 0.30 = 12.5
        assert abs(OPUS_CW / OPUS_CR - 12.5) < 1e-9      # 6.25 / 0.50 = 12.5
        assert abs(HAIKU_CW / HAIKU_CR - 12.5) < 1e-9    # 1.25 / 0.10 = 12.5


# ---------------------------------------------------------------------------
# Class 4: TestBandVariation — verifies band-level cache_rate differences
# ---------------------------------------------------------------------------

class TestBandVariation:
    """Verifies that band-level cache_rate differences produce correct relative costs."""

    # Bands from pricing.md
    CACHE_OPT = 0.60
    CACHE_EXP = 0.50
    CACHE_PES = 0.30

    INPUT_ACCUM = 1_000_000
    K = 7

    def test_optimistic_higher_write_cost_than_expected(self):
        """Optimistic band (cache_rate=0.60) produces higher absolute write cost than Expected (cache_rate=0.50)."""
        cwf = 1.0 / self.K
        write_cost_opt = self.INPUT_ACCUM * self.CACHE_OPT * cwf * SONNET_CW
        write_cost_exp = self.INPUT_ACCUM * self.CACHE_EXP * cwf * SONNET_CW
        assert write_cost_opt > write_cost_exp

    def test_pessimistic_lower_write_cost_than_expected(self):
        """Pessimistic band (cache_rate=0.30) produces lower absolute write cost than Expected (cache_rate=0.50)."""
        cwf = 1.0 / self.K
        write_cost_pes = self.INPUT_ACCUM * self.CACHE_PES * cwf * SONNET_CW
        write_cost_exp = self.INPUT_ACCUM * self.CACHE_EXP * cwf * SONNET_CW
        assert write_cost_pes < write_cost_exp

    def test_band_ordering_pes_exp_opt(self):
        """CORRECTED: pessimistic_input_cost > expected_input_cost > optimistic_input_cost.

        Higher cache_rate means more tokens priced at cheap cache rates (price_cw/price_cr)
        and fewer at the expensive price_in. Since price_in dominates (3.00 vs 0.30/3.75),
        a higher cache_rate lowers total input_cost.

        Numerical verification with Sonnet, K=7, input_accum=1_000_000:
          Optimistic (cache=0.60): 400000×3.00 + 600000×(1/7)×3.75 + 600000×(6/7)×0.30
                                 ≈ 1_200_000 + 321_429 + 154_286 = $1.6757/M
          Expected   (cache=0.50): 500000×3.00 + 500000×(1/7)×3.75 + 500000×(6/7)×0.30
                                 ≈ 1_500_000 + 267_857 + 128_571 = $1.8964/M
          Pessimistic(cache=0.30): 700000×3.00 + 300000×(1/7)×3.75 + 300000×(6/7)×0.30
                                 ≈ 2_100_000 + 160_714 +  77_143 = $2.3379/M
        """
        opt_cost = compute_input_cost(self.INPUT_ACCUM, self.CACHE_OPT, self.K, SONNET_IN, SONNET_CW, SONNET_CR)
        exp_cost = compute_input_cost(self.INPUT_ACCUM, self.CACHE_EXP, self.K, SONNET_IN, SONNET_CW, SONNET_CR)
        pes_cost = compute_input_cost(self.INPUT_ACCUM, self.CACHE_PES, self.K, SONNET_IN, SONNET_CW, SONNET_CR)

        # Verify numerical values
        assert abs(opt_cost - 1.6757) < 0.0001
        assert abs(exp_cost - 1.8964) < 0.0001
        assert abs(pes_cost - 2.3379) < 0.0001

        # Verify ordering: pessimistic > expected > optimistic
        assert pes_cost > exp_cost > opt_cost


# ---------------------------------------------------------------------------
# Class 5: TestParallelInteraction — verifies parallel cache_rate reduction
# ---------------------------------------------------------------------------

class TestParallelInteraction:
    """Verifies cache_rate parallel reduction applies before the write/read split."""

    PARALLEL_CACHE_RATE_REDUCTION = 0.15
    PARALLEL_CACHE_RATE_FLOOR = 0.05

    def test_parallel_reduces_cache_rate_before_split(self):
        """Parallel step: apply max(cache_rate - 0.15, 0.05) first, then three-term formula."""
        input_accum = 500_000
        base_cache_rate = 0.50
        K = 14
        parallel_cache_rate = max(base_cache_rate - self.PARALLEL_CACHE_RATE_REDUCTION, self.PARALLEL_CACHE_RATE_FLOOR)
        # parallel_cache_rate = max(0.50 - 0.15, 0.05) = 0.35

        cost_parallel = compute_input_cost(input_accum, parallel_cache_rate, K, SONNET_IN, SONNET_CW, SONNET_CR)
        cost_base = compute_input_cost(input_accum, base_cache_rate, K, SONNET_IN, SONNET_CW, SONNET_CR)

        # Parallel reduces total input_cost (fewer tokens hit cache rates, more go through price_in)
        # But since price_in > price_cw > price_cr and we confirmed above that lower cache_rate →
        # higher cost, parallel discount actually increases the per-token cost (more tokens priced at price_in)
        # However, the parallel_input_discount (0.75) reduces input_accum, which is the dominant effect.
        # This test just verifies that parallel_cache_rate was properly applied (different from base).
        assert parallel_cache_rate == 0.35
        assert abs(parallel_cache_rate - (base_cache_rate - self.PARALLEL_CACHE_RATE_REDUCTION)) < 1e-9

        # With parallel_input_discount applied, parallel cost would be lower overall.
        # Here we just verify the cache_rate reduction changes the formula result.
        assert cost_parallel != cost_base

    def test_parallel_floor_applies_before_split(self):
        """cache_rate=0.10 before parallel: effective rate = max(0.10-0.15, 0.05) = 0.05."""
        input_accum = 500_000
        base_cache_rate = 0.10
        K = 14
        effective_rate = max(base_cache_rate - self.PARALLEL_CACHE_RATE_REDUCTION, self.PARALLEL_CACHE_RATE_FLOOR)
        assert effective_rate == 0.05

        # Write fraction still applies to the floored rate, not the original or zero
        cwf = 1.0 / K
        result = compute_input_cost(input_accum, effective_rate, K, SONNET_IN, SONNET_CW, SONNET_CR)
        # Manually: uncached = 500000 × 0.95 × 3.00; write = 500000 × 0.05 × (1/14) × 3.75; read = 500000 × 0.05 × (13/14) × 0.30
        expected = (
            input_accum * (1 - effective_rate) * SONNET_IN
            + input_accum * effective_rate * cwf * SONNET_CW
            + input_accum * effective_rate * (1 - cwf) * SONNET_CR
        ) / 1_000_000
        assert abs(result - expected) < 0.0001


# ---------------------------------------------------------------------------
# Class 6: TestPRReviewLoopImpact — verifies PR Review Loop cost with new formula
# ---------------------------------------------------------------------------

class TestPRReviewLoopImpact:
    """Verifies PR Review Loop constituent costs reflect the new three-term formula."""

    # heuristics.md K values: Staff Review K=3, Engineer Final Plan K=5
    STAFF_REVIEW_K = 3
    ENGINEER_FINAL_K = 5

    # heuristics.md input_base for Staff Review (Opus) and Engineer Final Plan (Sonnet)
    # Using approximate accumulation values consistent with examples.md
    # Staff Review: input_accum ≈ 1_200_000 (approximate, for relative comparison)
    # Engineer Final Plan: input_accum ≈ 270_000 (approximate)
    # The test validates direction (new > old), not exact values.
    STAFF_INPUT_ACCUM = 1_200_000
    ENGINEER_INPUT_ACCUM = 270_000
    CACHE_RATE_EXP = 0.50

    def _old_input_cost(self, input_accum, cache_rate, price_in, price_cr):
        """Old two-term formula for comparison."""
        return (input_accum * (1 - cache_rate) * price_in
                + input_accum * cache_rate * price_cr) / 1_000_000

    def test_pr_loop_c_increases_with_new_formula(self):
        """Constituent costs are higher with three-term formula; C = staff + engineer is higher.

        Staff Review uses Opus prices from pricing.md: price_in=5.00, price_cw=6.25, price_cr=0.50.
        Engineer Final Plan uses Sonnet prices. Do NOT use stale examples.md values ($15.00 etc.).
        """
        staff_new = compute_input_cost(
            self.STAFF_INPUT_ACCUM, self.CACHE_RATE_EXP, self.STAFF_REVIEW_K,
            OPUS_IN, OPUS_CW, OPUS_CR
        )
        engineer_new = compute_input_cost(
            self.ENGINEER_INPUT_ACCUM, self.CACHE_RATE_EXP, self.ENGINEER_FINAL_K,
            SONNET_IN, SONNET_CW, SONNET_CR
        )

        staff_old = self._old_input_cost(
            self.STAFF_INPUT_ACCUM, self.CACHE_RATE_EXP, OPUS_IN, OPUS_CR
        )
        engineer_old = self._old_input_cost(
            self.ENGINEER_INPUT_ACCUM, self.CACHE_RATE_EXP, SONNET_IN, SONNET_CR
        )

        assert staff_new > staff_old
        assert engineer_new > engineer_old

        c_new = staff_new + engineer_new
        c_old = staff_old + engineer_old
        assert c_new > c_old

    def test_pr_loop_uses_undiscounted_costs(self):
        """PR Review Loop C must use pre-discount (undiscounted) costs, not parallel-discounted costs.

        For a scenario where Staff Review is in a parallel group:
        The undiscounted three-term cost differs from the discounted cost,
        and C must use the former.
        """
        input_accum = self.STAFF_INPUT_ACCUM
        base_cache_rate = self.CACHE_RATE_EXP
        K = self.STAFF_REVIEW_K
        parallel_input_discount = 0.75
        parallel_cache_rate_reduction = 0.15
        parallel_cache_rate_floor = 0.05

        # Undiscounted cost (used for C)
        undiscounted_cost = compute_input_cost(
            input_accum, base_cache_rate, K, OPUS_IN, OPUS_CW, OPUS_CR
        )

        # Discounted cost (used for the step's own table row, NOT for C)
        discounted_input_accum = input_accum * parallel_input_discount
        discounted_cache_rate = max(base_cache_rate - parallel_cache_rate_reduction, parallel_cache_rate_floor)
        discounted_cost = compute_input_cost(
            discounted_input_accum, discounted_cache_rate, K, OPUS_IN, OPUS_CW, OPUS_CR
        )

        assert undiscounted_cost != discounted_cost
        # C uses undiscounted — it should be higher (more tokens, higher cache_rate → cheaper cached tokens
        # but the discount also reduces input_accum, which is the dominant factor)
        assert undiscounted_cost > discounted_cost

    def test_pr_loop_decay_propagates_new_c(self):
        """Higher C from new formula produces proportionally higher decay review loop cost per band."""
        # Compute C values under old and new formulas
        staff_new = compute_input_cost(
            self.STAFF_INPUT_ACCUM, self.CACHE_RATE_EXP, self.STAFF_REVIEW_K,
            OPUS_IN, OPUS_CW, OPUS_CR
        )
        engineer_new = compute_input_cost(
            self.ENGINEER_INPUT_ACCUM, self.CACHE_RATE_EXP, self.ENGINEER_FINAL_K,
            SONNET_IN, SONNET_CW, SONNET_CR
        )
        c_new = staff_new + engineer_new

        staff_old = (
            self.STAFF_INPUT_ACCUM * (1 - self.CACHE_RATE_EXP) * OPUS_IN
            + self.STAFF_INPUT_ACCUM * self.CACHE_RATE_EXP * OPUS_CR
        ) / 1_000_000
        engineer_old = (
            self.ENGINEER_INPUT_ACCUM * (1 - self.CACHE_RATE_EXP) * SONNET_IN
            + self.ENGINEER_INPUT_ACCUM * self.CACHE_RATE_EXP * SONNET_CR
        ) / 1_000_000
        c_old = staff_old + engineer_old

        # Decay formula: C × (1 − 0.6^N) / 0.4
        def decay_sum(c, n, decay=0.6):
            if n == 0:
                return 0.0
            return c * (1.0 - decay**n) / (1.0 - decay)

        # Expected band (N=2)
        loop_new_exp = decay_sum(c_new, 2)
        loop_old_exp = decay_sum(c_old, 2)
        assert loop_new_exp > loop_old_exp

        # The ratio of new/old loop cost should equal ratio of new/old C (decay is linear in C)
        assert abs(loop_new_exp / loop_old_exp - c_new / c_old) < 0.0001


# ---------------------------------------------------------------------------
# Class 7: TestDocumentContent — verifies SKILL.md formula text and version strings
# ---------------------------------------------------------------------------

class TestDocumentContent:
    """Verifies SKILL.md contains the updated three-term formula and v1.4.0 version strings.

    All tests in this class MUST FAIL before implementation (SKILL.md still has the
    old two-term formula and v1.3.0 version strings). This is the TDD gate.
    """

    def test_skill_md_has_cache_write_fraction_formula(self):
        """SKILL.md must contain 'cache_write_fraction = 1 / K'."""
        content = SKILL_MD.read_text()
        assert "cache_write_fraction = 1 / K" in content

    def test_skill_md_has_three_term_input_cost(self):
        """SKILL.md must contain 'cache_write_fraction × price_cw' in the input_cost expression."""
        content = SKILL_MD.read_text()
        assert "cache_write_fraction × price_cw" in content

    def test_skill_md_no_not_yet_used_comment(self):
        """SKILL.md must NOT contain 'not yet used' (comment removed in v1.3.1)."""
        content = SKILL_MD.read_text()
        assert "not yet used" not in content

    def test_skill_md_version_140_frontmatter(self):
        """SKILL.md frontmatter must contain 'version: 1.4.0'."""
        content = SKILL_MD.read_text()
        assert "version: 1.4.0" in content

    def test_skill_md_version_140_output_header(self):
        """SKILL.md must contain '## costscope estimate (v1.4.0)' in the output template."""
        content = SKILL_MD.read_text()
        assert "## costscope estimate (v1.4.0)" in content

    def test_learn_sh_version_140(self):
        """scripts/tokencostscope-learn.sh must contain 'VERSION=\"1.4.0\"'."""
        content = LEARN_SH.read_text()
        assert 'VERSION="1.4.0"' in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
