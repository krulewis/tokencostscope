"""Core estimation algorithm for tokencast.

Implements SKILL.md Steps 1-4 as pure-computation Python functions.
No file I/O, no subprocess calls — file measurement is done by the caller
and passed in via params.

Entry point: compute_estimate(params, calibration_dir) -> dict
"""

import importlib.util
import warnings
from datetime import date
from pathlib import Path
from typing import Optional

from tokencast import heuristics, pricing
from tokencast.file_measurement import bracket_from_override, compute_avg_tokens

# ---------------------------------------------------------------------------
# Short model name lookup (Sonnet / Opus / Haiku)
# ---------------------------------------------------------------------------

_MODEL_SHORT = {
    pricing.MODEL_SONNET: "Sonnet",
    pricing.MODEL_OPUS:   "Opus",
    pricing.MODEL_HAIKU:  "Haiku",
}

# ---------------------------------------------------------------------------
# Lazy-load calibration_store from scripts/ (has a hyphen-less name but is
# outside the src/ package tree — same importlib pattern as existing tests)
# ---------------------------------------------------------------------------

def _load_calibration_store():
    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    spec = importlib.util.spec_from_file_location(
        "calibration_store",
        scripts_dir / "calibration_store.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_calibration_store = _load_calibration_store()
_read_factors = _calibration_store.read_factors


# ---------------------------------------------------------------------------
# Helper: resolve steps
# ---------------------------------------------------------------------------

def _resolve_steps(size: str, steps_override: Optional[list]) -> list:
    """Return the ordered list of pipeline step names in scope.

    If steps_override is provided and non-empty, use those names (in order),
    dropping any that are not in PIPELINE_STEPS. Otherwise return all keys
    from PIPELINE_STEPS in their defined order.
    """
    all_steps = list(heuristics.PIPELINE_STEPS.keys())
    if steps_override:
        result = []
        for s in steps_override:
            if s in heuristics.PIPELINE_STEPS:
                result.append(s)
            else:
                warnings.warn(f"Unknown step name: {s!r} — skipped", stacklevel=3)
        return result
    return all_steps


# ---------------------------------------------------------------------------
# Helper: resolve model
# ---------------------------------------------------------------------------

def _resolve_model(step_name: str, size: str) -> str:
    """Return the model ID for a pipeline step.

    Special case: Implementation with L-size uses Opus.
    Unknown step names fall back to Sonnet with a warning.
    """
    if step_name == "Implementation" and size == "L":
        return pricing.MODEL_OPUS
    model = pricing.STEP_MODEL_MAP.get(step_name)
    if model is None:
        warnings.warn(
            f"Unknown step name {step_name!r} in STEP_MODEL_MAP — using Sonnet",
            stacklevel=3,
        )
        return pricing.MODEL_SONNET
    return model


# ---------------------------------------------------------------------------
# Helper: resolve review cycles
# ---------------------------------------------------------------------------

def _resolve_review_cycles(params: dict, steps: list) -> int:
    """Return the review cycle count N.

    If params["review_cycles"] is explicitly set (not None), use that value.
    Otherwise infer from steps per SKILL.md Step 0 item 7.
    """
    if params.get("review_cycles") is not None:
        return int(params["review_cycles"])

    # Inference: need both a "review"-type step and a "final/implement/test" step
    has_review = any("review" in s.lower() for s in steps)
    has_final  = any(
        ("final" in s.lower() or "implement" in s.lower() or "test" in s.lower())
        for s in steps
    )
    if has_review and has_final:
        return heuristics.PR_REVIEW_LOOP["review_cycles_default"]
    return 0


# ---------------------------------------------------------------------------
# Helper: compute file token contributions for a single step
# ---------------------------------------------------------------------------

def _compute_file_tokens(
    step_name: str,
    activity: str,
    N: int,
    file_brackets: Optional[dict],
    avg_file_read_tokens: float,
    avg_file_edit_tokens: float,
) -> tuple:
    """Return (file_read_contribution, file_edit_contribution) for one activity.

    Called once per file-related activity in a step.
    Returns (0.0, 0.0) for non-file activities.
    """
    bk = heuristics.FILE_SIZE_BRACKETS["brackets"]
    fixed_counts = heuristics.FILE_SIZE_BRACKETS["fixed_count_steps"]

    if activity == "file_read":
        fixed_count = fixed_counts.get(step_name)
        if fixed_count is not None:
            # Fixed-count step: reads only, no edits
            return (avg_file_read_tokens * fixed_count, 0.0)
        elif step_name == "Test Writing":
            # Test Writing has 3 fixed reads (not N-scaling)
            return (avg_file_read_tokens * 3, 0.0)
        else:
            # N-scaling read (Implementation)
            if file_brackets is not None:
                read_contrib = (
                    file_brackets.get("small",  0) * bk["small"]["file_read_input"]
                    + file_brackets.get("medium", 0) * bk["medium"]["file_read_input"]
                    + file_brackets.get("large",  0) * bk["large"]["file_read_input"]
                )
            else:
                read_contrib = N * 10000  # medium default
            return (read_contrib, 0.0)

    elif activity == "file_edit":
        # N-scaling edit (Implementation)
        if file_brackets is not None:
            edit_contrib = (
                file_brackets.get("small",  0) * bk["small"]["file_edit_input"]
                + file_brackets.get("medium", 0) * bk["medium"]["file_edit_input"]
                + file_brackets.get("large",  0) * bk["large"]["file_edit_input"]
            )
        else:
            edit_contrib = N * 2500  # medium default
        return (0.0, edit_contrib)

    return (0.0, 0.0)


# ---------------------------------------------------------------------------
# Helper: compute base tokens for a step
# ---------------------------------------------------------------------------

def _compute_step_base_tokens(
    step_name: str,
    N: int,
    file_brackets: Optional[dict],
    avg_file_read_tokens: float,
    avg_file_edit_tokens: float,
) -> tuple:
    """Return (input_base, output_base, K) for one pipeline step.

    K = total activity count (sum of resolved counts across all activities).
    """
    step_def = heuristics.PIPELINE_STEPS.get(step_name)
    if step_def is None:
        warnings.warn(f"Unknown step {step_name!r} in PIPELINE_STEPS", stacklevel=3)
        return (0.0, 0.0, 0)

    activities = step_def["activities"]
    input_base  = 0.0
    output_base = 0.0
    K           = 0

    FILE_READ_OUT  = heuristics.FILE_SIZE_BRACKETS["file_read_output"]   # 200
    FILE_EDIT_OUT  = heuristics.FILE_SIZE_BRACKETS["file_edit_output"]   # 1500

    for activity, count in activities:
        # Resolve N-scaling
        if count == heuristics.N_SCALING:
            resolved_count = N
        else:
            resolved_count = count

        K += resolved_count

        if activity == "file_read":
            # Bracket-aware input
            read_in, _ = _compute_file_tokens(
                step_name, activity, N, file_brackets,
                avg_file_read_tokens, avg_file_edit_tokens,
            )
            input_base  += read_in
            output_base += FILE_READ_OUT * resolved_count

        elif activity == "file_edit":
            # Bracket-aware edit input
            _, edit_in = _compute_file_tokens(
                step_name, activity, N, file_brackets,
                avg_file_read_tokens, avg_file_edit_tokens,
            )
            input_base  += edit_in
            output_base += FILE_EDIT_OUT * resolved_count

        elif activity == "test_write":
            # test_write: input is fixed 2000 tokens (NOT bracket-dependent), output 5000
            at = heuristics.ACTIVITY_TOKENS["test_write"]
            input_base  += at["input"]  * resolved_count
            output_base += at["output"] * resolved_count

        else:
            at = heuristics.ACTIVITY_TOKENS.get(activity)
            if at is None:
                warnings.warn(
                    f"Unknown activity {activity!r} in step {step_name!r}",
                    stacklevel=3,
                )
                continue
            input_base  += at["input"]  * resolved_count
            output_base += at["output"] * resolved_count

    return (input_base, output_base, K)


# ---------------------------------------------------------------------------
# Helper: compute per-step costs for all bands
# ---------------------------------------------------------------------------

def _compute_step_cost(
    input_base: float,
    output_base: float,
    K: int,
    complexity: str,
    model_id: str,
    is_parallel: bool,
) -> dict:
    """Compute Optimistic / Expected / Pessimistic costs for one step.

    Returns:
        {
            "optimistic": float,
            "expected": float,
            "pessimistic": float,
            "expected_pre_discount": float,  # pre-parallel-discount Expected cost
        }
    """
    cmx = heuristics.COMPLEXITY_MULTIPLIERS[complexity]
    prices = pricing.MODEL_PRICES[model_id]
    price_in  = prices["input"]
    price_cw  = prices["cache_write"]
    price_cr  = prices["cache_read"]
    price_out = prices["output"]

    # 3b: complexity
    input_complex  = input_base  * cmx
    output_complex = output_base * cmx

    # 3c: context accumulation
    input_accum = input_complex * (K + 1) / 2

    # Cache_write_fraction guard (K=0 edge case)
    cache_write_fraction = 1.0 / max(K, 1)

    # Pre-discount accumulation (for PR Review Loop C computation)
    input_accum_pre_discount = input_accum

    if is_parallel:
        input_accum = input_accum * heuristics.PARALLEL_ACCOUNTING["parallel_input_discount"]

    result = {}

    # Compute expected_pre_discount (pre-parallel-discount Expected band)
    expected_cache_rate_base = pricing.CACHE_HIT_RATES["expected"]
    # No parallel cache reduction for pre-discount calculation
    _inp = input_accum_pre_discount
    _icost_pre = (
        _inp * (1 - expected_cache_rate_base) * price_in
        + _inp * expected_cache_rate_base * cache_write_fraction * price_cw
        + _inp * expected_cache_rate_base * (1 - cache_write_fraction) * price_cr
    ) / 1_000_000
    _ocost_pre = output_complex * price_out / 1_000_000
    expected_pre_discount = (_icost_pre + _ocost_pre) * heuristics.BAND_MULTIPLIERS["expected"]

    for band in ("optimistic", "expected", "pessimistic"):
        cache_rate = pricing.CACHE_HIT_RATES[band]
        if is_parallel:
            cache_rate = max(
                cache_rate - heuristics.PARALLEL_ACCOUNTING["parallel_cache_rate_reduction"],
                heuristics.PARALLEL_ACCOUNTING["parallel_cache_rate_floor"],
            )
        band_mult = heuristics.BAND_MULTIPLIERS[band]

        # Three-term cache cost formula (Step 3d)
        input_cost = (
            input_accum * (1 - cache_rate) * price_in
            + input_accum * cache_rate * cache_write_fraction * price_cw
            + input_accum * cache_rate * (1 - cache_write_fraction) * price_cr
        ) / 1_000_000

        output_cost = output_complex * price_out / 1_000_000
        result[band] = (input_cost + output_cost) * band_mult

    result["expected_pre_discount"] = expected_pre_discount
    return result


# ---------------------------------------------------------------------------
# Helper: resolve calibration factor (5-level precedence chain)
# ---------------------------------------------------------------------------

def _resolve_calibration_factor(
    step_name: str,
    size: str,
    pipeline_signature: str,
    factors: dict,
) -> tuple:
    """Return (factor, cal_label) for a step using the 5-level precedence chain.

    Levels: per-step (S) → per-signature (P) → size-class (Z) → global (G) → no-cal (--)
    """
    min_samples = heuristics.PER_STEP_CALIBRATION["per_step_min_samples"]  # 3

    # Level 1 — Per-step
    step_factors = factors.get("step_factors", {})
    entry = step_factors.get(step_name)
    if entry and entry.get("status") == "active":
        f = entry["factor"]
        return (f, f"S:{f:.2f}")

    # Level 2 — Per-signature
    sig_factors = factors.get("signature_factors", {})
    sig_entry = sig_factors.get(pipeline_signature)
    if sig_entry and sig_entry.get("status") == "active":
        f = sig_entry["factor"]
        return (f, f"P:{f:.2f}")

    # Level 3 — Size-class
    sz_factor = factors.get(size)
    sz_n      = factors.get(f"{size}_n", 0)
    if sz_factor is not None and sz_n >= min_samples:
        f = float(sz_factor)
        return (f, f"Z:{f:.2f}")

    # Level 4 — Global
    g_factor = factors.get("global")
    g_status = factors.get("status")
    if g_factor is not None and g_status == "active":
        f = float(g_factor)
        return (f, f"G:{f:.2f}")

    # Level 5 — No calibration
    return (1.0, "--")


# ---------------------------------------------------------------------------
# Helper: apply calibration (standard steps — re-anchor Opt/Pess from Expected)
# ---------------------------------------------------------------------------

def _apply_calibration(costs: dict, factor: float) -> dict:
    """Apply calibration factor to a step's costs dict.

    For regular steps, calibration re-anchors Optimistic and Pessimistic
    as fixed ratios of calibrated Expected:
        calibrated_expected    = expected × factor
        calibrated_optimistic  = calibrated_expected × 0.6
        calibrated_pessimistic = calibrated_expected × 3.0
    """
    calibrated_expected    = costs["expected"] * factor
    calibrated_optimistic  = calibrated_expected * 0.6
    calibrated_pessimistic = calibrated_expected * 3.0
    return {
        "optimistic":  calibrated_optimistic,
        "expected":    calibrated_expected,
        "pessimistic": calibrated_pessimistic,
    }


# ---------------------------------------------------------------------------
# Helper: PR Review Loop computation
# ---------------------------------------------------------------------------

def _compute_pr_review_loop(
    staff_review_expected_pre: float,
    engineer_final_expected_pre: float,
    review_cycles: int,
    factors: dict,
    size: str,
) -> Optional[dict]:
    """Compute PR Review Loop costs.

    Returns dict with optimistic/expected/pessimistic/cal_label/factor,
    or None if review_cycles == 0.

    Per SKILL.md and CLAUDE.md:
    - Cal is ALWAYS "--" (hardcoded, no calibration lookup)
    - Factor is ALWAYS 1.0 (no per-step or per-signature lookup)
    - Calibration is applied INDEPENDENTLY to each band (not re-anchored)
    """
    if review_cycles == 0:
        return None

    decay = heuristics.PR_REVIEW_LOOP["review_decay_factor"]  # 0.6

    C = staff_review_expected_pre + engineer_final_expected_pre

    def _loop_cost(cycles: int) -> float:
        if cycles == 0:
            return 0.0
        return C * (1 - decay ** cycles) / (1 - decay)

    opt_cycles  = 1
    exp_cycles  = review_cycles
    pess_cycles = review_cycles * 2

    opt_raw  = _loop_cost(opt_cycles)
    exp_raw  = _loop_cost(exp_cycles)
    pess_raw = _loop_cost(pess_cycles)

    # Per H1: factor is ALWAYS 1.0, cal is ALWAYS "--"
    factor    = 1.0
    cal_label = "--"

    return {
        "optimistic":  opt_raw  * factor,
        "expected":    exp_raw  * factor,
        "pessimistic": pess_raw * factor,
        "cal_label":   cal_label,
        "factor":      factor,
    }


# ---------------------------------------------------------------------------
# Helper: pipeline signature
# ---------------------------------------------------------------------------

def _compute_pipeline_signature(steps: list) -> str:
    """Compute the pipeline signature string.

    Formula mirrors learn.sh line 38:
        '+'.join(sorted(s.lower().replace(' ', '_') for s in steps))
    """
    return "+".join(sorted(s.lower().replace(" ", "_") for s in steps))


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_estimate(params: dict, calibration_dir: Optional[str] = None) -> dict:
    """Compute a cost estimate for a development plan.

    Args:
        params: Dict with required keys "size", "files", "complexity" and
            optional keys defined in the MCP tool schema.
        calibration_dir: Path to the calibration/ directory. When None,
            calibration factors default to {} (factor = 1.0 for all steps).

    Returns:
        Structured dict with "version", "estimate", "steps", "metadata",
        and "step_costs" keys.
    """
    # --- Extract params ---
    size       = params["size"]
    N          = int(params["files"])
    complexity = params["complexity"]

    project_type    = params.get("project_type", "greenfield")
    language        = params.get("language", "unknown")
    parallel_groups = params.get("parallel_groups") or []
    avg_file_lines  = params.get("avg_file_lines")

    # Pre-computed file bracket data (injected by MCP tool handler after measure_files)
    file_brackets_param     = params.get("file_brackets")
    avg_read_param          = params.get("avg_file_read_tokens")
    avg_edit_param          = params.get("avg_file_edit_tokens")

    # --- Step 1: Resolve steps ---
    steps_override = params.get("steps") or []
    steps = _resolve_steps(size, steps_override if steps_override else None)

    # --- Step 2: Review cycles ---
    review_cycles = _resolve_review_cycles(params, steps)

    # --- Load calibration factors ---
    if calibration_dir is not None:
        factors_path = str(Path(calibration_dir) / "factors.json")
        try:
            factors = _read_factors(factors_path)
        except Exception:
            factors = {}
    else:
        factors = {}

    # --- Compute pipeline signature ---
    pipeline_signature = _compute_pipeline_signature(steps)

    # --- Resolve parallel set ---
    parallel_set = set()
    for group in parallel_groups:
        for step_name in group:
            parallel_set.add(step_name)
    parallel_steps_detected = len(parallel_set)

    # --- Resolve file bracket state ---
    if file_brackets_param is not None:
        # Pre-measured by caller
        file_brackets = file_brackets_param
        if avg_read_param is not None and avg_edit_param is not None:
            avg_file_read_tokens = float(avg_read_param)
            avg_file_edit_tokens = float(avg_edit_param)
        else:
            avg_file_read_tokens, avg_file_edit_tokens = compute_avg_tokens(file_brackets)
        files_measured = int(params.get("files_measured", sum(file_brackets.values())))
    elif avg_file_lines is not None:
        # Override bracket — no disk measurement
        bracket_name = bracket_from_override(int(avg_file_lines))
        bk = heuristics.FILE_SIZE_BRACKETS["brackets"][bracket_name]
        file_brackets = {
            "small":  N if bracket_name == "small"  else 0,
            "medium": N if bracket_name == "medium" else 0,
            "large":  N if bracket_name == "large"  else 0,
        }
        avg_file_read_tokens = float(bk["file_read_input"])
        avg_file_edit_tokens = float(bk["file_edit_input"])
        files_measured = 0
    else:
        # No file path data — use medium defaults
        file_brackets        = None
        avg_file_read_tokens = 10000.0
        avg_file_edit_tokens = 2500.0
        files_measured       = 0

    # --- Per-step loop ---
    step_results = []
    staff_review_expected_pre  = 0.0
    engineer_final_expected_pre = 0.0

    for step_name in steps:
        # 3a: base tokens
        input_base, output_base, K = _compute_step_base_tokens(
            step_name, N, file_brackets, avg_file_read_tokens, avg_file_edit_tokens
        )

        # Resolve model
        model_id = _resolve_model(step_name, size)

        # Determine parallel membership
        is_parallel = step_name in parallel_set

        # 3b–3d: compute costs for all bands
        costs = _compute_step_cost(
            input_base, output_base, K, complexity, model_id, is_parallel
        )

        # Cache pre-discount Expected for PR Review Loop (Step 3.5)
        if step_name == "Staff Review":
            staff_review_expected_pre = costs["expected_pre_discount"]
        elif step_name == "Engineer Final Plan":
            engineer_final_expected_pre = costs["expected_pre_discount"]

        # 3e: calibration factor
        factor, cal_label = _resolve_calibration_factor(
            step_name, size, pipeline_signature, factors
        )

        # Apply calibration (standard re-anchor)
        cal_costs = _apply_calibration(costs, factor)

        model_short = _MODEL_SHORT.get(model_id, model_id)

        step_results.append({
            "name":        step_name,
            "model":       model_short,
            "model_id":    model_id,
            "cal":         cal_label,
            "factor":      factor,
            "optimistic":  cal_costs["optimistic"],
            "expected":    cal_costs["expected"],
            "pessimistic": cal_costs["pessimistic"],
            "is_parallel": is_parallel,
        })

    # --- Step 3.5: PR Review Loop (post-step-loop) ---
    pr_loop = _compute_pr_review_loop(
        staff_review_expected_pre,
        engineer_final_expected_pre,
        review_cycles,
        factors,
        size,
    )

    if pr_loop is not None:
        step_results.append({
            "name":        "PR Review Loop",
            "model":       "Opus+Sonnet",
            "model_id":    None,
            "cal":         pr_loop["cal_label"],
            "factor":      pr_loop["factor"],
            "optimistic":  pr_loop["optimistic"],
            "expected":    pr_loop["expected"],
            "pessimistic": pr_loop["pessimistic"],
            "is_parallel": False,
        })

    # --- Step 4: Sum all bands ---
    total_optimistic  = sum(s["optimistic"]  for s in step_results)
    total_expected    = sum(s["expected"]    for s in step_results)
    total_pessimistic = sum(s["pessimistic"] for s in step_results)

    # --- step_costs dict (calibrated Expected per step) ---
    step_costs = {s["name"]: s["expected"] for s in step_results}

    # --- Pricing staleness ---
    try:
        last_updated_date = date.fromisoformat(pricing.LAST_UPDATED)
        today = date.today()
        pricing_stale = (today - last_updated_date).days > pricing.STALENESS_WARNING_DAYS
    except Exception:
        pricing_stale = False

    # --- Import version from __init__.py ---
    from tokencast import __version__ as _ver

    return {
        "version": _ver,
        "estimate": {
            "optimistic":  total_optimistic,
            "expected":    total_expected,
            "pessimistic": total_pessimistic,
        },
        "steps": step_results,
        "metadata": {
            "size":                    size,
            "files":                   N,
            "complexity":              complexity,
            "project_type":            project_type,
            "language":                language,
            "review_cycles":           review_cycles,
            "file_brackets":           file_brackets,
            "files_measured":          files_measured,
            "parallel_groups":         parallel_groups,
            "parallel_steps_detected": parallel_steps_detected,
            "pricing_last_updated":    pricing.LAST_UPDATED,
            "pricing_stale":           pricing_stale,
            "pipeline_signature":      pipeline_signature,
        },
        "step_costs": step_costs,
    }
