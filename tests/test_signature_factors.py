"""Tests for per-signature calibration factors (v1.6.0).

Tests Pass 5 computation in update_factors(), signature normalization
(re-derive from steps array at Pass 1 read time), factors.json schema,
and document content verification.
"""
# Runner: pytest (required). Use: /usr/bin/python3 -m pytest tests/

import importlib.util
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SKILL_MD = REPO_ROOT / "SKILL.md"
HEURISTICS_MD = REPO_ROOT / "references" / "heuristics.md"
CALIBRATION_ALG_MD = REPO_ROOT / "references" / "calibration-algorithm.md"

_spec = importlib.util.spec_from_file_location("update_factors", str(SCRIPTS_DIR / "update-factors.py"))
assert _spec is not None, "Could not load spec for update-factors.py"
assert _spec.loader is not None, "Spec loader is None"
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
update_factors = _mod.update_factors


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sig_record(ratio=1.0, days_ago=0, steps=None, pipeline_signature=None):
    """Create a minimal history record with optional steps array and/or pipeline_signature."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    record = {
        "timestamp": ts,
        "size": "M",
        "expected_cost": 5.0,
        "actual_cost": 5.0 * ratio,
    }
    if steps is not None:
        record["steps"] = steps
    if pipeline_signature is not None:
        record["pipeline_signature"] = pipeline_signature
    return record


def run_update_factors(records):
    """Write records to temp history.jsonl, call update_factors, return parsed factors dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        history_path = os.path.join(tmpdir, "history.jsonl")
        factors_path = os.path.join(tmpdir, "factors.json")
        with open(history_path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        update_factors(history_path, factors_path)
        if not os.path.exists(factors_path):
            return {}
        with open(factors_path) as f:
            return json.load(f)


# ---------------------------------------------------------------------------
# Class 1: TestSignatureNormalization
# ---------------------------------------------------------------------------

class TestSignatureNormalization:
    """Tests for _canonical_sig derivation at Pass 1 read time."""

    def test_steps_sorted_alphabetically(self):
        """steps: ['Implementation', 'Research Agent'] → 'implementation+research_agent' (sorted)."""
        records = [
            make_sig_record(ratio=1.0, steps=["Implementation", "Research Agent"])
            for _ in range(3)
        ]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        # Expected canonical form: sorted alphabetically, lowercased, spaces to underscores
        assert "implementation+research_agent" in sig_factors, (
            f"Expected 'implementation+research_agent' in {list(sig_factors.keys())}"
        )

    def test_steps_lowercased_and_underscored(self):
        """'Research Agent' → 'research_agent' (lowercased, space → underscore)."""
        records = [
            make_sig_record(ratio=1.0, steps=["Research Agent"])
            for _ in range(3)
        ]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        assert "research_agent" in sig_factors, (
            f"Expected 'research_agent' in {list(sig_factors.keys())}"
        )

    def test_no_steps_array_uses_raw_signature(self):
        """Record with pipeline_signature but no steps array: raw signature used."""
        records = [
            make_sig_record(ratio=1.0, pipeline_signature="impl+test")
            for _ in range(3)
        ]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        assert "impl+test" in sig_factors, (
            f"Expected 'impl+test' in {list(sig_factors.keys())}"
        )

    def test_empty_steps_array_skipped_in_pass5(self):
        """steps: [] produces empty canonical sig, record skipped in Pass 5."""
        # These records have empty steps — they contribute to global factor but not signature_factors
        records = [
            make_sig_record(ratio=1.0, steps=[])
            for _ in range(3)
        ]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        # Empty string key should not be present
        assert "" not in sig_factors, "Empty-string signature should be excluded from signature_factors"

    def test_freetext_sig_overridden_by_steps_array(self):
        """When both 'steps' and 'pipeline_signature' are present, steps array wins."""
        records = [
            make_sig_record(ratio=1.0, steps=["Implementation"], pipeline_signature="full_pipeline")
            for _ in range(3)
        ]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        # steps-derived canonical form should win
        assert "implementation" in sig_factors, "Expected steps-derived key"
        # freetext value should NOT appear as a separate key
        assert "full_pipeline" not in sig_factors, "freetext pipeline_signature should be overridden"

    def test_canonical_form_roundtrips(self):
        """Records with matching steps produce a consistent signature key."""
        steps_list = ["Architect Agent", "Research Agent", "Implementation"]
        expected_sig = "architect_agent+implementation+research_agent"
        records = [make_sig_record(ratio=1.0, steps=steps_list) for _ in range(3)]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        assert expected_sig in sig_factors, f"Expected '{expected_sig}' in {list(sig_factors.keys())}"


# ---------------------------------------------------------------------------
# Class 2: TestSignatureFactorComputation
# ---------------------------------------------------------------------------

class TestSignatureFactorComputation:
    """Tests for Pass 5 factor values and statuses."""

    def test_3_records_same_sig_activates_factor(self):
        """3 records with same steps array → status 'active'."""
        records = [
            make_sig_record(ratio=1.2, steps=["Implementation", "Research Agent"])
            for _ in range(3)
        ]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        entry = sig_factors.get("implementation+research_agent", {})
        assert entry.get("status") == "active", f"Expected 'active', got {entry}"

    def test_2_records_same_sig_stays_collecting(self):
        """2 records with same steps → status 'collecting'."""
        # Need >= 3 global records for update_factors to run; add a third with different sig
        records = [
            make_sig_record(ratio=1.0, steps=["Implementation", "Research Agent"]),
            make_sig_record(ratio=1.0, steps=["Implementation", "Research Agent"]),
            make_sig_record(ratio=1.0, steps=["Architect Agent"]),  # different sig
        ]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        entry = sig_factors.get("implementation+research_agent", {})
        assert entry.get("status") == "collecting", f"Expected 'collecting', got {entry}"

    def test_different_sigs_separate_strata(self):
        """Two different step arrays each produce their own signature_factors entry."""
        records_a = [make_sig_record(ratio=1.0, steps=["Implementation"]) for _ in range(3)]
        records_b = [make_sig_record(ratio=1.5, steps=["Research Agent"]) for _ in range(3)]
        factors = run_update_factors(records_a + records_b)
        sig_factors = factors.get("signature_factors", {})
        assert "implementation" in sig_factors
        assert "research_agent" in sig_factors
        # Different factors since different ratios
        assert sig_factors["implementation"]["factor"] != sig_factors["research_agent"]["factor"]

    def test_signature_factors_absent_when_no_data(self):
        """Records without steps or pipeline_signature: signature_factors key absent from factors."""
        # Records with no steps and no pipeline_signature
        records = [
            {"timestamp": "2026-01-01T00:00:00Z", "size": "M", "expected_cost": 5.0, "actual_cost": 4.5}
            for _ in range(3)
        ]
        factors = run_update_factors(records)
        assert "signature_factors" not in factors, (
            f"Expected no signature_factors, but got: {factors.get('signature_factors')}"
        )

    def test_factor_value_matches_expected_mean(self):
        """4 records same sig, ratio=1.1 all from today → factor ≈ 1.1."""
        records = [
            make_sig_record(ratio=1.1, days_ago=0, steps=["Implementation"])
            for _ in range(4)
        ]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        entry = sig_factors.get("implementation", {})
        if entry:
            assert abs(entry.get("factor", 0) - 1.1) < 0.01, (
                f"Expected factor ≈ 1.1, got {entry.get('factor')}"
            )

    def test_decay_applies_to_signature_factors(self):
        """6 records same sig: first 5 old (ratio=2.0), last recent (ratio=0.5).

        Factor should be pulled toward 0.5 due to decay weighting.
        """
        old_records = [make_sig_record(ratio=2.0, days_ago=90, steps=["Implementation"]) for _ in range(5)]
        recent_record = make_sig_record(ratio=0.5, days_ago=0, steps=["Implementation"])
        records = old_records + [recent_record]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        entry = sig_factors.get("implementation", {})
        if entry and entry.get("status") == "active":
            # With decay, recent record (weight ≈ 1.0) dominates old ones (weight ≈ 0.06)
            # Factor should be significantly below the unweighted mean of ~1.75
            assert entry["factor"] < 1.5, (
                f"Expected decay to pull signature factor toward 0.5, got {entry['factor']}"
            )

    def test_freetext_sig_collects_but_stays_isolated(self):
        """Freetext-only signature accumulates independently from steps-derived signatures."""
        freetext_records = [
            make_sig_record(ratio=1.3, pipeline_signature="custom_pipeline")
            for _ in range(3)
        ]
        steps_records = [
            make_sig_record(ratio=0.9, steps=["Implementation"])
            for _ in range(3)
        ]
        factors = run_update_factors(freetext_records + steps_records)
        sig_factors = factors.get("signature_factors", {})
        # Both should have their own entries
        assert "custom_pipeline" in sig_factors
        assert "implementation" in sig_factors
        # They should have different factors
        assert sig_factors["custom_pipeline"]["factor"] != sig_factors["implementation"]["factor"]


# ---------------------------------------------------------------------------
# Class 3: TestSignatureFactorsSchema
# ---------------------------------------------------------------------------

class TestSignatureFactorsSchema:
    """Tests for factors.json schema when signature_factors is present."""

    def _make_active_sig_factors(self):
        """Helper: produce factors dict with at least one active signature entry."""
        records = [
            make_sig_record(ratio=1.1, steps=["Implementation", "Research Agent"])
            for _ in range(3)
        ]
        return run_update_factors(records)

    def test_signature_factors_key_present_with_3_records(self):
        """3+ same-sig records: 'signature_factors' key present in factors."""
        factors = self._make_active_sig_factors()
        assert "signature_factors" in factors

    def test_entry_has_factor_n_status(self):
        """Each signature_factors entry has 'factor' (float), 'n' (int), 'status' (str)."""
        factors = self._make_active_sig_factors()
        sig_factors = factors.get("signature_factors", {})
        for sig, entry in sig_factors.items():
            assert "factor" in entry, f"Missing 'factor' in {sig}: {entry}"
            assert "n" in entry, f"Missing 'n' in {sig}: {entry}"
            assert "status" in entry, f"Missing 'status' in {sig}: {entry}"
            assert isinstance(entry["factor"], float), f"'factor' should be float: {entry}"
            assert isinstance(entry["n"], int), f"'n' should be int: {entry}"
            assert isinstance(entry["status"], str), f"'status' should be str: {entry}"

    def test_active_entry_n_ge_3(self):
        """Active entries have n >= 3 (per_signature_min_samples = 3)."""
        factors = self._make_active_sig_factors()
        sig_factors = factors.get("signature_factors", {})
        for sig, entry in sig_factors.items():
            if entry.get("status") == "active":
                assert entry["n"] >= 3, f"Active entry '{sig}' has n={entry['n']} < 3"

    def test_collecting_entry_n_lt_3(self):
        """Collecting entries have n < 3 (finding 13: threshold is n < 3, not n < 2)."""
        # 2 records same sig + 1 different → sig has n=2 → collecting
        records = [
            make_sig_record(ratio=1.0, steps=["Implementation", "Research Agent"]),
            make_sig_record(ratio=1.0, steps=["Implementation", "Research Agent"]),
            make_sig_record(ratio=1.0, steps=["Architect Agent"]),
        ]
        factors = run_update_factors(records)
        sig_factors = factors.get("signature_factors", {})
        entry = sig_factors.get("implementation+research_agent", {})
        if entry:
            assert entry.get("status") == "collecting", f"Expected collecting for n=2: {entry}"
            assert entry["n"] < 3, f"Collecting entry should have n < 3: {entry}"


# ---------------------------------------------------------------------------
# Class 4: TestDocumentContent
# ---------------------------------------------------------------------------

class TestDocumentContent:
    """Verify required document content. Some tests fail until implementation is complete."""

    def test_skill_md_has_per_signature_in_step3e(self):
        """SKILL.md Step 3e contains 'per-signature' (case-insensitive).

        NOTE: This test FAILS until Group 2 (SKILL.md) is implemented.
        """
        content = SKILL_MD.read_text()
        assert "per-signature" in content.lower(), (
            "SKILL.md Step 3e should document per-signature factor precedence"
        )

    def test_skill_md_cal_column_has_p_indicator(self):
        """SKILL.md contains 'P:x.xx' Cal column indicator.

        NOTE: This test FAILS until Group 2 (SKILL.md) is implemented.
        """
        content = SKILL_MD.read_text()
        assert "P:" in content, "SKILL.md Cal column should document P: indicator for per-signature"

    def test_calibration_alg_has_phase5(self):
        """references/calibration-algorithm.md contains 'Phase 5'."""
        content = CALIBRATION_ALG_MD.read_text()
        assert "Phase 5" in content, "calibration-algorithm.md should document Phase 5"

    def test_heuristics_has_per_signature_min_samples(self):
        """references/heuristics.md contains 'per_signature_min_samples' parameter."""
        content = HEURISTICS_MD.read_text()
        assert "per_signature_min_samples" in content, (
            "heuristics.md should contain per_signature_min_samples parameter"
        )

    def test_factors_json_example_has_signature_factors(self):
        """references/calibration-algorithm.md contains 'signature_factors' example."""
        content = CALIBRATION_ALG_MD.read_text()
        assert "signature_factors" in content, (
            "calibration-algorithm.md should contain signature_factors in factors.json schema"
        )
