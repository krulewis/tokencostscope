"""Tests that all package modules introduced in the scripts-packaging fix
(tokencast 0.1.3) are importable and minimally functional in an installed
context (no scripts/ directory required).
"""


def test_calibration_store_importable():
    from tokencast.calibration_store import read_history, append_history
    from tokencast.calibration_store import read_factors, write_factors
    assert callable(read_history)
    assert callable(append_history)
    assert callable(read_factors)
    assert callable(write_factors)


def test_parse_last_estimate_importable():
    from tokencast.parse_last_estimate import parse
    assert callable(parse)


def test_tokencast_status_importable():
    from tokencast.tokencast_status import build_status_output
    assert callable(build_status_output)


def test_update_factors_importable():
    from tokencast.update_factors import update_factors
    assert callable(update_factors)


def test_estimation_engine_importable():
    # This was the highest-severity crash: module-level import failure.
    # Succeeding here means the installed package can serve estimate_cost.
    import tokencast.estimation_engine  # must not raise
    assert hasattr(tokencast.estimation_engine, '_read_factors')


def test_api_importable():
    import tokencast.api  # must not raise
    from tokencast.api import estimate_cost, get_calibration_status
    from tokencast.api import get_cost_history, report_session, report_step_cost
    assert callable(estimate_cost)


def test_read_history_empty_path(tmp_path):
    from tokencast.calibration_store import read_history
    # Non-existent file returns empty list (not an exception)
    result = read_history(str(tmp_path / "nonexistent.jsonl"))
    assert result == []


def test_read_factors_empty_path(tmp_path):
    from tokencast.calibration_store import read_factors
    result = read_factors(str(tmp_path / "nonexistent.json"))
    assert result == {}


def test_build_status_output_none_heuristics():
    # heuristics_path=None must not raise
    from tokencast.tokencast_status import build_status_output
    # Empty records/factors -- returns not-enough-data response, no crash
    result = build_status_output([], {}, heuristics_path=None)
    assert isinstance(result, dict)
    assert 'health' in result


def test_parse_returns_none_on_empty():
    from tokencast.parse_last_estimate import parse
    # Invalid content returns None without raising
    result = parse("no valid fields here", mtime=None)
    assert result is None


def test_update_factors_noop_on_missing_history(tmp_path):
    from tokencast.update_factors import update_factors
    # Non-existent history file is a no-op (not an exception)
    update_factors(
        str(tmp_path / "nonexistent.jsonl"),
        str(tmp_path / "factors.json"),
    )
    # factors.json should NOT be created (no data to compute from)
    assert not (tmp_path / "factors.json").exists()
