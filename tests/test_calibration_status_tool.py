# Run with: /usr/bin/python3 -m pytest tests/test_calibration_status_tool.py
"""Tests for the get_calibration_status MCP tool (US-1b.05).

Covers:
- tokencast.api.get_calibration_status with empty calibration dir
- tokencast.api.get_calibration_status with populated history
- window parameter parsing (30d, numeric, all)
- error handling for malformed files
- MCP tool handler returns schema_version=1 and text_summary
- MCP tool handler forwards calibration_dir from ServerConfig
"""

import asyncio
import json
import pathlib
import sys
import tempfile

import pytest

# ---------------------------------------------------------------------------
# Import the public API directly (no MCP dependency needed for unit tests)
# ---------------------------------------------------------------------------
REPO_ROOT = pathlib.Path('/Volumes/Macintosh HD2/Cowork/Projects/costscope')
sys.path.insert(0, str(REPO_ROOT / 'src'))

from tokencast.api import get_calibration_status  # noqa: E402

# MCP scaffold tests require the mcp package; skip gracefully if absent.
try:
    from tokencast_mcp.config import ServerConfig
    from tokencast_mcp.tools.get_calibration_status import (
        GET_CALIBRATION_STATUS_SCHEMA,
        handle_get_calibration_status,
        _format_text_summary,
    )
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_record(ratio=1.2, size='M', timestamp='2026-01-01T00:00:00Z') -> dict:
    expected = 5.0
    actual = expected * ratio
    return {
        'timestamp': timestamp,
        'size': size,
        'complexity': 'medium',
        'expected_cost': expected,
        'actual_cost': actual,
        'ratio': ratio,
        'project_type': 'greenfield',
        'language': 'python',
        'steps': ['Implementation'],
        'step_count': 1,
        'review_cycles_estimated': 2,
        'optimistic_cost': expected * 0.5,
        'pessimistic_cost': expected * 3.0,
    }


def write_history(calibration_dir: str, records: list) -> None:
    path = pathlib.Path(calibration_dir) / 'history.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')


def write_factors(calibration_dir: str, factors: dict) -> None:
    path = pathlib.Path(calibration_dir) / 'factors.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(factors, f)


# ---------------------------------------------------------------------------
# TestApiEmptyCalibrationDir
# ---------------------------------------------------------------------------


class TestApiEmptyCalibrationDir:
    def test_returns_schema_version_1(self, tmp_path):
        result = get_calibration_status({}, calibration_dir=str(tmp_path))
        assert result['schema_version'] == 1

    def test_status_is_no_data(self, tmp_path):
        result = get_calibration_status({}, calibration_dir=str(tmp_path))
        health = result['health']
        assert health['status'] == 'no_data'

    def test_accuracy_is_none(self, tmp_path):
        result = get_calibration_status({}, calibration_dir=str(tmp_path))
        assert result['accuracy'] is None

    def test_cost_attribution_is_none(self, tmp_path):
        result = get_calibration_status({}, calibration_dir=str(tmp_path))
        assert result['cost_attribution'] is None

    def test_outliers_is_none(self, tmp_path):
        result = get_calibration_status({}, calibration_dir=str(tmp_path))
        assert result['outliers'] is None

    def test_recommendations_is_empty_list(self, tmp_path):
        result = get_calibration_status({}, calibration_dir=str(tmp_path))
        assert result['recommendations'] == []

    def test_window_spec_is_none(self, tmp_path):
        result = get_calibration_status({}, calibration_dir=str(tmp_path))
        assert result['window']['spec'] is None

    def test_total_records_is_zero(self, tmp_path):
        result = get_calibration_status({}, calibration_dir=str(tmp_path))
        assert result['window']['total_records'] == 0

    def test_nonexistent_dir_returns_no_data(self, tmp_path):
        nonexistent = str(tmp_path / 'does' / 'not' / 'exist')
        result = get_calibration_status({}, calibration_dir=nonexistent)
        assert result['schema_version'] == 1
        assert result['health']['status'] == 'no_data'

    def test_window_param_ignored_on_empty(self, tmp_path):
        result = get_calibration_status({'window': '30d'}, calibration_dir=str(tmp_path))
        assert result['schema_version'] == 1
        assert result['health']['status'] == 'no_data'


