"""Public API for tokencast — stable dict-based contract.

MCP tool handlers are thin wrappers that call these functions and format the
result for MCP. Later stories (US-1b.04–1b.07) replace the stub bodies with
real implementations.
"""

import hashlib
import importlib.util
import json
import os
import pathlib
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from tokencast.pricing import DEFAULT_MODEL, STEP_MODEL_MAP, compute_cost_from_usage
from tokencast.step_names import resolve_step_name

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTLIER_HIGH = 3.0
OUTLIER_LOW = 0.2

# ---------------------------------------------------------------------------
# Lazy-load tokencast-status.py (filename has a hyphen — cannot be imported
# with normal import machinery).
# ---------------------------------------------------------------------------

_STATUS_MODULE = None


def _load_status_module():
    global _STATUS_MODULE
    if _STATUS_MODULE is not None:
        return _STATUS_MODULE
    scripts_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
    status_path = scripts_dir / "tokencast-status.py"
    spec = importlib.util.spec_from_file_location("tokencast_status", status_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _STATUS_MODULE = mod
    return mod


_CALIBRATION_STORE_MODULE = None
_PARSE_LAST_ESTIMATE_MODULE = None


def _load_calibration_store():
    """Load calibration_store.py from the scripts directory (cached)."""
    global _CALIBRATION_STORE_MODULE
    if _CALIBRATION_STORE_MODULE is not None:
        return _CALIBRATION_STORE_MODULE
    scripts_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
    cs_path = scripts_dir / "calibration_store.py"
    spec = importlib.util.spec_from_file_location("calibration_store", cs_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _CALIBRATION_STORE_MODULE = mod
    return mod


def _load_parse_last_estimate():
    """Load parse_last_estimate.py from the scripts directory (cached)."""
    global _PARSE_LAST_ESTIMATE_MODULE
    if _PARSE_LAST_ESTIMATE_MODULE is not None:
        return _PARSE_LAST_ESTIMATE_MODULE
    scripts_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
    ple_path = scripts_dir / "parse_last_estimate.py"
    spec = importlib.util.spec_from_file_location("parse_last_estimate", ple_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _PARSE_LAST_ESTIMATE_MODULE = mod
    return mod


# ---------------------------------------------------------------------------
# History helpers (mirrors tokencast-status.py)
# ---------------------------------------------------------------------------


def _get_ratio(record: dict) -> float:
    ratio = record.get("ratio")
    if ratio is not None:
        return float(ratio)
    return record.get("actual_cost", 0) / max(record.get("expected_cost", 0.001), 0.001)


def _is_outlier(record: dict) -> bool:
    r = _get_ratio(record)
    return r > OUTLIER_HIGH or r < OUTLIER_LOW


def _band_hit(record: dict) -> str:
    actual = record.get("actual_cost", 0)
    opt_cost = record.get("optimistic_cost")
    pess_cost = record.get("pessimistic_cost")
    if opt_cost is not None and pess_cost is not None:
        if actual <= opt_cost:
            return "optimistic"
        if actual <= pess_cost:
            return "expected"
        return "over_pessimistic"
    r = _get_ratio(record)
    if r <= 0.6:
        return "optimistic"
    if r <= 3.0:
        return "expected"
    return "over_pessimistic"


def _record_timestamp(record: dict) -> float:
    ts = record.get("timestamp", "")
    if not ts:
        return 0.0
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _resolve_window(records: list, window_spec: Optional[str]) -> list:
    """Filter records by window_spec.

    "Nd" → last N days; "N" (numeric) → last N records; "all" / None → everything.
    """
    if window_spec is None or window_spec == "all":
        return list(records)

    spec = str(window_spec).strip()
    if spec.endswith("d"):
        try:
            n_days = int(spec[:-1])
        except ValueError:
            return list(records)
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - n_days * 86400
        return [r for r in records if _record_timestamp(r) >= cutoff]
    else:
        try:
            n = int(spec)
            return records[-n:] if n < len(records) else list(records)
        except ValueError:
            return list(records)


def _format_record(record: dict) -> dict:
    """Return a cleaned-up summary of a history record for API consumers."""
    return {
        "timestamp": record.get("timestamp"),
        "size": record.get("size"),
        "expected_cost": record.get("expected_cost"),
        "actual_cost": record.get("actual_cost"),
        "ratio": _get_ratio(record),
        "steps": record.get("steps", []),
        "band_hit": _band_hit(record),
        "attribution_method": record.get("attribution_method"),
    }


def _compute_summary(records: list) -> dict:
    """Compute aggregate statistics over a list of records."""
    if not records:
        return {
            "session_count": 0,
            "mean_ratio": None,
            "median_ratio": None,
            "pct_within_expected": None,
        }
    ratios = [_get_ratio(r) for r in records]
    within = [r for r in records if _band_hit(r) in ("optimistic", "expected")]
    return {
        "session_count": len(records),
        "mean_ratio": sum(ratios) / len(ratios),
        "median_ratio": statistics.median(ratios),
        "pct_within_expected": len(within) / len(records),
    }


def estimate_cost(
    params: dict,
    calibration_dir: Optional[str] = None,
    project_dir: Optional[str] = None,
) -> dict:
    """Estimate Anthropic API token costs for a development plan.

    Handles file measurement (when file_paths are provided), runs the
    estimation engine, and persists active-estimate.json and last-estimate.md
    to calibration_dir (when provided).

    Args:
        params: Dict with keys ``size``, ``files``, ``complexity``, and
            optional fields defined in the MCP tool schema.
        calibration_dir: Path to the calibration directory. When ``None``,
            calibration factors default to {} and no files are written.
        project_dir: Project root for resolving relative file paths during
            file measurement. When ``None``, paths are resolved as-is.

    Returns:
        Dict with keys ``version``, ``estimate``, ``steps``, ``metadata``,
        and ``step_costs``.
    """
    import tempfile

    from tokencast.estimation_engine import compute_estimate

    global _step_accumulator, _accumulator_file_path
    _step_accumulator = {}
    _accumulator_file_path = None

    # --- File measurement pre-processing ---
    file_paths = params.get("file_paths") or []
    if file_paths:
        from tokencast.file_measurement import measure_files
        measurement = measure_files(file_paths, project_dir=project_dir)
        params = dict(params)  # avoid mutating caller's dict
        params["file_brackets"]        = measurement["brackets"]
        params["avg_file_read_tokens"] = measurement["avg_file_read_tokens"]
        params["avg_file_edit_tokens"] = measurement["avg_file_edit_tokens"]
        params["files_measured"]       = measurement["files_measured"]

    result = compute_estimate(params, calibration_dir=calibration_dir)

    # --- Persist calibration files (only when calibration_dir is provided) ---
    if calibration_dir is not None:
        cal_path = Path(calibration_dir)
        active_estimate_path = cal_path / "active-estimate.json"
        last_estimate_path = cal_path / "last-estimate.md"

        meta = result["metadata"]
        step_costs = result.get("step_costs", {})
        estimate_bands = result["estimate"]
        all_step_names = [s["name"] for s in result["steps"]]

        active_estimate = {
            "timestamp":               datetime.now(timezone.utc).isoformat(),
            "size":                    meta["size"],
            "files":                   meta["files"],
            "complexity":              meta["complexity"],
            "steps":                   all_step_names,
            "step_count":              len(all_step_names),
            "project_type":            meta["project_type"],
            "language":                meta["language"],
            "expected_cost":           estimate_bands["expected"],
            "optimistic_cost":         estimate_bands["optimistic"],
            "pessimistic_cost":        estimate_bands["pessimistic"],
            "baseline_cost":           0,
            "review_cycles_estimated": meta["review_cycles"],
            "review_cycles_actual":    None,
            "parallel_groups":         meta["parallel_groups"],
            "parallel_steps_detected": meta["parallel_steps_detected"],
            "file_brackets":           meta["file_brackets"],
            "files_measured":          meta["files_measured"],
            "step_costs":              step_costs,
            "continuation":            False,
        }

        try:
            cal_path.mkdir(parents=True, exist_ok=True)
            # Atomic write via temp file + rename
            fd, tmp_str = tempfile.mkstemp(dir=str(cal_path), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(json.dumps(active_estimate, indent=2))
                    f.write("\n")
                os.replace(tmp_str, str(active_estimate_path))
            except Exception:
                try:
                    os.unlink(tmp_str)
                except OSError:
                    pass
                raise
            # Clean up any stale step-accumulator from a prior session
            hash_prefix = hashlib.md5(str(active_estimate_path).encode()).hexdigest()[:12]
            acc_path = cal_path / f"{hash_prefix}-step-accumulator.json"
            acc_path.unlink(missing_ok=True)
        except Exception:
            pass  # non-fatal

        try:
            last_md = _format_last_estimate_md_from_data(active_estimate, meta)
            last_estimate_path.write_text(last_md, encoding="utf-8")
        except Exception:
            pass  # non-fatal

    return result


def _format_last_estimate_md_from_data(active_estimate: dict, meta: dict) -> str:
    """Format last-estimate.md content from active_estimate and meta dicts."""
    fb = meta.get("file_brackets")
    files_measured = meta.get("files_measured", 0)
    total_files = meta.get("files", 0)

    if fb is None:
        file_brackets_line = "**File Brackets:** none (no paths extracted)"
    else:
        small  = fb.get("small",  0)
        medium = fb.get("medium", 0)
        large  = fb.get("large",  0)
        defaulted = total_files - files_measured
        file_brackets_line = (
            f"**File Brackets:** {files_measured} measured "
            f"({small} small, {medium} medium, {large} large); "
            f"{max(defaulted, 0)} defaulted"
        )

    steps_str = ", ".join(
        s for s in active_estimate.get("steps", []) if s != "PR Review Loop"
    )
    ts = active_estimate.get("timestamp", "")

    lines = [
        "# Last tokencast Estimate",
        "",
        "**Feature:** (see plan)",
        f"**Recorded:** {ts}",
        f"**Size:** {meta['size']} | **Files:** {meta['files']} | **Complexity:** {meta['complexity']}",
        f"**Type:** {meta['project_type']} | **Language:** {meta['language']}",
        f"**Steps:** {steps_str}",
        file_brackets_line,
        "",
        "| Band        | Cost    |",
        "|-------------|---------|",
        f"| Optimistic  | ${active_estimate['optimistic_cost']:.4f} |",
        f"| Expected    | ${active_estimate['expected_cost']:.4f} |",
        f"| Pessimistic | ${active_estimate['pessimistic_cost']:.4f} |",
        "",
        f"Review cycles estimated: {active_estimate['review_cycles_estimated']}",
        f"Parallel steps detected: {active_estimate['parallel_steps_detected']}",
        f"Baseline Cost: ${active_estimate['baseline_cost']}",
    ]
    return "\n".join(lines) + "\n"


def get_calibration_status(
    params: dict,
    calibration_dir: Optional[str] = None,
) -> dict:
    """Return calibration health and accuracy metrics.

    Delegates to ``tokencast-status.py``'s ``build_status_output()`` function.
    Works standalone — no MCP dependency required.

    Args:
        params: Dict with optional key ``window`` (e.g. ``"30d"``).
        calibration_dir: Path to the calibration directory.  When ``None``,
            defaults to ``~/.tokencast/calibration``.

    Returns:
        Dict matching the ``tokencast-status.py --json`` output schema
        (``schema_version: 1``).  Returns a ``"no_data"`` status dict when
        calibration files are absent or empty.
    """
    window_spec = params.get("window", None)

    # Resolve calibration paths
    if calibration_dir is not None:
        cal_path = pathlib.Path(calibration_dir)
    else:
        cal_path = pathlib.Path.home() / ".tokencast" / "calibration"

    history_path = str(cal_path / "history.jsonl")
    factors_path = str(cal_path / "factors.json")

    # Default heuristics path (references/heuristics.md relative to repo root)
    repo_root = pathlib.Path(__file__).resolve().parent.parent.parent
    heuristics_path = str(repo_root / "references" / "heuristics.md")

    try:
        cs = _load_calibration_store()
        all_records = cs.read_history(history_path)
        factors = cs.read_factors(factors_path)
    except Exception:
        all_records = []
        factors = {}

    try:
        status_mod = _load_status_module()
        result = status_mod.build_status_output(
            all_records,
            factors,
            verbose=False,
            window_spec=window_spec,
            heuristics_path=heuristics_path,
        )
        return result
    except Exception as exc:
        # Degrade gracefully — return a minimal no-data response
        return {
            "schema_version": 1,
            "health": {
                "status": "no_data",
                "message": f"Could not load calibration status: {exc}",
                "clean_sample_count": 0,
                "active_factor_level": "none",
                "factor_value": None,
            },
            "accuracy": None,
            "cost_attribution": None,
            "outliers": None,
            "recommendations": [],
            "window": {
                "spec": window_spec,
                "records_in_window": 0,
                "total_records": 0,
            },
            "meta": {"verbose": False, "no_apply": False},
        }


def get_cost_history(
    params: dict,
    calibration_dir: Optional[str] = None,
) -> dict:
    """Query historical cost estimation records and actuals.

    Args:
        params: Dict with optional keys:
            - ``window``: "Nd" (days), "N" (sessions), "all", or omit for all.
            - ``include_outliers``: bool, default False.
            - ``calibration_dir``: path override (also accepted as a direct arg).
        calibration_dir: Path to the calibration directory.  When ``None``,
            falls back to ``params.get("calibration_dir")`` then
            ``~/.tokencast/calibration``.

    Returns:
        Dict with:
            - ``records``: list of session summary dicts
            - ``summary``: aggregate statistics dict
    """
    window_spec = params.get("window", None)
    include_outliers = params.get("include_outliers", False)

    # Resolve calibration directory: arg > params > default
    _cal_dir = calibration_dir or params.get("calibration_dir") or None
    if _cal_dir is not None:
        cal_path = pathlib.Path(_cal_dir)
    else:
        cal_path = pathlib.Path.home() / ".tokencast" / "calibration"

    history_path = str(cal_path / "history.jsonl")

    try:
        cs = _load_calibration_store()
        all_records = cs.read_history(history_path)
    except Exception:
        all_records = []

    # Apply window filter
    windowed = _resolve_window(all_records, window_spec)

    # Filter outliers
    if not include_outliers:
        windowed = [r for r in windowed if not _is_outlier(r)]

    formatted = [_format_record(r) for r in windowed]
    summary = _compute_summary(windowed)

    return {
        "records": formatted,
        "summary": summary,
    }


_WAITLIST_URL = (
    "https://github.com/krulewis/tokencast/discussions/new?category=team-sharing"
)
_CTA_SESSION_THRESHOLD = 5


def report_session(
    params: dict,
    calibration_dir: Optional[str] = None,
    *,
    session_count: Optional[int] = None,
    suppress_cta: bool = False,
) -> dict:
    """Report actual session cost to improve future calibration.

    Reads active-estimate.json, merges accumulated step costs with any call-time
    step_actuals, calls build_history_record(), persists the record, and cleans up
    the accumulator and active-estimate.json.

    Args:
        params: Dict with required key ``actual_cost`` (float >= 0) and
            optional keys ``step_actuals`` (dict), ``turn_count`` (int),
            ``review_cycles_actual`` (int).
        calibration_dir: Path to the calibration directory. When None,
            defaults to ~/.tokencast/calibration.
        session_count: Number of history records already on disk (before this
            call). When provided and >= _CTA_SESSION_THRESHOLD and
            suppress_cta is False, a ``team_sharing_cta`` field is added to
            the successful response. Callers are responsible for showing the
            CTA at most once per server session (pass suppress_cta=True on
            subsequent calls).
        suppress_cta: When True, omit ``team_sharing_cta`` regardless of
            session_count. Honoured by --no-cta flag and TOKENCAST_NO_CTA env
            var (enforced at the MCP handler layer).

    Returns:
        Protocol-compliant dict with ``attribution_protocol_version``,
        ``record_written``, ``attribution_method``, ``actual_cost``,
        ``step_actuals``. May include ``warning`` and/or
        ``team_sharing_cta`` when applicable.
        On validation failure returns an error dict with ``error`` key.
    """
    from tokencast.session_recorder import build_history_record

    global _step_accumulator, _accumulator_file_path

    # --- 1. Validate actual_cost ---
    actual_cost_raw = params.get("actual_cost")
    if actual_cost_raw is None:
        return {
            "error": "missing_actual_cost",
            "message": "actual_cost is required.",
        }
    try:
        actual_cost = float(actual_cost_raw)
    except (TypeError, ValueError):
        return {
            "error": "invalid_cost",
            "message": "actual_cost must be >= 0.",
        }
    if actual_cost < 0:
        return {
            "error": "invalid_cost",
            "message": "actual_cost must be >= 0.",
        }

    # --- 2. Validate step_actuals (call-time) ---
    calltime_step_actuals = params.get("step_actuals") or {}
    if calltime_step_actuals:
        for step_name, step_val in calltime_step_actuals.items():
            try:
                step_val_f = float(step_val)
            except (TypeError, ValueError):
                return {
                    "error": "invalid_step_actual",
                    "message": f"step_actuals['{step_name}'] must be a non-negative float.",
                }
            if step_val_f < 0:
                return {
                    "error": "invalid_step_actual",
                    "message": f"step_actuals['{step_name}'] must be a non-negative float.",
                }

    # --- 3. Parse optional params ---
    turn_count = int(params.get("turn_count", 0) or 0)
    review_cycles_actual_raw = params.get("review_cycles_actual")
    review_cycles_actual = (
        int(review_cycles_actual_raw) if review_cycles_actual_raw is not None else None
    )
    # Convert 0 to None — matches learn.sh convention: 0 cycles means "unknown"
    if review_cycles_actual == 0:
        review_cycles_actual = None

    # --- 4. Resolve calibration dir ---
    if calibration_dir is None:
        calibration_dir = Path.home() / ".tokencast" / "calibration"
    else:
        calibration_dir = Path(calibration_dir)

    active_estimate_path = calibration_dir / "active-estimate.json"
    # Compute accumulator path based on the estimate path string (hash does not
    # require the file to exist — allows stale accumulator detection below).
    acc_hash = _compute_accumulator_hash(active_estimate_path)
    accumulator_path: Optional[Path] = calibration_dir / f"{acc_hash}-step-accumulator.json"

    # --- 5. Load active estimate ---
    warnings_list: List[str] = []
    estimate_data: dict = {}
    estimate_missing = False

    if active_estimate_path.exists():
        try:
            estimate_data = json.loads(active_estimate_path.read_text())
        except Exception:
            estimate_data = {}
    else:
        estimate_missing = True
        # Attempt reconstitution from last-estimate.md
        last_estimate_md = calibration_dir / "last-estimate.md"
        reconstituted = False
        if last_estimate_md.exists():
            try:
                _parse_mod = _load_parse_last_estimate()
                content = last_estimate_md.read_text()
                mtime = last_estimate_md.stat().st_mtime
                result = _parse_mod.parse(content, mtime=mtime)
                if result is not None:
                    estimate_data = result
                    reconstituted = True
            except Exception:
                pass
        if not reconstituted:
            estimate_data = {
                "size": "unknown",
                "steps": [],
                "expected_cost": 0,
                "continuation": False,
            }
        warnings_list.append("no_active_estimate")

    # --- 6. Stale accumulator check ---
    if estimate_missing and accumulator_path is not None and accumulator_path.exists():
        warnings_list.append("stale_accumulator_discarded")
        accumulator_path = None  # discard; do not merge

    # --- 7. Merge accumulated step costs with call-time step_actuals ---
    merged_step_actuals: dict = {}
    if accumulator_path is not None:
        accumulated = _load_accumulator(accumulator_path)
        merged_step_actuals.update(accumulated)
    # call-time values override accumulated values for duplicate keys
    for k, v in calltime_step_actuals.items():
        merged_step_actuals[k] = float(v)

    # --- 8. Zero-cost guard: no record written ---
    if actual_cost <= 0.001:
        response: dict = {
            "attribution_protocol_version": 1,
            "record_written": False,
            "attribution_method": "proportional",
            "actual_cost": actual_cost,
            "step_actuals": None,
            "warning": f"actual_cost is {actual_cost}; no calibration record written.",
        }
        # Still clean up accumulator and active estimate
        if _accumulator_file_path is not None:
            try:
                _accumulator_file_path.unlink(missing_ok=True)
            except Exception:
                pass
        if accumulator_path is not None and accumulator_path != _accumulator_file_path:
            try:
                accumulator_path.unlink(missing_ok=True)
            except Exception:
                pass
        try:
            active_estimate_path.unlink(missing_ok=True)
        except Exception:
            pass
        _step_accumulator = {}
        _accumulator_file_path = None
        return response

    # --- 9. Build history record ---
    record = build_history_record(
        estimate=estimate_data,
        actual_cost=actual_cost,
        turn_count=turn_count,
        review_cycles_actual=review_cycles_actual,
        step_actuals_mcp=merged_step_actuals or None,
    )

    # --- 10. Persist record via calibration_store subprocess (matches learn.sh) ---
    record_write_error: Optional[str] = None
    try:
        calibration_dir.mkdir(parents=True, exist_ok=True)
        history_path = str(calibration_dir / "history.jsonl")
        factors_path = str(calibration_dir / "factors.json")
        cs = _load_calibration_store()
        cs.append_history(history_path, record)
        # Trigger factor recomputation via update-factors.py
        import subprocess
        import sys as _sys
        scripts_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
        subprocess.run(
            [
                _sys.executable,
                str(scripts_dir / "update-factors.py"),
                history_path,
                factors_path,
            ],
            check=False,
        )
    except Exception as _write_exc:
        record_write_error = str(_write_exc)

    # --- 11. Cleanup ---
    _acc_path_to_delete = _accumulator_file_path
    if accumulator_path is not None:
        _acc_path_to_delete = accumulator_path
    if _acc_path_to_delete is not None:
        try:
            _acc_path_to_delete.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        active_estimate_path.unlink(missing_ok=True)
    except Exception:
        pass

    _step_accumulator = {}
    _accumulator_file_path = None

    # --- 12. Build response ---
    if record_write_error is not None:
        # Disk write failed — return error response (does not crash server)
        response = {
            "attribution_protocol_version": 1,
            "record_written": False,
            "attribution_method": record["attribution_method"],
            "actual_cost": actual_cost,
            "step_actuals": record["step_actuals"],
            "error": "write_failed",
            "message": f"Could not persist calibration record: {record_write_error}",
        }
        if warnings_list:
            response["warning"] = "; ".join(warnings_list)
        return response

    response = {
        "attribution_protocol_version": 1,
        "record_written": True,
        "attribution_method": record["attribution_method"],
        "actual_cost": actual_cost,
        "step_actuals": record["step_actuals"],
    }
    if warnings_list:
        response["warning"] = "; ".join(warnings_list)

    # --- 13. Team-sharing waitlist CTA ---
    if (
        not suppress_cta
        and session_count is not None
        and session_count >= _CTA_SESSION_THRESHOLD
    ):
        response["team_sharing_cta"] = {
            "message": (
                "You've completed 5+ calibrated sessions! Interested in sharing"
                f" calibration with your team? Let us know: {_WAITLIST_URL}"
            ),
            "url": _WAITLIST_URL,
        }

    return response


# ---------------------------------------------------------------------------
# report_step_cost — step cost accumulator
# ---------------------------------------------------------------------------

# NOTE: Module-level mutable state. Safe for single-client stdio MCP server.
# If the server is adapted for concurrent connections, these must be moved
# into a session-scoped state object.
# In-memory step cost accumulator. Maps canonical step_name -> cumulative cost.
# Reset when estimate_cost is called or report_session clears it.
_step_accumulator: dict = {}

# Path to the active accumulator file on disk (set when active estimate is loaded).
# None means no active estimate.
_accumulator_file_path: Optional[Path] = None


def _compute_accumulator_hash(active_estimate_path: Path) -> str:
    """Return the first 12 chars of the MD5 of the active-estimate.json path.

    Matches the hash pattern used by agent-hook.sh for sidecar files.
    """
    return hashlib.md5(str(active_estimate_path).encode()).hexdigest()[:12]


def _get_accumulator_path(calibration_dir: Path) -> Optional[Path]:
    """Return the accumulator file path if active-estimate.json exists.

    Returns None when no active estimate exists (calibration_dir absent or
    active-estimate.json missing).
    """
    active_estimate = calibration_dir / "active-estimate.json"
    if not active_estimate.exists():
        return None
    hash_prefix = _compute_accumulator_hash(active_estimate)
    return calibration_dir / f"{hash_prefix}-step-accumulator.json"


def _load_accumulator(path: Path) -> dict:
    """Load step costs from an accumulator JSON file.

    Returns:
        Dict mapping step name -> float cost, or {} on any read error.
    """
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text())
        return dict(data.get("steps", {}))
    except Exception:
        return {}


def _save_accumulator(path: Path, steps: dict) -> None:
    """Persist step costs to disk using an atomic rename pattern.

    Writes to a .tmp file first, then calls os.replace() to atomically
    overwrite the target path. Prevents corrupt reads on interrupted writes.
    """
    tmp_path = path.with_suffix(".tmp")
    payload = {
        "attribution_protocol_version": 1,
        "steps": steps,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    tmp_path.write_text(json.dumps(payload, indent=2))
    os.replace(tmp_path, path)


def report_step_cost(params: dict, calibration_dir: Optional[str] = None) -> dict:
    """Record the cost of a completed pipeline step.

    Costs accumulate per step (additive for the same step_name) and are
    persisted to disk after each call via atomic rename.  The accumulator is
    flushed when report_session is called.

    Args:
        params: Dict with required key ``step_name`` and optional keys
            ``cost``, ``tokens_in``, ``tokens_out``, ``tokens_cache_read``,
            ``tokens_cache_write``, ``model``.
        calibration_dir: Path to the calibration directory.  When ``None``,
            defaults to ``~/.tokencast/calibration``.

    Returns:
        Protocol-compliant dict with ``attribution_protocol_version``,
        ``step_name``, ``cost_this_call``, ``cumulative_step_cost``,
        ``total_session_accumulated``, and optionally ``warning``.
        On validation failure returns an error dict with ``error`` key.
    """
    global _step_accumulator, _accumulator_file_path

    # --- 1. Validate step_name ---
    raw_name = params.get("step_name")
    if raw_name is None:
        return {"error": "missing_step_name", "message": "step_name is required."}
    raw_name_str = str(raw_name)
    if not raw_name_str.strip():
        return {
            "error": "invalid_step_name",
            "message": "step_name must be a non-empty string.",
        }

    # --- 2. Validate numeric fields ---
    cost_param = params.get("cost")
    if cost_param is not None:
        try:
            cost_param = float(cost_param)
        except (TypeError, ValueError):
            return {"error": "invalid_cost", "message": "cost must be >= 0."}
        if cost_param < 0:
            return {"error": "invalid_cost", "message": "cost must be >= 0."}

    token_fields = ["tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_write"]
    for field in token_fields:
        val = params.get(field)
        if val is not None:
            try:
                val = int(val)
            except (TypeError, ValueError):
                return {
                    "error": "invalid_tokens",
                    "message": "Token counts must be >= 0.",
                    "field": field,
                }
            if val < 0:
                return {
                    "error": "invalid_tokens",
                    "message": "Token counts must be >= 0.",
                    "field": field,
                }

    # --- 3. Check active estimate ---
    if calibration_dir is None:
        _cal_dir: Path = Path.home() / ".tokencast" / "calibration"
    else:
        _cal_dir = Path(calibration_dir)

    accumulator_path = _get_accumulator_path(_cal_dir)
    if accumulator_path is None:
        return {
            "error": "no_active_estimate",
            "message": "Call estimate_cost before reporting step costs.",
        }

    # --- 4. Resolve canonical step name ---
    canonical_name, warning = resolve_step_name(raw_name_str, _cal_dir)

    # --- 5. Compute cost for this call ---
    warnings_list = []
    if warning is not None:
        warnings_list.append(warning)

    if cost_param is not None:
        cost_this_call = cost_param
    else:
        # Check if any token field is present and non-zero
        has_token_data = any(
            params.get(f) is not None and int(params.get(f, 0)) != 0
            for f in token_fields
        )
        if has_token_data:
            usage = {
                "tokens_in": int(params.get("tokens_in", 0) or 0),
                "tokens_out": int(params.get("tokens_out", 0) or 0),
                "tokens_cache_read": int(params.get("tokens_cache_read", 0) or 0),
                "tokens_cache_write": int(params.get("tokens_cache_write", 0) or 0),
            }
            model = params.get("model") or STEP_MODEL_MAP.get(canonical_name, DEFAULT_MODEL)
            cost_this_call = compute_cost_from_usage(usage, model)
        else:
            cost_this_call = 0.0
            warnings_list.append("No cost or token data provided; recorded 0.0")

    # --- 6. Load accumulator, accumulate, persist ---
    steps = _load_accumulator(accumulator_path)
    steps[canonical_name] = steps.get(canonical_name, 0.0) + cost_this_call
    _save_accumulator(accumulator_path, steps)
    _step_accumulator = steps
    _accumulator_file_path = accumulator_path

    # --- 7. Build response ---
    result: dict = {
        "attribution_protocol_version": 1,
        "step_name": canonical_name,
        "cost_this_call": cost_this_call,
        "cumulative_step_cost": steps[canonical_name],
        "total_session_accumulated": sum(steps.values()),
    }
    if warnings_list:
        result["warning"] = "; ".join(warnings_list)
    return result
