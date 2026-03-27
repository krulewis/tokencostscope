# Run with: /usr/bin/python3 -m pytest tests/test_cost_history_tool.py
"""Tests for get_cost_history API and MCP tool handler (US-1b.06).

Covers:
- Empty history → empty records list and zero-valued summary
- Populated history → correct record shape and summary stats
- Window filtering by days ("Nd")
- Window filtering by session count ("N")
- "all" window → no filtering
- None window → no filtering
- Outlier exclusion (default include_outliers=False)
- include_outliers=True passes through outlier records
- band_hit classification using stored costs and ratio fallback
- Summary statistics: mean_ratio, median_ratio, pct_within_expected
- calibration_dir resolution: params key, direct arg, default
- MCP handler delegates to API with config.calibration_dir
"""

import asyncio
import json
import pathlib
import statistics
import sys

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — ensure src/ is importable
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tokencast.api import (  # noqa: E402
    OUTLIER_HIGH,
    OUTLIER_LOW,
    _band_hit,
    _compute_summary,
    _format_record,
    _get_ratio,
    _is_outlier,
    _resolve_window,
    get_cost_history,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RECENT_TS = "2026-03-26T12:00:00Z"
_OLD_TS = "2025-01-01T00:00:00Z"


def make_record(
    ratio=1.2,
    size="M",
    timestamp=None,
    steps=None,
    optimistic_cost=None,
    pessimistic_cost=None,
    attribution_method=None,
) -> dict:
    """Build a minimal valid history record."""
    ts = timestamp or _RECENT_TS
    expected = 5.0
    actual = expected * ratio
    r = {
        "timestamp": ts,
        "size": size,
        "expected_cost": expected,
        "actual_cost": actual,
        "ratio": ratio,
        "steps": steps or ["Implementation"],
        "attribution_method": attribution_method or "proportional",
    }
    if optimistic_cost is not None:
        r["optimistic_cost"] = optimistic_cost
    if pessimistic_cost is not None:
        r["pessimistic_cost"] = pessimistic_cost
    return r


def write_history(tmp_path: pathlib.Path, records: list) -> pathlib.Path:
    cal = tmp_path / "calibration"
    cal.mkdir(parents=True, exist_ok=True)
    history = cal / "history.jsonl"
    with open(history, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return cal


# ---------------------------------------------------------------------------
# TestGetCostHistoryEmpty
# ---------------------------------------------------------------------------


class TestGetCostHistoryEmpty:
    def test_empty_history_returns_empty_records(self, tmp_path):
        cal = write_history(tmp_path, [])
        result = get_cost_history({}, calibration_dir=str(cal))
        assert result["records"] == []

    def test_empty_history_summary_zeros(self, tmp_path):
        cal = write_history(tmp_path, [])
        result = get_cost_history({}, calibration_dir=str(cal))
        s = result["summary"]
        assert s["session_count"] == 0
        assert s["mean_ratio"] is None
        assert s["median_ratio"] is None
        assert s["pct_within_expected"] is None

    def test_missing_calibration_dir_returns_empty(self, tmp_path):
        """Non-existent calibration_dir → empty result, no crash."""
        result = get_cost_history(
            {}, calibration_dir=str(tmp_path / "does_not_exist" / "calibration")
        )
        assert result["records"] == []
        assert result["summary"]["session_count"] == 0


# ---------------------------------------------------------------------------
# TestGetCostHistoryRecordShape
# ---------------------------------------------------------------------------


class TestGetCostHistoryRecordShape:
    def test_record_has_required_fields(self, tmp_path):
        cal = write_history(tmp_path, [make_record(ratio=1.5)])
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert len(result["records"]) == 1
        rec = result["records"][0]
        for field in (
            "timestamp",
            "size",
            "expected_cost",
            "actual_cost",
            "ratio",
            "steps",
            "band_hit",
            "attribution_method",
        ):
            assert field in rec, f"Missing field: {field}"

    def test_ratio_computed_correctly(self, tmp_path):
        cal = write_history(tmp_path, [make_record(ratio=1.4)])
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["records"][0]["ratio"] == pytest.approx(1.4)

    def test_steps_preserved(self, tmp_path):
        steps = ["Research", "Implementation", "QA"]
        cal = write_history(tmp_path, [make_record(steps=steps)])
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["records"][0]["steps"] == steps

    def test_attribution_method_preserved(self, tmp_path):
        rec = make_record(attribution_method="sidecar")
        cal = write_history(tmp_path, [rec])
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["records"][0]["attribution_method"] == "sidecar"


# ---------------------------------------------------------------------------
# TestWindowFiltering
# ---------------------------------------------------------------------------


class TestWindowFiltering:
    def test_window_none_returns_all(self, tmp_path):
        records = [make_record(ratio=1.0), make_record(ratio=1.1), make_record(ratio=1.2)]
        cal = write_history(tmp_path, records)
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert len(result["records"]) == 3

    def test_window_all_returns_all(self, tmp_path):
        records = [make_record(ratio=1.0), make_record(ratio=1.1), make_record(ratio=1.2)]
        cal = write_history(tmp_path, records)
        result = get_cost_history(
            {"window": "all", "include_outliers": True}, calibration_dir=str(cal)
        )
        assert len(result["records"]) == 3

    def test_window_by_count(self, tmp_path):
        records = [make_record(ratio=float(i)) for i in range(1, 6)]
        # ratios 1.0..5.0 — last 3 sessions
        cal = write_history(tmp_path, records)
        result = get_cost_history(
            {"window": "3", "include_outliers": True}, calibration_dir=str(cal)
        )
        assert len(result["records"]) == 3
        # Should be the last 3 records (ratios 3, 4, 5)
        ratios = [r["ratio"] for r in result["records"]]
        assert ratios == pytest.approx([3.0, 4.0, 5.0])

    def test_window_count_larger_than_history(self, tmp_path):
        records = [make_record(ratio=1.0), make_record(ratio=1.1)]
        cal = write_history(tmp_path, records)
        result = get_cost_history(
            {"window": "10", "include_outliers": True}, calibration_dir=str(cal)
        )
        assert len(result["records"]) == 2

    def test_window_by_days_recent_only(self, tmp_path):
        recent = make_record(ratio=1.2, timestamp=_RECENT_TS)
        old = make_record(ratio=1.5, timestamp=_OLD_TS)
        cal = write_history(tmp_path, [old, recent])
        # 30d window — old record (>400 days ago) should be filtered out
        result = get_cost_history(
            {"window": "30d", "include_outliers": True}, calibration_dir=str(cal)
        )
        assert len(result["records"]) == 1
        assert result["records"][0]["ratio"] == pytest.approx(1.2)

    def test_window_by_days_all_excluded(self, tmp_path):
        old = make_record(ratio=1.5, timestamp=_OLD_TS)
        cal = write_history(tmp_path, [old])
        result = get_cost_history(
            {"window": "30d", "include_outliers": True}, calibration_dir=str(cal)
        )
        assert result["records"] == []

    def test_window_invalid_string_returns_all(self, tmp_path):
        records = [make_record(ratio=1.0), make_record(ratio=1.1)]
        cal = write_history(tmp_path, records)
        result = get_cost_history(
            {"window": "bogus", "include_outliers": True}, calibration_dir=str(cal)
        )
        assert len(result["records"]) == 2


# ---------------------------------------------------------------------------
# TestOutlierFiltering
# ---------------------------------------------------------------------------


class TestOutlierFiltering:
    def test_outlier_excluded_by_default(self, tmp_path):
        normal = make_record(ratio=1.2)
        outlier_high = make_record(ratio=OUTLIER_HIGH + 0.1)
        outlier_low = make_record(ratio=OUTLIER_LOW - 0.1)
        cal = write_history(tmp_path, [normal, outlier_high, outlier_low])
        result = get_cost_history({}, calibration_dir=str(cal))
        assert len(result["records"]) == 1
        assert result["records"][0]["ratio"] == pytest.approx(1.2)

    def test_include_outliers_passes_all_through(self, tmp_path):
        normal = make_record(ratio=1.2)
        outlier = make_record(ratio=OUTLIER_HIGH + 0.5)
        cal = write_history(tmp_path, [normal, outlier])
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert len(result["records"]) == 2

    def test_exact_outlier_boundary_high_excluded(self, tmp_path):
        # ratio == OUTLIER_HIGH is NOT > OUTLIER_HIGH → not an outlier
        rec = make_record(ratio=OUTLIER_HIGH)
        cal = write_history(tmp_path, [rec])
        result = get_cost_history({}, calibration_dir=str(cal))
        assert len(result["records"]) == 1

    def test_exact_outlier_boundary_low_excluded(self, tmp_path):
        # ratio == OUTLIER_LOW is NOT < OUTLIER_LOW → not an outlier
        rec = make_record(ratio=OUTLIER_LOW)
        cal = write_history(tmp_path, [rec])
        result = get_cost_history({}, calibration_dir=str(cal))
        assert len(result["records"]) == 1


# ---------------------------------------------------------------------------
# TestBandHit
# ---------------------------------------------------------------------------


class TestBandHit:
    def test_band_hit_stored_costs_optimistic(self):
        rec = make_record(ratio=0.3, optimistic_cost=2.0, pessimistic_cost=10.0)
        # actual = 5.0 * 0.3 = 1.5, which is <= optimistic_cost 2.0
        assert _band_hit(rec) == "optimistic"

    def test_band_hit_stored_costs_expected(self):
        rec = make_record(ratio=1.0, optimistic_cost=2.0, pessimistic_cost=10.0)
        # actual = 5.0 * 1.0 = 5.0, > opt_cost 2.0, <= pess_cost 10.0
        assert _band_hit(rec) == "expected"

    def test_band_hit_stored_costs_over_pessimistic(self):
        rec = make_record(ratio=3.0, optimistic_cost=2.0, pessimistic_cost=10.0)
        # actual = 5.0 * 3.0 = 15.0, > pess_cost 10.0
        assert _band_hit(rec) == "over_pessimistic"

    def test_band_hit_ratio_fallback_optimistic(self):
        rec = make_record(ratio=0.5)
        assert _band_hit(rec) == "optimistic"

    def test_band_hit_ratio_fallback_expected(self):
        rec = make_record(ratio=1.5)
        assert _band_hit(rec) == "expected"

    def test_band_hit_ratio_fallback_over_pessimistic(self):
        rec = make_record(ratio=4.0)
        assert _band_hit(rec) == "over_pessimistic"

    def test_band_hit_in_formatted_record(self, tmp_path):
        rec = make_record(ratio=1.5)
        cal = write_history(tmp_path, [rec])
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["records"][0]["band_hit"] == "expected"


# ---------------------------------------------------------------------------
# TestSummaryStatistics
# ---------------------------------------------------------------------------


class TestSummaryStatistics:
    def test_single_record_summary(self, tmp_path):
        rec = make_record(ratio=1.5)
        cal = write_history(tmp_path, [rec])
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        s = result["summary"]
        assert s["session_count"] == 1
        assert s["mean_ratio"] == pytest.approx(1.5)
        assert s["median_ratio"] == pytest.approx(1.5)
        assert s["pct_within_expected"] == pytest.approx(1.0)

    def test_mean_ratio_correct(self, tmp_path):
        ratios = [1.0, 2.0, 3.0]
        records = [make_record(ratio=r) for r in ratios]
        cal = write_history(tmp_path, records)
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["summary"]["mean_ratio"] == pytest.approx(2.0)

    def test_median_ratio_odd_count(self, tmp_path):
        ratios = [1.0, 2.0, 3.0]
        records = [make_record(ratio=r) for r in ratios]
        cal = write_history(tmp_path, records)
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["summary"]["median_ratio"] == pytest.approx(2.0)

    def test_median_ratio_even_count(self, tmp_path):
        ratios = [1.0, 2.0, 3.0, 4.0]
        records = [make_record(ratio=r) for r in ratios]
        cal = write_history(tmp_path, records)
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["summary"]["median_ratio"] == pytest.approx(statistics.median(ratios))

    def test_pct_within_expected_all_in(self, tmp_path):
        records = [make_record(ratio=r) for r in [0.8, 1.0, 1.5, 2.5]]
        cal = write_history(tmp_path, records)
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["summary"]["pct_within_expected"] == pytest.approx(1.0)

    def test_pct_within_expected_some_optimistic(self, tmp_path):
        # ratio=0.4 → optimistic (within), ratio=1.5 → expected (within)
        # both count as "within_expected"
        records = [make_record(ratio=0.4), make_record(ratio=1.5)]
        cal = write_history(tmp_path, records)
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["summary"]["pct_within_expected"] == pytest.approx(1.0)

    def test_pct_within_expected_some_over(self, tmp_path):
        # Need stored costs to produce over_pessimistic without being an outlier
        in_band = make_record(ratio=1.5, optimistic_cost=2.0, pessimistic_cost=10.0)
        over = make_record(ratio=3.5, optimistic_cost=2.0, pessimistic_cost=10.0)
        # actual for over: 5.0*3.5=17.5, > pess_cost 10.0
        cal = write_history(tmp_path, [in_band, over])
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["summary"]["pct_within_expected"] == pytest.approx(0.5)

    def test_session_count_matches_records(self, tmp_path):
        records = [make_record(ratio=r) for r in [1.0, 1.2, 1.4]]
        cal = write_history(tmp_path, records)
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["summary"]["session_count"] == len(result["records"])


# ---------------------------------------------------------------------------
# TestCalibrationDirResolution
# ---------------------------------------------------------------------------


class TestCalibrationDirResolution:
    def test_calibration_dir_from_direct_arg(self, tmp_path):
        cal = write_history(tmp_path, [make_record()])
        result = get_cost_history({"include_outliers": True}, calibration_dir=str(cal))
        assert result["summary"]["session_count"] == 1

    def test_calibration_dir_from_params(self, tmp_path):
        cal = write_history(tmp_path, [make_record()])
        result = get_cost_history(
            {"include_outliers": True, "calibration_dir": str(cal)}
        )
        assert result["summary"]["session_count"] == 1

    def test_direct_arg_takes_precedence_over_params(self, tmp_path):
        cal_a = write_history(tmp_path / "a", [make_record(ratio=1.0)])
        cal_b = write_history(tmp_path / "b", [make_record(ratio=2.0), make_record(ratio=2.1)])
        # Direct arg points at cal_a (1 record), params points at cal_b (2 records)
        result = get_cost_history(
            {"include_outliers": True, "calibration_dir": str(cal_b)},
            calibration_dir=str(cal_a),
        )
        assert result["summary"]["session_count"] == 1


# ---------------------------------------------------------------------------
# TestResolveWindowUnit
# ---------------------------------------------------------------------------


class TestResolveWindowUnit:
    """Unit tests for the _resolve_window helper directly."""

    def _recs(self, n: int, ts: str = _RECENT_TS) -> list:
        return [make_record(ratio=float(i + 1), timestamp=ts) for i in range(n)]

    def test_none_returns_all(self):
        recs = self._recs(5)
        assert _resolve_window(recs, None) == recs

    def test_all_returns_all(self):
        recs = self._recs(5)
        assert _resolve_window(recs, "all") == recs

    def test_count_window(self):
        recs = self._recs(5)
        result = _resolve_window(recs, "3")
        assert result == recs[-3:]

    def test_count_exceeds_length(self):
        recs = self._recs(3)
        result = _resolve_window(recs, "10")
        assert result == recs

    def test_days_window_filters_old(self):
        recent = [make_record(timestamp=_RECENT_TS)]
        old = [make_record(timestamp=_OLD_TS)]
        result = _resolve_window(old + recent, "30d")
        assert len(result) == 1
        assert result[0]["timestamp"] == _RECENT_TS

    def test_invalid_count_returns_all(self):
        recs = self._recs(3)
        assert _resolve_window(recs, "abc") == recs

    def test_invalid_days_returns_all(self):
        recs = self._recs(3)
        assert _resolve_window(recs, "abcd") == recs


# ---------------------------------------------------------------------------
# TestMcpHandler (requires mcp package)
# ---------------------------------------------------------------------------

try:
    from tokencast_mcp.config import ServerConfig  # noqa: E402
    from tokencast_mcp.tools.get_cost_history import (  # noqa: E402
        GET_COST_HISTORY_SCHEMA,
        handle_get_cost_history,
    )

    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False


@pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not available")
class TestMcpHandler:
    def test_schema_has_window_and_include_outliers(self):
        props = GET_COST_HISTORY_SCHEMA["properties"]
        assert "window" in props
        assert "include_outliers" in props

    def test_handler_returns_records_and_summary(self, tmp_path):
        cal = write_history(tmp_path, [make_record(ratio=1.2)])
        config = ServerConfig.from_args(str(cal), None)
        result = asyncio.run(handle_get_cost_history({}, config))
        assert "records" in result
        assert "summary" in result
        assert "_stub" not in result

    def test_handler_empty_history(self, tmp_path):
        cal = write_history(tmp_path, [])
        config = ServerConfig.from_args(str(cal), None)
        result = asyncio.run(handle_get_cost_history({}, config))
        assert result["records"] == []
        assert result["summary"]["session_count"] == 0

    def test_handler_passes_window_to_api(self, tmp_path):
        recent = make_record(ratio=1.2, timestamp=_RECENT_TS)
        old = make_record(ratio=1.5, timestamp=_OLD_TS)
        cal = write_history(tmp_path, [old, recent])
        config = ServerConfig.from_args(str(cal), None)
        result = asyncio.run(
            handle_get_cost_history({"window": "30d", "include_outliers": True}, config)
        )
        assert len(result["records"]) == 1

    def test_handler_uses_config_calibration_dir(self, tmp_path):
        cal_a = write_history(tmp_path / "a", [make_record(ratio=1.0)])
        cal_b = write_history(
            tmp_path / "b",
            [make_record(ratio=2.0), make_record(ratio=2.1)],
        )
        config_a = ServerConfig.from_args(str(cal_a), None)
        result = asyncio.run(
            handle_get_cost_history(
                {"include_outliers": True, "calibration_dir": str(cal_b)}, config_a
            )
        )
        # config.calibration_dir (cal_a) takes precedence over params key
        assert result["summary"]["session_count"] == 1
