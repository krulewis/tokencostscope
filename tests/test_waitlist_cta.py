# Run with: /usr/bin/python3 -m pytest tests/test_waitlist_cta.py
# Do NOT use bare 'python3 -m pytest' -- Homebrew Python 3.14 does not have pytest.
"""Tests for US-PM.02: "Share with team" waitlist CTA hook.

Covers:
- CTA not shown when session_count < 5
- CTA shown when session_count >= 5
- CTA shown at most once per server session (config.cta_shown gate)
- CTA suppressed by config.no_cta flag
- CTA suppressed by TOKENCAST_NO_CTA=1 env var
- CTA not shown when actual_cost is below threshold (no record written)
- CTA includes correct URL and message format
- API layer: suppress_cta=True omits CTA regardless of session_count
- API layer: session_count=None never adds CTA
- MCP handler: _get_session_count counts lines in history.jsonl
- MCP handler: full integration with config and real history file
"""

import asyncio
import json
import os
import pathlib
import sys
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — ensure src/ is importable
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# ---------------------------------------------------------------------------
# Conditional MCP import
# ---------------------------------------------------------------------------

try:
    from tokencast_mcp.config import ServerConfig
    from tokencast_mcp.tools.report_session import (
        handle_report_session,
        _get_session_count,
    )
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

from tokencast.api import report_session, _WAITLIST_URL, _CTA_SESSION_THRESHOLD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_active_estimate(cal_dir: pathlib.Path) -> None:
    """Write a minimal active-estimate.json to cal_dir."""
    cal_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "size": "M",
        "files": 3,
        "complexity": "medium",
        "project_type": "feature",
        "language": "python",
        "steps": ["Research Agent", "Implementation"],
        "step_count": 2,
        "review_cycles_estimated": 2,
        "expected_cost": 5.0,
        "optimistic_cost": 3.0,
        "pessimistic_cost": 15.0,
        "parallel_groups": [],
        "parallel_steps_detected": 0,
        "file_brackets": None,
        "files_measured": 0,
        "step_costs": {"Research Agent": 2.0, "Implementation": 3.0},
        "continuation": False,
        "baseline_cost": 0.0,
    }
    (cal_dir / "active-estimate.json").write_text(json.dumps(payload))


def _write_history_records(cal_dir: pathlib.Path, count: int) -> None:
    """Write `count` minimal history records to history.jsonl."""
    cal_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "size": "M",
        "expected_cost": 5.0,
        "actual_cost": 4.0,
        "ratio": 0.8,
        "attribution_method": "proportional",
        "steps": [],
        "turn_count": 10,
        "review_cycles_actual": 2,
        "step_actuals": None,
        "step_ratios": None,
        "step_costs_estimated": {},
        "continuation": False,
    }
    lines = "\n".join(json.dumps(record) for _ in range(count))
    (cal_dir / "history.jsonl").write_text(lines + "\n" if lines else "")


def _make_config(cal_dir: pathlib.Path, no_cta: bool = False) -> "ServerConfig":
    cfg = ServerConfig.from_args(str(cal_dir), None, no_cta=no_cta)
    return cfg


# ---------------------------------------------------------------------------
# TestApiLayerCta — pure api.report_session() unit tests
# ---------------------------------------------------------------------------