# ---------------------------------------------------------------------------
# TestApiWithPopulatedHistory
# ---------------------------------------------------------------------------


class TestApiWithPopulatedHistory:
    def _make_cal_dir(self, tmp_path, records, factors=None):
        cal = str(tmp_path / 'calibration')
        write_history(cal, records)
        if factors is not None:
            write_factors(cal, factors)
        return cal

    def test_returns_schema_version_1(self, tmp_path):
        records = [make_record(ratio=1.1) for _ in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['schema_version'] == 1

    def test_collecting_when_fewer_than_3_clean_records(self, tmp_path):
        records = [make_record(ratio=1.1), make_record(ratio=1.2)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        # 2 clean records → collecting
        assert result['health']['status'] in ('collecting', 'no_data')

    def test_active_when_3_or_more_clean_records(self, tmp_path):
        records = [make_record(ratio=1.0 + i * 0.05) for i in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['health']['status'] == 'active'

    def test_accuracy_section_present_when_active(self, tmp_path):
        records = [make_record(ratio=1.0 + i * 0.05) for i in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['accuracy'] is not None

    def test_accuracy_has_mean_ratio(self, tmp_path):
        records = [make_record(ratio=1.0 + i * 0.05) for i in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert 'mean_ratio' in result['accuracy']

    def test_accuracy_has_trend(self, tmp_path):
        records = [make_record(ratio=1.0 + i * 0.05) for i in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert 'trend' in result['accuracy']

    def test_total_records_matches_history(self, tmp_path):
        records = [make_record() for _ in range(7)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['window']['total_records'] == 7

    def test_no_params_returns_all_fields(self, tmp_path):
        records = [make_record(ratio=1.0 + i * 0.05) for i in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert 'health' in result
        assert 'accuracy' in result
        assert 'cost_attribution' in result
        assert 'outliers' in result
        assert 'recommendations' in result
        assert 'window' in result
        assert 'meta' in result

    def test_outliers_section_present_when_active(self, tmp_path):
        records = [make_record(ratio=1.0 + i * 0.05) for i in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['outliers'] is not None

    def test_outlier_detected(self, tmp_path):
        # One extreme outlier (ratio > 3.0), rest are clean
        records = [make_record(ratio=1.1) for _ in range(5)]
        records.append(make_record(ratio=10.0))
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['outliers']['count'] >= 1


# ---------------------------------------------------------------------------
# TestApiWindowParameter
# ---------------------------------------------------------------------------


class TestApiWindowParameter:
    def _make_cal_dir(self, tmp_path, records):
        cal = str(tmp_path / 'calibration')
        write_history(cal, records)
        return cal

    def test_window_30d_spec_stored_in_result(self, tmp_path):
        records = [make_record() for _ in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({'window': '30d'}, calibration_dir=cal)
        assert result['window']['spec'] == '30d'

    def test_window_numeric_spec_stored(self, tmp_path):
        records = [make_record() for _ in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({'window': '3'}, calibration_dir=cal)
        assert result['window']['spec'] == '3'

    def test_window_all_returns_all_records(self, tmp_path):
        records = [make_record() for _ in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({'window': 'all'}, calibration_dir=cal)
        assert result['window']['spec'] == 'all'
        assert result['window']['total_records'] == 5

    def test_no_window_param_is_adaptive(self, tmp_path):
        records = [make_record() for _ in range(5)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['window']['spec'] is None

    def test_window_numeric_limits_window_records(self, tmp_path):
        records = [make_record(ratio=1.0 + i * 0.05) for i in range(10)]
        cal = self._make_cal_dir(tmp_path, records)
        result = get_calibration_status({'window': '3'}, calibration_dir=cal)
        # total always shows full history; records_in_window is limited
        assert result['window']['total_records'] == 10
        # When fewer than 3 clean records in no_data/collecting, records_in_window=0
        # When active (3+ clean records), records_in_window=3
        assert result['window']['records_in_window'] in (0, 3)


# ---------------------------------------------------------------------------
# TestApiErrorHandling
# ---------------------------------------------------------------------------


class TestApiErrorHandling:
    def test_malformed_history_json_returns_no_data_or_collecting(self, tmp_path):
        cal = str(tmp_path / 'calibration')
        pathlib.Path(cal).mkdir(parents=True)
        history_file = pathlib.Path(cal) / 'history.jsonl'
        # Fully invalid JSON lines are skipped; partial-but-valid JSON objects
        # are kept as records (with missing required fields, they count toward
        # "collecting" but not "active").
        history_file.write_text('not valid json\n{"key": "partial"}\n')
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['schema_version'] == 1
        assert result['health']['status'] in ('no_data', 'collecting')

    def test_malformed_factors_json_uses_empty_factors(self, tmp_path):
        cal = str(tmp_path / 'calibration')
        pathlib.Path(cal).mkdir(parents=True)
        # Valid history with 5 clean records
        records = [make_record(ratio=1.0 + i * 0.05) for i in range(5)]
        write_history(cal, records)
        # Corrupt factors file
        factors_file = pathlib.Path(cal) / 'factors.json'
        factors_file.write_text('{not valid json')
        # Should not raise — falls back to empty factors
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['schema_version'] == 1

    def test_default_calibration_dir_used_when_none(self):
        # Passing no calibration_dir should not raise; uses ~/.tokencast/calibration
        result = get_calibration_status({})
        assert result['schema_version'] == 1

    def test_empty_history_file_returns_no_data(self, tmp_path):
        cal = str(tmp_path / 'calibration')
        pathlib.Path(cal).mkdir(parents=True)
        (pathlib.Path(cal) / 'history.jsonl').write_text('')
        result = get_calibration_status({}, calibration_dir=cal)
        assert result['health']['status'] == 'no_data'

    def test_result_always_has_schema_version(self, tmp_path):
        # Verify schema_version=1 is always present (no exception path omits it)
        result = get_calibration_status({}, calibration_dir=str(tmp_path))
        assert result.get('schema_version') == 1


# ---------------------------------------------------------------------------
# TestMcpToolHandler — requires mcp package
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MCP_AVAILABLE, reason='mcp package not available')
class TestMcpToolHandler:
    def _make_config(self, tmp_path):
        return ServerConfig.from_args(str(tmp_path / 'calibration'), None)

    def test_returns_schema_version_1(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        assert result['schema_version'] == 1

    def test_no_stub_flag(self, tmp_path):
        """Real implementation must not return _stub=True."""
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        assert '_stub' not in result or result.get('_stub') is not True

    def test_returns_text_summary(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        assert 'text_summary' in result
        assert isinstance(result['text_summary'], str)
        assert len(result['text_summary']) > 0

    def test_text_summary_mentions_status(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        # Should mention some kind of status word
        summary = result['text_summary'].lower()
        assert any(word in summary for word in ('no_data', 'collecting', 'active', 'status'))

    def test_accepts_window_param(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({'window': '7d'}, config))
        assert result['schema_version'] == 1

    def test_uses_config_calibration_dir(self, tmp_path):
        # Write some history into the config's calibration dir
        cal_dir = tmp_path / 'calibration'
        cal_dir.mkdir()
        records = [make_record(ratio=1.0 + i * 0.05) for i in range(5)]
        write_history(str(cal_dir), records)

        config = ServerConfig.from_args(str(cal_dir), None)
        result = asyncio.run(handle_get_calibration_status({}, config))
        # Should find the 5 records we wrote
        assert result['window']['total_records'] == 5

    def test_health_key_present(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        assert 'health' in result

    def test_empty_dir_returns_no_data_health(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        assert result['health']['status'] == 'no_data'

    def test_window_field_in_result(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        assert 'window' in result

    def test_recommendations_is_list(self, tmp_path):
        config = self._make_config(tmp_path)
        result = asyncio.run(handle_get_calibration_status({}, config))
        assert isinstance(result['recommendations'], list)


# ---------------------------------------------------------------------------
# TestFormatTextSummary — unit tests for the summary helper
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _MCP_AVAILABLE, reason='mcp package not available')
class TestFormatTextSummary:
    def test_includes_status(self):
        result = {
            'health': {'status': 'no_data', 'message': 'No calibration data yet.'},
            'accuracy': None,
            'outliers': None,
            'recommendations': [],
            'window': {'spec': None, 'records_in_window': 0, 'total_records': 0},
        }
        summary = _format_text_summary(result)
        assert 'no_data' in summary

    def test_includes_mean_ratio_when_present(self):
        result = {
            'health': {'status': 'active', 'message': 'Active.'},
            'accuracy': {
                'mean_ratio': 1.15,
                'trend': 'stable',
                'pct_within_expected': 0.8,
            },
            'outliers': {'count': 0},
            'recommendations': [],
            'window': {'spec': None, 'records_in_window': 5, 'total_records': 5},
        }
        summary = _format_text_summary(result)
        assert '1.15' in summary

    def test_includes_outlier_count_when_nonzero(self):
        result = {
            'health': {'status': 'active', 'message': 'Active.'},
            'accuracy': None,
            'outliers': {'count': 2, 'outlier_rate': 0.4},
            'recommendations': [],
            'window': {'spec': None, 'records_in_window': 5, 'total_records': 5},
        }
        summary = _format_text_summary(result)
        assert 'Outlier' in summary or 'outlier' in summary

    def test_no_outlier_line_when_zero_outliers(self):
        result = {
            'health': {'status': 'active', 'message': 'Active.'},
            'accuracy': None,
            'outliers': {'count': 0, 'outlier_rate': 0.0},
            'recommendations': [],
            'window': {'spec': None, 'records_in_window': 5, 'total_records': 5},
        }
        summary = _format_text_summary(result)
        # With zero outliers the outlier line should be absent
        assert 'Outlier' not in summary and 'outlier' not in summary

    def test_handles_missing_accuracy(self):
        result = {
            'health': {'status': 'collecting', 'message': 'Collecting.'},
            'accuracy': None,
            'outliers': None,
            'recommendations': [],
            'window': {'spec': None, 'records_in_window': 0, 'total_records': 2},
        }
        # Should not raise
        summary = _format_text_summary(result)
        assert isinstance(summary, str)

    def test_shows_recommendation_count(self):
        result = {
            'health': {'status': 'active', 'message': 'Active.'},
            'accuracy': None,
            'outliers': {'count': 0},
            'recommendations': [
                {'type': 'review_cycles_high', 'description': 'Too many review cycles.', 'priority': 'accuracy'},
                {'type': 'stale_pricing', 'description': 'Pricing is stale.', 'priority': 'informational'},
            ],
            'window': {'spec': None, 'records_in_window': 5, 'total_records': 5},
        }
        summary = _format_text_summary(result)
        assert 'Recommendation' in summary or 'recommendation' in summary

    def test_truncates_long_recommendation_description(self):
        long_desc = 'A' * 200
        result = {
            'health': {'status': 'active', 'message': 'Active.'},
            'accuracy': None,
            'outliers': {'count': 0},
            'recommendations': [
                {'type': 'some_type', 'description': long_desc, 'priority': 'guidance'},
            ],
            'window': {'spec': None, 'records_in_window': 5, 'total_records': 5},
        }
        summary = _format_text_summary(result)
        # The description should be truncated with ellipsis
        assert '...' in summary


# ---------------------------------------------------------------------------
# TestMcpSchemaConstant — no mcp package needed for this
# ---------------------------------------------------------------------------


class TestMcpSchemaConstant:
    @pytest.mark.skipif(not _MCP_AVAILABLE, reason='mcp package not available')
    def test_schema_type_is_object(self):
        assert GET_CALIBRATION_STATUS_SCHEMA['type'] == 'object'

    @pytest.mark.skipif(not _MCP_AVAILABLE, reason='mcp package not available')
    def test_schema_has_window_property(self):
        assert 'window' in GET_CALIBRATION_STATUS_SCHEMA['properties']

    @pytest.mark.skipif(not _MCP_AVAILABLE, reason='mcp package not available')
    def test_schema_has_additional_properties_false(self):
        assert GET_CALIBRATION_STATUS_SCHEMA.get('additionalProperties') is False
