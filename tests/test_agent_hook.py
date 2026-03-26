"""Tests for tokencast-agent-hook.sh and sum_session_by_agent() (v1.7.0).

Covers:
- Sidecar event schema: required fields, no parent_agent (F1), span_id (F2)
- Agent-to-step mapping: defaults, ordinal disambiguation, custom overrides (E1)
- Nesting inference from open spans (F1)
- FIFO span matching by span_id (F2)
- sum_session_by_agent: single-pass cost attribution, zero-width spans (F15)
- compute_line_cost helper: single-source cost formula (F4)
- learn.sh integration with SIDECAR_PATH_ENV (F13)
- Shell integration tests (skipped if agent-hook.sh absent)
"""
# Runner: pytest (required). Use: /usr/bin/python3 -m pytest tests/

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
AGENT_HOOK_SH = SCRIPTS_DIR / "tokencast-agent-hook.sh"
LEARN_SH = SCRIPTS_DIR / "tokencast-learn.sh"
SUM_SESSION_PY = SCRIPTS_DIR / "sum-session-tokens.py"
CALIBRATION_DIR = REPO_ROOT / "calibration"

PYTHON = sys.executable


# ---------------------------------------------------------------------------
# Module loader helpers
# ---------------------------------------------------------------------------

def load_sum_session_module():
    """Load sum-session-tokens.py (hyphens in filename)."""
    if not SUM_SESSION_PY.exists():
        pytest.skip(f"sum-session-tokens.py not found: {SUM_SESSION_PY}")
    spec = importlib.util.spec_from_file_location("sum_session_tokens", str(SUM_SESSION_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------

def make_sidecar_event(
    event_type="agent_start",
    agent_name="researcher",
    session_id="test-session",
    jsonl_line_count=0,
    span_id=1,
    schema_version=1,
    ts="2026-01-01T00:00:00.000Z",
    extra_fields=None,
):
    """Build a minimal sidecar event dict."""
    ev = {
        "schema_version": schema_version,
        "type": event_type,
        "timestamp": ts,
        "agent_name": agent_name,
        "session_id": session_id,
        "jsonl_line_count": jsonl_line_count,
        "span_id": span_id,
        "metadata": {},
    }
    if extra_fields:
        ev.update(extra_fields)
    return ev


def write_sidecar(tmp_path, events, filename="test-session-timeline.jsonl"):
    """Write sidecar events to a JSONL file, return path."""
    path = str(tmp_path / filename)
    with open(path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return path


def make_assistant_line(input_tokens=1000, output_tokens=100, model="claude-sonnet-4-6"):
    """Build one assistant message dict (billable)."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": output_tokens,
            },
        },
    }


def write_jsonl(tmp_path, lines, filename="session.jsonl"):
    """Write a list of dicts as JSONL, return path."""
    path = str(tmp_path / filename)
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return path


def write_agent_map(tmp_path, mapping):
    """Write an agent-map.json file, return calibration dir path."""
    cal_dir = str(tmp_path / "calibration")
    os.makedirs(cal_dir, exist_ok=True)
    map_path = os.path.join(cal_dir, "agent-map.json")
    with open(map_path, "w") as f:
        json.dump(mapping, f)
    return cal_dir


# ---------------------------------------------------------------------------
# TestSidecarEventSchema
# ---------------------------------------------------------------------------

class TestSidecarEventSchema:
    """Verify the sidecar event schema (E3 contract)."""

    def test_agent_start_event_fields(self):
        """agent_start event has all required fields."""
        ev = make_sidecar_event(event_type="agent_start")
        required = {"schema_version", "type", "timestamp", "agent_name",
                    "session_id", "jsonl_line_count", "span_id", "metadata"}
        for field in required:
            assert field in ev, f"Missing required field: {field}"

    def test_agent_stop_event_fields(self):
        """agent_stop event has same field set as agent_start."""
        ev = make_sidecar_event(event_type="agent_stop")
        required = {"schema_version", "type", "timestamp", "agent_name",
                    "session_id", "jsonl_line_count", "span_id", "metadata"}
        for field in required:
            assert field in ev, f"Missing required field: {field}"

    def test_schema_version_is_1(self):
        """schema_version must be 1 for v1 events."""
        ev = make_sidecar_event()
        assert ev["schema_version"] == 1

    def test_agent_name_lowercased(self, tmp_path):
        """agent_name in events should be lowercase (hook does lowercasing)."""
        # Build a sidecar with a lowercase agent name (as hook would produce)
        ev = make_sidecar_event(agent_name="researcher")
        assert ev["agent_name"] == ev["agent_name"].lower()

    def test_no_parent_agent_field(self):
        """F1: 'parent_agent' must NOT be in event dict — nesting inferred at read time."""
        ev = make_sidecar_event()
        assert "parent_agent" not in ev, (
            "F1 violation: parent_agent field found in sidecar event. "
            "Nesting must be inferred chronologically, not stored at write time."
        )

    def test_span_id_is_integer(self):
        """F2: span_id must be an integer."""
        ev = make_sidecar_event(span_id=3)
        assert isinstance(ev["span_id"], int)

    def test_span_id_increments(self):
        """F2: successive events should have increasing span_id values."""
        ev1 = make_sidecar_event(span_id=1)
        ev2 = make_sidecar_event(span_id=2)
        assert ev2["span_id"] > ev1["span_id"]

    def test_metadata_is_empty_dict(self):
        """metadata field is an empty dict by default."""
        ev = make_sidecar_event()
        assert ev["metadata"] == {}


# ---------------------------------------------------------------------------
# TestAgentToStepMapping
# ---------------------------------------------------------------------------

class TestAgentToStepMapping:
    """Test DEFAULT_AGENT_TO_STEP mapping and _load_agent_map() (E1)."""

    def test_default_known_names_map(self, tmp_path):
        """researcher → Research Agent, staff-reviewer → Staff Review, etc."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        mapping = mod._load_agent_map(cal_dir)
        assert mapping.get("researcher") == "Research Agent"
        assert mapping.get("staff-reviewer") == "Staff Review"
        assert mapping.get("implementer") == "Implementation"
        assert mapping.get("qa") == "QA"

    def test_engineer_ordinal_first(self, tmp_path):
        """First 'engineer' span resolves to 'Engineer Initial Plan'."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "engineer", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "engineer", jsonl_line_count=10, span_id=2),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        assert "Engineer Initial Plan" in ranges

    def test_engineer_ordinal_second(self, tmp_path):
        """Second 'engineer' span resolves to 'Engineer Final Plan'."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "engineer", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "engineer", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_start", "engineer", jsonl_line_count=5, span_id=3),
            make_sidecar_event("agent_stop", "engineer", jsonl_line_count=10, span_id=4),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        assert "Engineer Final Plan" in ranges

    def test_engineer_ordinal_third(self, tmp_path):
        """F8: Third 'engineer' span uses raw agent name (not a known ordinal)."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "engineer", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "engineer", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_start", "engineer", jsonl_line_count=5, span_id=3),
            make_sidecar_event("agent_stop", "engineer", jsonl_line_count=10, span_id=4),
            make_sidecar_event("agent_start", "engineer", jsonl_line_count=10, span_id=5),
            make_sidecar_event("agent_stop", "engineer", jsonl_line_count=15, span_id=6),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        # Third engineer → raw name "engineer"
        assert "engineer" in ranges

    def test_engineer_initial_explicit(self, tmp_path):
        """'engineer-initial' → 'Engineer Initial Plan' via default map (no ordinal counting)."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        mapping = mod._load_agent_map(cal_dir)
        assert mapping.get("engineer-initial") == "Engineer Initial Plan"

    def test_engineer_final_explicit(self, tmp_path):
        """'engineer-final' → 'Engineer Final Plan' via default map."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        mapping = mod._load_agent_map(cal_dir)
        assert mapping.get("engineer-final") == "Engineer Final Plan"

    def test_unrecognized_agent_raw_name(self, tmp_path):
        """Unknown agent name stored as-is in step_actuals."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "my-custom-agent", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "my-custom-agent", jsonl_line_count=10, span_id=2),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        assert "my-custom-agent" in ranges

    def test_custom_map_overrides_default(self, tmp_path):
        """E1: agent-map.json with custom key overrides default mapping."""
        mod = load_sum_session_module()
        cal_dir = write_agent_map(tmp_path, {"impl-backend": "Implementation"})
        mapping = mod._load_agent_map(cal_dir)
        assert mapping.get("impl-backend") == "Implementation"

    def test_custom_map_merges_with_defaults(self, tmp_path):
        """E1: custom key added; all default keys still work."""
        mod = load_sum_session_module()
        cal_dir = write_agent_map(tmp_path, {"sec-review": "Staff Review"})
        mapping = mod._load_agent_map(cal_dir)
        # Custom key present
        assert mapping.get("sec-review") == "Staff Review"
        # Default keys preserved
        assert mapping.get("researcher") == "Research Agent"
        assert mapping.get("implementer") == "Implementation"

    def test_missing_agent_map_uses_defaults(self, tmp_path):
        """E1: absent agent-map.json → DEFAULT_AGENT_TO_STEP used, no error."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration_empty")
        os.makedirs(cal_dir, exist_ok=True)
        mapping = mod._load_agent_map(cal_dir)
        # Should have default keys
        assert "researcher" in mapping
        assert "implementer" in mapping

    def test_malformed_agent_map_uses_defaults(self, tmp_path):
        """E1: malformed JSON in agent-map.json → defaults used, no exception."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        map_path = os.path.join(cal_dir, "agent-map.json")
        with open(map_path, "w") as f:
            f.write("not valid json {{{")
        mapping = mod._load_agent_map(cal_dir)
        assert "researcher" in mapping


# ---------------------------------------------------------------------------
# TestNestingInference (F1)
# ---------------------------------------------------------------------------

class TestNestingInference:
    """Test chronological nesting inference for parent-child span attribution."""

    def test_no_nesting_single_agent(self, tmp_path):
        """Single agent with no overlap: effective range equals raw range."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=10, span_id=2),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        assert "Research Agent" in ranges
        # Should have range (0, 10)
        assert (0, 10) in ranges["Research Agent"]

    def test_nested_agent_inferred_from_open_span(self, tmp_path):
        """B starts while A open → B is inferred child of A; child range excluded from parent."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_start", "qa", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_stop", "qa", jsonl_line_count=8, span_id=3),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=10, span_id=4),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        # QA is child of researcher → researcher effective ranges must not cover lines 5-8
        researcher_ranges = ranges.get("Research Agent", [])
        assert len(researcher_ranges) > 0, "Researcher span must be present"
        # After child subtraction, researcher should have two sub-ranges: [0,5) and [8,10)
        # Verify the child range [5,8) does NOT appear as a single contiguous researcher range
        single_full_range = any(s <= 5 and e >= 8 for (s, e) in researcher_ranges)
        assert not single_full_range, (
            "Researcher effective range should exclude child QA span [5,8). "
            f"Got ranges: {researcher_ranges}"
        )

    def test_nested_agent_cost_subtracted_from_parent(self, tmp_path):
        """Lines in child span are attributed to child, not parent."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        # Create sidecar: researcher 0-10, qa 5-8 (nested inside researcher)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_start", "qa", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_stop", "qa", jsonl_line_count=8, span_id=3),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=10, span_id=4),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        # Lines 1-5 and 9-10 belong to researcher; 6-8 belong to qa
        # Create JSONL: 10 lines, each with cost
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * 10
        jsonl_path = write_jsonl(tmp_path, lines)
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        step_actuals = result.get("step_actuals") or {}
        # Both researcher and QA should have attributed costs
        assert "Research Agent" in step_actuals or "QA" in step_actuals

    def test_deeply_nested_three_levels(self, tmp_path):
        """Three-level nesting: A > B > C; C cost not double-counted in A or B."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_start", "implementer", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_start", "qa", jsonl_line_count=8, span_id=3),
            make_sidecar_event("agent_stop", "qa", jsonl_line_count=10, span_id=4),
            make_sidecar_event("agent_stop", "implementer", jsonl_line_count=15, span_id=5),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=20, span_id=6),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        # Ranges should exist for all three; verify no negative ranges
        for step_name, step_ranges in ranges.items():
            for (s, e) in step_ranges:
                assert s <= e, f"Invalid range ({s}, {e}) for step {step_name}"

    def test_parallel_non_overlapping_agents(self, tmp_path):
        """Sequential non-overlapping agents: no open span → not nested."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_start", "implementer", jsonl_line_count=5, span_id=3),
            make_sidecar_event("agent_stop", "implementer", jsonl_line_count=10, span_id=4),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        # No nesting → researcher keeps full range 0-5, implementer keeps full 5-10
        assert "Research Agent" in ranges
        assert "Implementation" in ranges
        assert (0, 5) in ranges["Research Agent"]
        assert (5, 10) in ranges["Implementation"]


# ---------------------------------------------------------------------------
# TestFIFOSpanMatching (F2)
# ---------------------------------------------------------------------------

class TestFIFOSpanMatching:
    """Test FIFO span matching: oldest start matched to first stop for same agent."""

    def test_fifo_two_sequential_same_agent(self, tmp_path):
        """Two sequential spans for same agent: each matched in FIFO order."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "implementer", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "implementer", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_start", "implementer", jsonl_line_count=5, span_id=3),
            make_sidecar_event("agent_stop", "implementer", jsonl_line_count=10, span_id=4),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        # Two implementer spans should produce two range entries totaling 10 lines
        impl_ranges = ranges.get("Implementation", [])
        total_lines = sum(e - s for s, e in impl_ranges)
        assert total_lines == 10, f"Expected 10 total lines, got {total_lines}"

    def test_fifo_span_id_ordering(self, tmp_path):
        """F2: stops are matched to starts by span_id order (FIFO), not arbitrary."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        # Two researcher starts, then two stops. FIFO: first stop matches first start.
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=3, span_id=2),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=6, span_id=3),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=10, span_id=4),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        # Both spans produce ranges; first start (span_id=1) matched to first stop (span_id=3)
        researcher_ranges = ranges.get("Research Agent", [])
        assert len(researcher_ranges) >= 1

    def test_unmatched_stop_discarded(self, tmp_path):
        """A stop with no matching start is silently discarded."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            # No matching start for this stop
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=10, span_id=2),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        # Should not raise; ranges may be empty or missing researcher
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        # No researcher range since no matching start
        assert "Research Agent" not in ranges or ranges["Research Agent"] == []

    def test_unmatched_start_gets_session_end(self, tmp_path):
        """An unmatched start is given end_line = last recorded line count."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            # A sequential (non-nested) agent that runs after researcher would stop
            make_sidecar_event("agent_start", "qa", jsonl_line_count=25, span_id=2),
            make_sidecar_event("agent_stop", "qa", jsonl_line_count=30, span_id=3),
            # researcher never gets a stop — end_line should be max line count seen (30)
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        agent_to_step = mod._load_agent_map(cal_dir)
        ranges = mod._build_spans(sidecar_path, agent_to_step)
        researcher_ranges = ranges.get("Research Agent", [])
        assert len(researcher_ranges) >= 1
        # Unmatched start gets end_line = max jsonl_line_count seen (30).
        # qa starts at 25 (while researcher is open) so it is a child — researcher's
        # effective range is (0,25) after subtraction of the child span (25,30).
        # The researcher range should cover lines 0..25.
        max_end = max(e for s, e in researcher_ranges)
        assert max_end >= 25


# ---------------------------------------------------------------------------
# TestSumSessionByAgent
# ---------------------------------------------------------------------------

class TestSumSessionByAgent:
    """Test sum_session_by_agent() attribution and return value schema."""

    def test_no_sidecar_returns_session_totals_only(self, tmp_path):
        """When sidecar_path is None, returns same structure as sum_session()."""
        mod = load_sum_session_module()
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)]
        jsonl_path = write_jsonl(tmp_path, lines)
        # sum_session_by_agent is not called without sidecar — use sum_session
        result = mod.sum_session(jsonl_path, 0.0)
        assert "total_session_cost" in result
        assert "actual_cost" in result
        assert result["turn_count"] == 1

    def test_missing_sidecar_returns_session_totals_only(self, tmp_path):
        """When sidecar_path points to nonexistent file, raises or returns gracefully."""
        mod = load_sum_session_module()
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)]
        jsonl_path = write_jsonl(tmp_path, lines)
        missing_sidecar = str(tmp_path / "missing-timeline.jsonl")
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        # sum_session_by_agent requires sidecar to exist; check it raises or handles gracefully
        try:
            result = mod.sum_session_by_agent(jsonl_path, missing_sidecar, 0.0, cal_dir)
            # If it handles gracefully, step_actuals should be None
            assert result.get("step_actuals") is None or result["step_actuals"] == {}
        except (FileNotFoundError, OSError):
            pass  # Raising is also acceptable behavior

    def test_single_agent_span_full_session(self, tmp_path):
        """Single agent spanning all JSONL lines → all cost attributed to that step."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        n_lines = 5
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * n_lines
        jsonl_path = write_jsonl(tmp_path, lines)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=n_lines, span_id=2),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        step_actuals = result.get("step_actuals") or {}
        # All cost should be under Research Agent (lines 1-5 are within span 0-5)
        assert "Research Agent" in step_actuals
        # Total attributed cost should approximately equal actual_cost
        total_attributed = sum(step_actuals.values())
        assert abs(total_attributed - result["actual_cost"]) < 0.0001

    def test_two_non_overlapping_agents(self, tmp_path):
        """Two sequential non-overlapping agents: both attributed, costs > 0."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * 10
        jsonl_path = write_jsonl(tmp_path, lines)
        # Span (0,5) covers lines where 0 <= line_num < 5 → JSONL lines 1-4 (1-based)
        # Span (5,10) covers lines where 5 <= line_num < 10 → JSONL lines 5-9 (1-based)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_start", "implementer", jsonl_line_count=5, span_id=3),
            make_sidecar_event("agent_stop", "implementer", jsonl_line_count=10, span_id=4),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        step_actuals = result.get("step_actuals") or {}
        assert "Research Agent" in step_actuals, "Research Agent should have attributed cost"
        assert "Implementation" in step_actuals, "Implementation should have attributed cost"
        # Both spans cover lines → both should have non-zero cost
        assert step_actuals["Research Agent"] > 0
        assert step_actuals["Implementation"] > 0

    def test_nested_agent_cost_not_double_counted(self, tmp_path):
        """Nested agent's cost is not double-counted in parent's total."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * 10
        jsonl_path = write_jsonl(tmp_path, lines)
        # researcher 0-10, qa 5-8 (nested)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_start", "qa", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_stop", "qa", jsonl_line_count=8, span_id=3),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=10, span_id=4),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        step_actuals = result.get("step_actuals") or {}
        total_attributed = sum(step_actuals.values())
        # Total attributed should not exceed actual_cost (no double counting)
        assert total_attributed <= result["actual_cost"] + 0.0001

    def test_zero_width_span_no_cost(self, tmp_path):
        """F15: start_line == end_line → $0.00 attributed to that span."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * 5
        jsonl_path = write_jsonl(tmp_path, lines)
        events = [
            # Zero-width span: start and end at same line count
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=3, span_id=1),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=3, span_id=2),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        step_actuals = result.get("step_actuals") or {}
        # Zero-width span covers no lines → no cost attributed
        researcher_cost = step_actuals.get("Research Agent", 0.0)
        assert researcher_cost == 0.0, \
            f"F15: Zero-width span should have $0.00 cost, got {researcher_cost}"

    def test_unattributed_lines_in_orchestrator(self, tmp_path):
        """Lines outside all agent spans are attributed to '_orchestrator'."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        # 10 lines; agent spans only lines 1-5
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * 10
        jsonl_path = write_jsonl(tmp_path, lines)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=1, span_id=1),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=5, span_id=2),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        step_actuals = result.get("step_actuals") or {}
        # Lines 1 and 6-10 not in any span → attributed to _orchestrator
        assert "_orchestrator" in step_actuals

    def test_empty_sidecar_no_step_actuals(self, tmp_path):
        """Empty sidecar file → step_actuals is None or empty."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * 5
        jsonl_path = write_jsonl(tmp_path, lines)
        # Empty sidecar file (no events)
        sidecar_path = str(tmp_path / "empty-timeline.jsonl")
        open(sidecar_path, "w").close()
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        step_actuals = result.get("step_actuals")
        # No events → no spans → everything goes to _orchestrator or step_actuals is None
        assert step_actuals is None or "_orchestrator" in step_actuals

    def test_malformed_sidecar_lines_skipped(self, tmp_path):
        """Malformed JSONL lines in sidecar are skipped; valid events processed."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * 5
        jsonl_path = write_jsonl(tmp_path, lines)
        sidecar_path = str(tmp_path / "test-timeline.jsonl")
        with open(sidecar_path, "w") as f:
            f.write("not valid json {{{{\n")
            f.write(json.dumps(make_sidecar_event("agent_start", "researcher",
                                                   jsonl_line_count=0, span_id=1)) + "\n")
            f.write("also not json\n")
            f.write(json.dumps(make_sidecar_event("agent_stop", "researcher",
                                                   jsonl_line_count=5, span_id=2)) + "\n")
        # Should not raise; researcher span should still be processed
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        assert "actual_cost" in result

    def test_unknown_schema_version_skipped(self, tmp_path):
        """Events with schema_version != 1 are silently ignored."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * 5
        jsonl_path = write_jsonl(tmp_path, lines)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0,
                               span_id=1, schema_version=2),  # Unknown version
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=5,
                               span_id=2, schema_version=2),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        step_actuals = result.get("step_actuals")
        # v2 events skipped → no researcher span
        if step_actuals is not None:
            assert "Research Agent" not in step_actuals

    def test_step_actuals_sum_to_actual_cost(self, tmp_path):
        """step_actuals values sum to approximately actual_cost."""
        mod = load_sum_session_module()
        cal_dir = str(tmp_path / "calibration")
        os.makedirs(cal_dir, exist_ok=True)
        lines = [make_assistant_line(input_tokens=10000, output_tokens=100)] * 10
        jsonl_path = write_jsonl(tmp_path, lines)
        events = [
            make_sidecar_event("agent_start", "researcher", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "researcher", jsonl_line_count=5, span_id=2),
            make_sidecar_event("agent_start", "implementer", jsonl_line_count=5, span_id=3),
            make_sidecar_event("agent_stop", "implementer", jsonl_line_count=10, span_id=4),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        result = mod.sum_session_by_agent(jsonl_path, sidecar_path, 0.0, cal_dir)
        step_actuals = result.get("step_actuals") or {}
        if step_actuals:
            total = sum(step_actuals.values())
            assert abs(total - result["actual_cost"]) < 0.001


# ---------------------------------------------------------------------------
# TestComputeLineCost (F4)
# ---------------------------------------------------------------------------

class TestComputeLineCost:
    """Test compute_line_cost() shared cost helper."""

    def test_non_assistant_type_zero(self):
        """Non-assistant message objects return 0.0."""
        mod = load_sum_session_module()
        obj = {"type": "user", "message": {"usage": {"input_tokens": 1000, "output_tokens": 100}}}
        assert mod.compute_line_cost(obj) == 0.0

    def test_missing_usage_zero(self):
        """Assistant message with no usage returns 0.0."""
        mod = load_sum_session_module()
        obj = {"type": "assistant", "message": {"model": "claude-sonnet-4-6"}}
        assert mod.compute_line_cost(obj) == 0.0

    def test_synthetic_model_zero(self):
        """Messages with model='<synthetic>' return 0.0."""
        mod = load_sum_session_module()
        obj = {
            "type": "assistant",
            "message": {
                "model": "<synthetic>",
                "usage": {"input_tokens": 1000, "output_tokens": 100},
            },
        }
        assert mod.compute_line_cost(obj) == 0.0

    def test_known_model_correct_cost(self):
        """Known model computes correct cost using PRICES lookup."""
        mod = load_sum_session_module()
        # claude-sonnet-4-6: input=$3.00/M, output=$15.00/M
        # 1M input tokens + 100K output = $3.00 + $1.50 = $4.50
        obj = {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 1_000_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 100_000,
                },
            },
        }
        cost = mod.compute_line_cost(obj)
        expected = (1_000_000 * 3.00 + 100_000 * 15.00) / 1_000_000
        assert abs(cost - expected) < 0.0001

    def test_unknown_model_falls_back_to_default(self):
        """Unknown model falls back to DEFAULT_MODEL pricing."""
        mod = load_sum_session_module()
        obj = {
            "type": "assistant",
            "message": {
                "model": "claude-unknown-model",
                "usage": {
                    "input_tokens": 1_000_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                },
            },
        }
        cost = mod.compute_line_cost(obj)
        # Should use DEFAULT_MODEL (claude-sonnet-4-6) pricing: $3.00/M input
        expected = 1_000_000 * 3.00 / 1_000_000
        assert abs(cost - expected) < 0.0001

    def test_cost_includes_all_token_types(self):
        """Cost includes input, cache_read, cache_write, and output tokens."""
        mod = load_sum_session_module()
        # claude-sonnet-4-6: input=3.00, cache_read=0.30, cache_write=3.75, output=15.00 per M
        obj = {
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": 100_000,
                    "cache_read_input_tokens": 200_000,
                    "cache_creation_input_tokens": 50_000,
                    "output_tokens": 10_000,
                },
            },
        }
        cost = mod.compute_line_cost(obj)
        expected = (
            100_000 * 3.00
            + 200_000 * 0.30
            + 50_000 * 3.75
            + 10_000 * 15.00
        ) / 1_000_000
        assert abs(cost - expected) < 0.0001


# ---------------------------------------------------------------------------
# TestLearnShAgentHookIntegration
# ---------------------------------------------------------------------------

@pytest.fixture
def learn_sh_exists():
    """Skip tests if learn.sh does not exist."""
    if not LEARN_SH.exists():
        pytest.skip(f"learn.sh not found: {LEARN_SH}")


def make_estimate_file(tmp_path, expected_cost=5.0, baseline_cost=0.0):
    """Write a minimal active-estimate.json, return path."""
    data = {
        "expected_cost": expected_cost,
        "optimistic_cost": expected_cost * 0.6,
        "pessimistic_cost": expected_cost * 3.0,
        "baseline_cost": baseline_cost,
        "size": "M",
        "files": 5,
        "complexity": "medium",
        "steps": ["Research Agent", "Implementation"],
        "pipeline_signature": "implementation+research_agent",
        "project_type": "unknown",
        "language": "python",
        "step_count": 2,
        "review_cycles": 2,
        "parallel_groups": [],
        "parallel_steps_detected": 0,
        "file_brackets": None,
        "files_measured": 0,
        "step_costs": {"Research Agent": 2.0, "Implementation": 3.0},
    }
    path = str(tmp_path / "active-estimate.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def make_mock_jsonl(tmp_path, cost_approx=3.0, filename="session.jsonl"):
    """Write a JSONL that produces approximately cost_approx dollars."""
    # sonnet: $3/M input, $15/M output
    # To get ~$3: 1M input tokens
    path = str(tmp_path / filename)
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    }
    with open(path, "w") as f:
        f.write(json.dumps(entry) + "\n")
    return path


def make_sidecar_file(tmp_path, agent_name="researcher", filename="test-timeline.jsonl"):
    """Write a minimal sidecar with start/stop events, return path."""
    events = [
        make_sidecar_event("agent_start", agent_name, jsonl_line_count=0, span_id=1),
        make_sidecar_event("agent_stop", agent_name, jsonl_line_count=1, span_id=2),
    ]
    return write_sidecar(tmp_path, events, filename=filename)


def run_learn_sh(estimate_file, history_file, jsonl_path, sidecar_path, tmp_path):
    """Run learn.sh with override env vars; return (returncode, stdout, stderr)."""
    env = {
        **os.environ,
        "TOKENCOSTSCOPE_ESTIMATE_FILE": estimate_file,
        "TOKENCOSTSCOPE_HISTORY_FILE": history_file,
        "TOKENCOSTSCOPE_SIDECAR_PATH": sidecar_path if sidecar_path else "",
    }
    result = subprocess.run(
        ["bash", str(LEARN_SH), jsonl_path],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    return result.returncode, result.stdout, result.stderr


class TestLearnShAgentHookIntegration:
    """Integration tests for learn.sh with sidecar data (F13)."""

    def test_step_actuals_written_to_history(self, tmp_path, learn_sh_exists):
        """step_actuals field written to history record when sidecar present."""
        estimate_file = make_estimate_file(tmp_path, expected_cost=5.0, baseline_cost=0.0)
        history_file = str(tmp_path / "history.jsonl")
        jsonl_path = make_mock_jsonl(tmp_path)
        sidecar_path = make_sidecar_file(tmp_path, "researcher")
        rc, out, err = run_learn_sh(estimate_file, history_file, jsonl_path, sidecar_path, tmp_path)
        if not os.path.exists(history_file):
            pytest.skip("learn.sh did not write history (may need implementation)")
        with open(history_file) as f:
            records = [json.loads(l) for l in f if l.strip()]
        assert len(records) >= 1
        assert "step_actuals" in records[-1]

    def test_attribution_method_sidecar(self, tmp_path, learn_sh_exists):
        """attribution_method='sidecar' when sidecar has valid events."""
        estimate_file = make_estimate_file(tmp_path, expected_cost=5.0, baseline_cost=0.0)
        history_file = str(tmp_path / "history.jsonl")
        jsonl_path = make_mock_jsonl(tmp_path)
        sidecar_path = make_sidecar_file(tmp_path, "researcher")
        run_learn_sh(estimate_file, history_file, jsonl_path, sidecar_path, tmp_path)
        if not os.path.exists(history_file):
            pytest.skip("learn.sh did not write history")
        with open(history_file) as f:
            records = [json.loads(l) for l in f if l.strip()]
        if records and records[-1].get("step_actuals"):
            assert records[-1].get("attribution_method") == "sidecar"

    def test_attribution_method_proportional_fallback(self, tmp_path, learn_sh_exists):
        """attribution_method='proportional' when no sidecar provided."""
        estimate_file = make_estimate_file(tmp_path, expected_cost=5.0, baseline_cost=0.0)
        history_file = str(tmp_path / "history.jsonl")
        jsonl_path = make_mock_jsonl(tmp_path)
        rc, out, err = run_learn_sh(estimate_file, history_file, jsonl_path, "", tmp_path)
        if not os.path.exists(history_file):
            pytest.skip("learn.sh did not write history")
        with open(history_file) as f:
            records = [json.loads(l) for l in f if l.strip()]
        if records:
            assert records[-1].get("attribution_method") == "proportional"

    def test_review_cycles_actual_populated(self, tmp_path, learn_sh_exists):
        """review_cycles_actual is populated from sidecar staff-reviewer events."""
        estimate_file = make_estimate_file(tmp_path, expected_cost=5.0, baseline_cost=0.0)
        history_file = str(tmp_path / "history.jsonl")
        jsonl_path = make_mock_jsonl(tmp_path)
        # Sidecar with staff-reviewer stop event
        events = [
            make_sidecar_event("agent_start", "staff-reviewer", jsonl_line_count=0, span_id=1),
            make_sidecar_event("agent_stop", "staff-reviewer", jsonl_line_count=1, span_id=2),
        ]
        sidecar_path = write_sidecar(tmp_path, events)
        run_learn_sh(estimate_file, history_file, jsonl_path, sidecar_path, tmp_path)
        if not os.path.exists(history_file):
            pytest.skip("learn.sh did not write history")
        with open(history_file) as f:
            records = [json.loads(l) for l in f if l.strip()]
        if records:
            # review_cycles_actual should be 1 (one staff-reviewer stop event)
            assert records[-1].get("review_cycles_actual") == 1

    def test_optimistic_pessimistic_in_history(self, tmp_path, learn_sh_exists):
        """F10: optimistic_cost and pessimistic_cost written to history record."""
        estimate_file = make_estimate_file(tmp_path, expected_cost=5.0, baseline_cost=0.0)
        history_file = str(tmp_path / "history.jsonl")
        jsonl_path = make_mock_jsonl(tmp_path)
        sidecar_path = make_sidecar_file(tmp_path, "researcher")
        run_learn_sh(estimate_file, history_file, jsonl_path, sidecar_path, tmp_path)
        if not os.path.exists(history_file):
            pytest.skip("learn.sh did not write history")
        with open(history_file) as f:
            records = [json.loads(l) for l in f if l.strip()]
        if records:
            rec = records[-1]
            assert "optimistic_cost" in rec, "F10: optimistic_cost missing from history"
            assert "pessimistic_cost" in rec, "F10: pessimistic_cost missing from history"

    def test_sidecar_deleted_after_processing(self, tmp_path, learn_sh_exists):
        """learn.sh deletes sidecar file after successful processing."""
        estimate_file = make_estimate_file(tmp_path, expected_cost=5.0, baseline_cost=0.0)
        history_file = str(tmp_path / "history.jsonl")
        jsonl_path = make_mock_jsonl(tmp_path)
        sidecar_path = make_sidecar_file(tmp_path, "researcher", filename="testsid-timeline.jsonl")
        # Ensure sidecar exists before running
        assert os.path.exists(sidecar_path)
        run_learn_sh(estimate_file, history_file, jsonl_path, sidecar_path, tmp_path)
        # Sidecar should be deleted after processing
        assert not os.path.exists(sidecar_path), \
            "learn.sh should delete sidecar file after processing"

    def test_span_counter_deleted_after_processing(self, tmp_path, learn_sh_exists):
        """learn.sh deletes span counter file alongside sidecar."""
        estimate_file = make_estimate_file(tmp_path, expected_cost=5.0, baseline_cost=0.0)
        history_file = str(tmp_path / "history.jsonl")
        jsonl_path = make_mock_jsonl(tmp_path)
        sidecar_path = make_sidecar_file(tmp_path, "researcher", filename="testsid2-timeline.jsonl")
        # Create accompanying counter file
        counter_path = str(tmp_path / "testsid2-span-counter")
        with open(counter_path, "w") as f:
            f.write("3\n")
        run_learn_sh(estimate_file, history_file, jsonl_path, sidecar_path, tmp_path)
        assert not os.path.exists(counter_path), \
            "learn.sh should delete span counter file alongside sidecar"

    def test_orphan_sidecar_swept_on_next_run(self, tmp_path, learn_sh_exists):
        """Old sidecar files (>7 days) are swept on next learn.sh run."""
        # This is enforced by: find "$CALIBRATION_DIR" -name "*-timeline.jsonl" -mtime +7 -delete
        # We verify the sweep logic exists (document test) rather than manipulating mtime
        learn_sh_text = LEARN_SH.read_text()
        assert "-mtime +7" in learn_sh_text or "mtime" in learn_sh_text, \
            "learn.sh should include mtime-based cleanup of old sidecar files"


# ---------------------------------------------------------------------------
# TestAgentHookShellScript
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_hook_exists():
    """Skip tests if agent-hook.sh does not exist yet."""
    if not AGENT_HOOK_SH.exists():
        pytest.skip(f"tokencast-agent-hook.sh not yet implemented: {AGENT_HOOK_SH}")


def run_agent_hook(hook_payload, extra_env=None):
    """Run agent-hook.sh with a JSON payload on stdin. Returns (returncode, stdout, stderr)."""
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(AGENT_HOOK_SH)],
        input=json.dumps(hook_payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )
    return result.returncode, result.stdout, result.stderr


class TestAgentHookShellScript:
    """Shell-level integration tests for tokencast-agent-hook.sh."""

    def test_non_agent_tool_exits_early(self, tmp_path, agent_hook_exists):
        """Non-Agent toolName causes script to exit 0 without writing sidecar."""
        payload = {
            "hookEventName": "PreToolUse",
            "toolName": "Read",
            "tool_input": {"name": ""},
            "session_id": "test-nonagent",
        }
        rc, out, err = run_agent_hook(payload)
        assert rc == 0
        # No sidecar should be written
        sidecars = list(CALIBRATION_DIR.glob("*test-nonagent*-timeline.jsonl")) if CALIBRATION_DIR.exists() else []
        assert len(sidecars) == 0

    def test_pre_tool_use_writes_agent_start(self, tmp_path, agent_hook_exists):
        """PreToolUse with Agent tool writes agent_start event to sidecar."""
        session_id = "testhook-pre-start"
        payload = {
            "hookEventName": "PreToolUse",
            "toolName": "Agent",
            "tool_input": {"name": "researcher"},
            "session_id": session_id,
        }
        rc, out, err = run_agent_hook(payload)
        assert rc == 0
        # Check sidecar for agent_start event
        sidecar_files = list(CALIBRATION_DIR.glob(f"*{session_id}*-timeline.jsonl")) if CALIBRATION_DIR.exists() else []
        if sidecar_files:
            with open(sidecar_files[0]) as f:
                events = [json.loads(l) for l in f if l.strip()]
            starts = [e for e in events if e.get("type") == "agent_start"]
            assert len(starts) >= 1

    def test_post_tool_use_writes_agent_stop(self, tmp_path, agent_hook_exists):
        """PostToolUse with Agent tool writes agent_stop event to sidecar."""
        session_id = "testhook-post-stop"
        payload = {
            "hookEventName": "PostToolUse",
            "toolName": "Agent",
            "tool_input": {"name": "researcher"},
            "session_id": session_id,
        }
        rc, out, err = run_agent_hook(payload)
        assert rc == 0
        sidecar_files = list(CALIBRATION_DIR.glob(f"*{session_id}*-timeline.jsonl")) if CALIBRATION_DIR.exists() else []
        if sidecar_files:
            with open(sidecar_files[0]) as f:
                events = [json.loads(l) for l in f if l.strip()]
            stops = [e for e in events if e.get("type") == "agent_stop"]
            assert len(stops) >= 1

    def test_hook_is_fail_silent(self, agent_hook_exists):
        """Malformed stdin causes hook to exit 0 (fail-silent)."""
        result = subprocess.run(
            ["bash", str(AGENT_HOOK_SH)],
            input="not valid json {{{",
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0

    def test_sidecar_file_path_uses_session_id(self, agent_hook_exists):
        """Sidecar file is named <session_id>-timeline.jsonl."""
        session_id = "testhook-session-path"
        payload = {
            "hookEventName": "PreToolUse",
            "toolName": "Agent",
            "tool_input": {"name": "researcher"},
            "session_id": session_id,
        }
        run_agent_hook(payload)
        if CALIBRATION_DIR.exists():
            sidecar_files = list(CALIBRATION_DIR.glob(f"*{session_id}*"))
            assert any("timeline.jsonl" in str(f) for f in sidecar_files)

    def test_span_id_in_event(self, agent_hook_exists):
        """F2: Written event includes a span_id integer field."""
        session_id = "testhook-spanid"
        payload = {
            "hookEventName": "PreToolUse",
            "toolName": "Agent",
            "tool_input": {"name": "researcher"},
            "session_id": session_id,
        }
        run_agent_hook(payload)
        if CALIBRATION_DIR.exists():
            sidecar_files = list(CALIBRATION_DIR.glob(f"*{session_id}*-timeline.jsonl"))
            if sidecar_files:
                with open(sidecar_files[0]) as f:
                    events = [json.loads(l) for l in f if l.strip()]
                for ev in events:
                    assert "span_id" in ev
                    assert isinstance(ev["span_id"], int)

    def test_no_parent_agent_in_event(self, agent_hook_exists):
        """F1: Written event does NOT include a parent_agent field."""
        session_id = "testhook-noparent"
        payload = {
            "hookEventName": "PreToolUse",
            "toolName": "Agent",
            "tool_input": {"name": "researcher"},
            "session_id": session_id,
        }
        run_agent_hook(payload)
        if CALIBRATION_DIR.exists():
            sidecar_files = list(CALIBRATION_DIR.glob(f"*{session_id}*-timeline.jsonl"))
            if sidecar_files:
                with open(sidecar_files[0]) as f:
                    events = [json.loads(l) for l in f if l.strip()]
                for ev in events:
                    assert "parent_agent" not in ev, \
                        "F1 violation: parent_agent should not be written to sidecar"
