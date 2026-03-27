"""Heuristic parameters for tokencast estimation.

Derived from references/heuristics.md — update both files together.
"""

# Sentinel value for N-scaling activities (count scales with file count N from the plan).
# Used in PIPELINE_STEPS activities lists where the count is not a fixed integer.
N_SCALING = -1

# Activity Token Estimates.
# Markdown activity name → Python key mapping:
#   "File read"            → "file_read"
#   "File write (new)"     → "file_write_new"
#   "File edit"            → "file_edit"
#   "Test write"           → "test_write"
#   "Code review pass"     → "code_review_pass"
#   "Research/exploration" → "research_exploration"
#   "Planning step"        → "planning_step"
#   "Grep/search"          → "grep_search"
#   "Shell command"        → "shell_command"
#   "Conversation turn"    → "conversation_turn"
#
# Note: file_read and file_edit values here are the medium-bracket defaults
# (50–500 lines). See FILE_SIZE_BRACKETS for bracket-specific values.
ACTIVITY_TOKENS: dict = {
    "file_read":            {"input": 10000, "output": 200},
    "file_write_new":       {"input": 1500,  "output": 4000},
    "file_edit":            {"input": 2500,  "output": 1500},
    "test_write":           {"input": 2000,  "output": 5000},
    "code_review_pass":     {"input": 8000,  "output": 3000},
    "research_exploration": {"input": 5000,  "output": 2000},
    "planning_step":        {"input": 3000,  "output": 4000},
    "grep_search":          {"input": 500,   "output": 500},
    "shell_command":        {"input": 300,   "output": 500},
    "conversation_turn":    {"input": 5000,  "output": 1500},
}

# Pipeline Step Activity Counts.
# Each entry maps a step name (matching pricing.STEP_MODEL_MAP keys) to its
# list of (activity_name, count) tuples.
#
# N_SCALING (-1) marks activities whose count scales with the file count N
# from the implementation plan.
#
# Note: The "model" key is intentionally omitted here. Use pricing.STEP_MODEL_MAP
# as the single source of truth for step→model assignments.
#
# PR Review Loop is NOT listed here — it is a composite step defined via
# PR_REVIEW_LOOP parameters below.
#
# Algorithmic descriptions (Context Accumulation formula, weighted average formula,
# new-file classification rules, cap overflow behavior) are not extractable constants
# and are documented in heuristics.md prose only, not reproduced here.
PIPELINE_STEPS: dict = {
    "Research Agent": {
        "activities": [
            ("file_read",          6),
            ("grep_search",        4),
            ("planning_step",      1),
            ("conversation_turn",  3),
        ],
    },
    "Architect Agent": {
        "activities": [
            ("code_review_pass",   1),
            ("planning_step",      1),
            ("conversation_turn",  2),
        ],
    },
    "Engineer Initial Plan": {
        "activities": [
            ("file_read",          4),
            ("grep_search",        2),
            ("planning_step",      1),
            ("conversation_turn",  2),
        ],
    },
    "Staff Review": {
        "activities": [
            ("code_review_pass",   1),
            ("conversation_turn",  2),
        ],
    },
    "Engineer Final Plan": {
        "activities": [
            ("file_read",          2),
            ("planning_step",      1),
            ("conversation_turn",  2),
        ],
    },
    # Test Writing is a hybrid step:
    #   - 3 FIXED file reads (use weighted-average brackets, same as fixed-count steps)
    #   - N N-scaling test writes (scale with file count N)
    #   - 3 fixed conversation turns
    "Test Writing": {
        "activities": [
            ("file_read",          3),           # fixed count; uses weighted-average brackets
            ("test_write",         N_SCALING),   # scales with N
            ("conversation_turn",  3),
        ],
    },
    "Implementation": {
        "activities": [
            ("file_read",          N_SCALING),   # scales with N
            ("file_edit",          N_SCALING),   # scales with N
            ("conversation_turn",  4),
        ],
    },
    "QA": {
        "activities": [
            ("shell_command",      3),
            ("file_read",          2),
            ("conversation_turn",  2),
        ],
    },
}

# Complexity multipliers applied to both input and output base tokens before
# context accumulation.
COMPLEXITY_MULTIPLIERS: dict = {
    "low":    0.7,
    "medium": 1.0,
    "high":   1.5,
}

# Confidence band multipliers applied after context accumulation.
BAND_MULTIPLIERS: dict = {
    "optimistic":  0.6,
    "expected":    1.0,
    "pessimistic": 3.0,
}