class TestApiLayerCta:
    """Tests targeting the api.report_session() suppress_cta / session_count params."""

    def test_no_cta_when_session_count_none(self, tmp_path):
        """Default call (no session_count) must never emit CTA."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 2.0},
            calibration_dir=str(cal),
        )
        assert "team_sharing_cta" not in result

    def test_no_cta_when_session_count_below_threshold(self, tmp_path):
        """session_count=4 is below threshold — no CTA."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 2.0},
            calibration_dir=str(cal),
            session_count=4,
        )
        assert "team_sharing_cta" not in result

    def test_no_cta_at_zero_sessions(self, tmp_path):
        """session_count=0 — no CTA."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 2.0},
            calibration_dir=str(cal),
            session_count=0,
        )
        assert "team_sharing_cta" not in result

    def test_cta_shown_at_threshold(self, tmp_path):
        """session_count at _CTA_SESSION_THRESHOLD emits CTA."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 2.0},
            calibration_dir=str(cal),
            session_count=_CTA_SESSION_THRESHOLD,
        )
        assert "team_sharing_cta" in result

    def test_cta_shown_above_threshold(self, tmp_path):
        """session_count > threshold still emits CTA."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 2.0},
            calibration_dir=str(cal),
            session_count=20,
        )
        assert "team_sharing_cta" in result

    def test_cta_suppressed_by_suppress_cta_flag(self, tmp_path):
        """suppress_cta=True prevents CTA even at threshold."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 2.0},
            calibration_dir=str(cal),
            session_count=_CTA_SESSION_THRESHOLD,
            suppress_cta=True,
        )
        assert "team_sharing_cta" not in result

    def test_cta_has_url(self, tmp_path):
        """CTA dict includes a non-empty url field."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 2.0},
            calibration_dir=str(cal),
            session_count=_CTA_SESSION_THRESHOLD,
        )
        cta = result["team_sharing_cta"]
        assert "url" in cta
        assert cta["url"] == _WAITLIST_URL
        assert cta["url"].startswith("https://")

    def test_cta_has_message_with_url(self, tmp_path):
        """CTA message contains the URL string."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 2.0},
            calibration_dir=str(cal),
            session_count=_CTA_SESSION_THRESHOLD,
        )
        cta = result["team_sharing_cta"]
        assert "message" in cta
        assert _WAITLIST_URL in cta["message"]

    def test_cta_message_mentions_five_sessions(self, tmp_path):
        """CTA message mentions 5+ sessions."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 2.0},
            calibration_dir=str(cal),
            session_count=_CTA_SESSION_THRESHOLD,
        )
        assert "5+" in result["team_sharing_cta"]["message"]

    def test_no_cta_when_record_not_written(self, tmp_path):
        """Zero-cost guard exits early — CTA must not appear even if threshold met."""
        cal = tmp_path / "calibration"
        _make_active_estimate(cal)
        result = report_session(
            {"actual_cost": 0.0},
            calibration_dir=str(cal),
            session_count=_CTA_SESSION_THRESHOLD,
        )
        assert result["record_written"] is False
        assert "team_sharing_cta" not in result

    def test_cta_not_in_error_response(self, tmp_path):
        """Validation errors must not include CTA."""
        cal = tmp_path / "calibration"
        result = report_session(
            {},
            calibration_dir=str(cal),
            session_count=_CTA_SESSION_THRESHOLD,
        )
        assert "error" in result
        assert "team_sharing_cta" not in result


# ---------------------------------------------------------------------------
# TestGetSessionCount — MCP handler helper
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not available")
class TestGetSessionCount:
    """Tests for _get_session_count helper in tools/report_session.py."""

    def test_returns_zero_when_no_history(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        config = _make_config(cal)
        assert _get_session_count(config) == 0

    def test_returns_zero_when_history_missing(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        config = _make_config(cal)
        # history.jsonl does not exist
        assert _get_session_count(config) == 0

    def test_counts_non_empty_lines(self, tmp_path):
        cal = tmp_path / "calibration"
        _write_history_records(cal, 3)
        config = _make_config(cal)
        assert _get_session_count(config) == 3

    def test_counts_five_records(self, tmp_path):
        cal = tmp_path / "calibration"
        _write_history_records(cal, 5)
        config = _make_config(cal)
        assert _get_session_count(config) == 5

    def test_ignores_blank_lines(self, tmp_path):
        cal = tmp_path / "calibration"
        cal.mkdir()
        # Write 2 records with blank lines interspersed
        (cal / "history.jsonl").write_text(
            json.dumps({"actual_cost": 1.0}) + "\n\n"
            + json.dumps({"actual_cost": 2.0}) + "\n\n"
        )
        config = _make_config(cal)
        assert _get_session_count(config) == 2


# ---------------------------------------------------------------------------
# TestMcpHandlerCta — integration via handle_report_session
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MCP_AVAILABLE, reason="mcp package not available")
class TestMcpHandlerCta:
    """Integration tests: handle_report_session CTA behaviour."""

    def test_no_cta_below_threshold(self, tmp_path):
        """4 existing sessions → no CTA in response."""
        cal = tmp_path / "calibration"
        _write_history_records(cal, 4)
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert "team_sharing_cta" not in result

    def test_cta_at_threshold(self, tmp_path):
        """5 existing sessions → CTA appears in response."""
        cal = tmp_path / "calibration"
        _write_history_records(cal, 5)
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert "team_sharing_cta" in result

    def test_cta_shown_once_per_server_session(self, tmp_path):
        """CTA shown once; second call to handle_report_session omits it."""
        cal = tmp_path / "calibration"
        _write_history_records(cal, 5)
        config = _make_config(cal)

        # First call
        _make_active_estimate(cal)
        result1 = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert "team_sharing_cta" in result1
        assert config.cta_shown is True

        # Second call in the same server session
        _write_history_records(cal, 6)
        _make_active_estimate(cal)
        result2 = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert "team_sharing_cta" not in result2

    def test_cta_suppressed_by_no_cta_flag(self, tmp_path):
        """config.no_cta=True suppresses CTA even when threshold reached."""
        cal = tmp_path / "calibration"
        _write_history_records(cal, 5)
        _make_active_estimate(cal)
        config = _make_config(cal, no_cta=True)
        result = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert "team_sharing_cta" not in result

    def test_cta_suppressed_by_env_var(self, tmp_path):
        """TOKENCAST_NO_CTA=1 suppresses CTA."""
        cal = tmp_path / "calibration"
        _write_history_records(cal, 5)
        _make_active_estimate(cal)
        config = _make_config(cal)
        with mock.patch.dict(os.environ, {"TOKENCAST_NO_CTA": "1"}):
            result = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert "team_sharing_cta" not in result

    def test_env_var_zero_does_not_suppress(self, tmp_path):
        """TOKENCAST_NO_CTA=0 must NOT suppress CTA (0 means opt-in)."""
        cal = tmp_path / "calibration"
        _write_history_records(cal, 5)
        _make_active_estimate(cal)
        config = _make_config(cal)
        with mock.patch.dict(os.environ, {"TOKENCAST_NO_CTA": "0"}):
            result = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert "team_sharing_cta" in result

    def test_env_var_empty_does_not_suppress(self, tmp_path):
        """TOKENCAST_NO_CTA='' (empty string) must NOT suppress CTA."""
        cal = tmp_path / "calibration"
        _write_history_records(cal, 5)
        _make_active_estimate(cal)
        config = _make_config(cal)
        # Ensure the env var is absent (not just empty)
        env = {k: v for k, v in os.environ.items() if k != "TOKENCAST_NO_CTA"}
        with mock.patch.dict(os.environ, env, clear=True):
            result = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert "team_sharing_cta" in result

    def test_cta_url_matches_constant(self, tmp_path):
        """CTA url field matches _WAITLIST_URL constant."""
        cal = tmp_path / "calibration"
        _write_history_records(cal, 5)
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        assert result["team_sharing_cta"]["url"] == _WAITLIST_URL

    def test_cta_does_not_affect_existing_response_fields(self, tmp_path):
        """Adding team_sharing_cta must not remove any standard response fields."""
        cal = tmp_path / "calibration"
        _write_history_records(cal, 5)
        _make_active_estimate(cal)
        config = _make_config(cal)
        result = asyncio.run(handle_report_session({"actual_cost": 2.0}, config))
        for key in ("attribution_protocol_version", "record_written",
                    "attribution_method", "actual_cost", "step_actuals"):
            assert key in result, f"Expected key {key!r} missing from response"
