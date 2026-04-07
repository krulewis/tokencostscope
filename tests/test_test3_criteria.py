# Run with: /usr/bin/python3 -m pytest tests/test_test3_criteria.py
"""Tests for Test 3 success criteria document (v0.1.6).

Verifies that references/test3-success-criteria.md exists, contains all
four required criteria sections, and that numeric thresholds are present
and within sensible ranges.

The four criteria:
  (a) Engaged calibration ratio  — report_session / estimate_cost call ratio
  (b) Statistical confidence     — minimum calibration record count
  (c) Band accuracy              — % of actuals landing inside [optimistic, pessimistic]
  (d) Quality install            — installs with N+ sessions sub-metric
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CRITERIA_PATH = REPO_ROOT / "references" / "test3-success-criteria.md"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_criteria() -> str:
    """Return the criteria file content, skipping if file is absent."""
    if not CRITERIA_PATH.exists():
        pytest.skip(f"Criteria file not yet created: {CRITERIA_PATH}")
    return CRITERIA_PATH.read_text(encoding="utf-8")


def _extract_value(text: str, key: str) -> str:
    """Extract a value from a line like '| key | value |' or 'key = value'."""
    # Try table row: | key | value |
    pattern_table = rf"\|\s*{re.escape(key)}\s*\|\s*([^\|]+?)\s*\|"
    m = re.search(pattern_table, text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # Try assignment: key = value  or  key: value
    pattern_assign = rf"{re.escape(key)}\s*[=:]\s*([^\n]+)"
    m = re.search(pattern_assign, text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Existence and structure
# ---------------------------------------------------------------------------

class TestFileExists:
    def test_criteria_file_exists(self):
        assert CRITERIA_PATH.exists(), (
            f"references/test3-success-criteria.md must exist — "
            f"not found at {CRITERIA_PATH}"
        )

    def test_criteria_file_not_empty(self):
        content = _read_criteria()
        assert len(content.strip()) > 100, "Criteria file appears empty or too short"

    def test_has_title(self):
        content = _read_criteria()
        assert "Test 3" in content, "File must mention 'Test 3' in the title or header"

    def test_has_measurement_window_reference(self):
        content = _read_criteria()
        assert re.search(r"(measurement.window|4.week|march)", content, re.IGNORECASE), (
            "File must reference the measurement window or start date"
        )


# ---------------------------------------------------------------------------
# (a) Engaged calibration ratio
# ---------------------------------------------------------------------------

class TestEngagedCalibrationRatio:
    """Criterion (a): report_session / estimate_cost call ratio per user."""

    def test_section_present(self):
        content = _read_criteria()
        assert re.search(r"engaged.calibration|report_session.*ratio|ratio.*report_session",
                         content, re.IGNORECASE), (
            "Criteria doc must contain an 'engaged calibration ratio' section"
        )

    def test_ratio_key_present(self):
        content = _read_criteria()
        assert re.search(r"report_session_ratio_min|ratio_min|minimum.*ratio",
                         content, re.IGNORECASE), (
            "Must define a minimum report_session/estimate_cost ratio threshold"
        )

    def test_ratio_value_is_numeric(self):
        content = _read_criteria()
        val = _extract_value(content, "report_session_ratio_min")
        if not val:
            # Try any line with ratio_min
            m = re.search(r"ratio_min\s*[=:]\s*([\d.]+)", content, re.IGNORECASE)
            assert m, "Could not extract a numeric ratio_min value"
            val = m.group(1)
        ratio = float(val.split()[0])  # handle trailing comments
        assert 0.0 < ratio <= 1.0, f"report_session_ratio_min must be in (0, 1], got {ratio}"

    def test_ratio_above_meaningful_minimum(self):
        """Ratio of 0.1 is too low to indicate genuine calibration engagement."""
        content = _read_criteria()
        m = re.search(r"report_session_ratio_min\s*[=:]\s*([\d.]+)", content, re.IGNORECASE)
        if not m:
            m = re.search(r"ratio_min\s*[=:]\s*([\d.]+)", content, re.IGNORECASE)
        assert m, "Must define report_session_ratio_min"
        assert float(m.group(1)) >= 0.3, (
            "report_session_ratio_min should be >= 0.3 to be a meaningful engagement bar"
        )


# ---------------------------------------------------------------------------
# (b) Statistical confidence — minimum sample count
# ---------------------------------------------------------------------------

class TestStatisticalConfidence:
    """Criterion (b): minimum calibration record count for statistical confidence."""

    def test_section_present(self):
        content = _read_criteria()
        assert re.search(r"statistical.confidence|sample.count|calibration.records",
                         content, re.IGNORECASE), (
            "Criteria doc must contain a statistical confidence / sample count section"
        )

    def test_min_sample_key_present(self):
        content = _read_criteria()
        assert re.search(r"calibration_records_min|min.*sample|sample.*min|records_min",
                         content, re.IGNORECASE), (
            "Must define a minimum calibration record count"
        )

    def test_min_sample_value_is_integer(self):
        content = _read_criteria()
        m = re.search(r"calibration_records_min\s*[=:]\s*(\d+)", content, re.IGNORECASE)
        if not m:
            m = re.search(r"records_min\s*[=:]\s*(\d+)", content, re.IGNORECASE)
        assert m, "Must define calibration_records_min as an integer"
        n = int(m.group(1))
        assert n >= 3, "calibration_records_min must be >= 3 (the algorithm activation threshold)"

    def test_min_sample_exceeds_activation_threshold(self):
        """Test 3 confidence bar should be higher than the 3-record algorithm activation."""
        content = _read_criteria()
        m = re.search(r"calibration_records_min\s*[=:]\s*(\d+)", content, re.IGNORECASE)
        if not m:
            m = re.search(r"records_min\s*[=:]\s*(\d+)", content, re.IGNORECASE)
        assert m, "Must define calibration_records_min"
        assert int(m.group(1)) >= 5, (
            "calibration_records_min should be > 3 (algorithm activation threshold) "
            "for meaningful statistical confidence"
        )


# ---------------------------------------------------------------------------
# (c) Band accuracy target
# ---------------------------------------------------------------------------

class TestBandAccuracy:
    """Criterion (c): % of actuals landing within [optimistic, pessimistic] band."""

    def test_section_present(self):
        content = _read_criteria()
        assert re.search(r"band.accuracy|within.band|band.hit|actuals.*band",
                         content, re.IGNORECASE), (
            "Criteria doc must contain a band accuracy section"
        )

    def test_band_accuracy_key_present(self):
        content = _read_criteria()
        assert re.search(r"band_hit_rate_min|band_accuracy_min|accuracy.*min|min.*accuracy",
                         content, re.IGNORECASE), (
            "Must define a minimum band hit rate / accuracy target"
        )

    def test_band_accuracy_is_fraction(self):
        """Value should be a fraction 0–1 (e.g., 0.80) not a percentage (e.g., 80)."""
        content = _read_criteria()
        m = re.search(r"band_hit_rate_min\s*[=:]\s*([\d.]+)", content, re.IGNORECASE)
        if not m:
            m = re.search(r"band_accuracy_min\s*[=:]\s*([\d.]+)", content, re.IGNORECASE)
        assert m, "Must define band_hit_rate_min as a decimal fraction"
        val = float(m.group(1))
        # Accept both fraction (0.80) and percentage (80.0) but prefer fraction
        if val > 1.0:
            val = val / 100.0
        assert 0.5 <= val <= 1.0, f"band_hit_rate_min must be between 0.5 and 1.0, got {val}"

    def test_band_accuracy_target_is_ambitious(self):
        """Target should be >= 70% to be a meaningful accuracy bar."""
        content = _read_criteria()
        m = re.search(r"band_hit_rate_min\s*[=:]\s*([\d.]+)", content, re.IGNORECASE)
        if not m:
            m = re.search(r"band_accuracy_min\s*[=:]\s*([\d.]+)", content, re.IGNORECASE)
        assert m, "Must define band_hit_rate_min"
        val = float(m.group(1))
        if val > 1.0:
            val = val / 100.0
        assert val >= 0.70, f"band_hit_rate_min should be >= 0.70 (70%), got {val:.0%}"


# ---------------------------------------------------------------------------
# (d) Quality install sub-metric
# ---------------------------------------------------------------------------

class TestQualityInstall:
    """Criterion (d): installs with 3+ sessions = quality install."""

    def test_section_present(self):
        content = _read_criteria()
        assert re.search(r"quality.install|quality.*install|install.*quality",
                         content, re.IGNORECASE), (
            "Criteria doc must contain a quality install section"
        )

    def test_quality_install_count_present(self):
        content = _read_criteria()
        assert re.search(r"quality_install_min_installs|min.*installs|installs.*min",
                         content, re.IGNORECASE), (
            "Must define a minimum quality install count target"
        )

    def test_quality_install_session_threshold_present(self):
        content = _read_criteria()
        assert re.search(r"quality_install_min_sessions|min.*sessions|sessions.*min",
                         content, re.IGNORECASE), (
            "Must define how many sessions constitute a 'quality install'"
        )

    def test_quality_install_session_threshold_is_3(self):
        """ROADMAP.md already established 3+ sessions = quality install."""
        content = _read_criteria()
        m = re.search(r"quality_install_min_sessions\s*[=:]\s*(\d+)", content, re.IGNORECASE)
        if not m:
            m = re.search(r"min_sessions\s*[=:]\s*(\d+)", content, re.IGNORECASE)
        assert m, "Must define quality_install_min_sessions"
        assert int(m.group(1)) == 3, (
            "quality_install_min_sessions must be 3 (per ROADMAP.md v0.1.5 decision)"
        )

    def test_quality_install_count_target(self):
        """ROADMAP.md established 100 quality installs as the gate."""
        content = _read_criteria()
        m = re.search(r"quality_install_min_installs\s*[=:]\s*(\d+)", content, re.IGNORECASE)
        if not m:
            m = re.search(r"min_installs\s*[=:]\s*(\d+)", content, re.IGNORECASE)
        assert m, "Must define quality_install_min_installs"
        assert int(m.group(1)) == 100, (
            "quality_install_min_installs must be 100 (per ROADMAP.md v0.1.5 decision)"
        )


# ---------------------------------------------------------------------------
# Machine-readability: key-value lines parseable by status tool
# ---------------------------------------------------------------------------

class TestMachineReadability:
    """Verify that key thresholds appear as parseable key = value lines."""

    REQUIRED_KEYS = [
        "report_session_ratio_min",
        "calibration_records_min",
        "band_hit_rate_min",
        "quality_install_min_installs",
        "quality_install_min_sessions",
    ]

    def test_all_required_keys_present(self):
        content = _read_criteria()
        missing = []
        for key in self.REQUIRED_KEYS:
            if not re.search(rf"{re.escape(key)}\s*[=:]", content, re.IGNORECASE):
                missing.append(key)
        assert not missing, (
            f"Missing machine-readable key(s) in criteria file: {missing}\n"
            "Each threshold must appear as 'key = value' or 'key: value' "
            "so the status tool can parse it."
        )

    def test_all_key_values_are_numeric(self):
        content = _read_criteria()
        for key in self.REQUIRED_KEYS:
            m = re.search(rf"{re.escape(key)}\s*[=:]\s*([\d.]+)", content, re.IGNORECASE)
            assert m, f"Value for '{key}' must be a plain number (found no numeric match)"
            val = m.group(1)
            # Should parse as float without error
            float(val)