# PR Review Loop parameters.
# band_cycles controls the cycle count for each confidence band:
#   "optimistic"  → always 1 cycle (best case: first review pass is clean)
#   "expected"    → None  # Resolved by engine as: review_cycles (from input or default)
#   "pessimistic" → None  # Resolved by engine as: review_cycles * 2
# If review_cycles=0, no PR Review Loop row appears in the output.
PR_REVIEW_LOOP: dict = {
    "review_cycles_default": 2,
    "review_decay_factor":   0.6,
    "band_cycles": {
        "optimistic":  1,
        "expected":    None,   # Resolved by engine as: review_cycles
        "pessimistic": None,   # Resolved by engine as: review_cycles * 2
    },
}

# Parallel agent accounting adjustments.
# Applied when pipeline steps run as parallel subagents.
PARALLEL_ACCOUNTING: dict = {
    "parallel_input_discount":       0.75,
    "parallel_cache_rate_reduction": 0.15,
    "parallel_cache_rate_floor":     0.05,
}

# Per-step calibration activation threshold.
# Minimum history entries required before a per-step factor activates.
# Matches the size-class activation threshold; both should be updated together.
#
# Note: DECAY_MIN_RECORDS=5 is a statistical invariant hardcoded in
# update-factors.py — intentionally NOT here. See CLAUDE.md architecture conventions.
PER_STEP_CALIBRATION: dict = {
    "per_step_min_samples": 3,
}

# File size bracket parameters.
# Boundary values:
#   lines ≤ small_max_lines  → small bracket
#   lines ≥ large_min_lines  → large bracket
#   otherwise                → medium bracket
#
# Note: The weighted average formula, new-file classification logic, and cap
# overflow behavior are algorithmic descriptions in heuristics.md prose — not
# extractable constants. Only the tunable boundary values and bracket token
# counts are reproduced here.
#
# cache_write_fraction = 1/K is computed by the estimation engine per-step
# (where K = total activity count); it is not a heuristic constant.
FILE_SIZE_BRACKETS: dict = {
    "small_max_lines": 49,
    "large_min_lines": 501,
    "measurement_cap": 30,
    # Per-bracket input token counts for file read and file edit activities.
    # File read output (200) and file edit output (1,500) are unchanged across brackets.
    "brackets": {
        "small":  {"file_read_input": 3000,  "file_edit_input": 1000},
        "medium": {"file_read_input": 10000, "file_edit_input": 2500},
        "large":  {"file_read_input": 20000, "file_edit_input": 5000},
    },
    "file_read_output":  200,
    "file_edit_output":  1500,
    # Binary file extensions excluded from wc -l measurement (fall back to medium bracket).
    # Stored as a tuple (sorted alphabetically) for JSON serializability.
    "binary_extensions": (
        ".a",
        ".bin",
        ".bmp",
        ".class",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".o",
        ".png",
        ".pyc",
        ".pyo",
        ".so",
        ".svg",
        ".wasm",
    ),
    # Steps that use fixed (non-N) file read counts.
    # These steps use the weighted-average bracket for their file reads.
    "fixed_count_steps": {
        "Research Agent":        6,
        "Engineer Initial Plan": 4,
        "Engineer Final Plan":   2,
        "QA":                    2,
    },
}

# Time-based decay weighting parameters.
# Applied to history records before factor aggregation in update-factors.py Passes 3–5.
# DECAY_MIN_RECORDS=5 (cold-start guard) is a statistical invariant hardcoded in
# update-factors.py — intentionally NOT here. See CLAUDE.md architecture conventions.
TIME_DECAY: dict = {
    "decay_halflife_days": 30,
}

# Per-signature calibration activation threshold.
# Minimum history entries required before a per-signature factor activates.
# Matches per-step and size-class activation thresholds; all three should be
# updated together if changed.
PER_SIGNATURE_CALIBRATION: dict = {
    "per_signature_min_samples": 3,
}

# Mid-session cost tracking parameters for tokencast-midcheck.sh.
# Sampling gate uses file size (single stat syscall) rather than line counting
# to avoid O(n) overhead on every tool call.
MID_SESSION_TRACKING: dict = {
    "midcheck_warn_threshold":  0.80,    # Warn when spend reaches 80% of pessimistic
    "midcheck_sampling_bytes":  50000,   # Check cost every ~50KB of JSONL growth
    "midcheck_cooldown_bytes":  200000,  # After warning, suppress for ~200KB of growth
}
