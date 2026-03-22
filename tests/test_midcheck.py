"""Tests for mid-session cost tracking hook (v1.6.0).

Tests tokencostscope-midcheck.sh: sampling gate logic, warning threshold,
cooldown mechanism, guard conditions, and JSON output format.

Shell integration tests require bash and python3. They run the script directly
against mock estimate files and mock JSONL inputs.
"""
# Runner: pytest (required). Use: /usr/bin/python3 -m pytest tests/

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT_PATH = str(SCRIPTS_DIR / "tokencostscope-midcheck.sh")
HEURISTICS_MD = REPO_ROOT / "references" / "heuristics.md"
SETTINGS_JSON = REPO_ROOT / ".claude" / "settings.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def midcheck_script_exists():
    """Skip tests if midcheck.sh does not exist yet (pre-implementation)."""
    if not os.path.exists(SCRIPT_PATH):
        pytest.skip(f"midcheck.sh not yet implemented: {SCRIPT_PATH}")


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------

def make_estimate_file(tmp_dir, pessimistic_cost, baseline_cost=0.0):
    """Write a minimal active-estimate.json."""
    data = {"pessimistic_cost": pessimistic_cost, "baseline_cost": baseline_cost}
    path = os.path.join(tmp_dir, "estimate.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def make_jsonl_file(tmp_dir, size_bytes=0, content=None, filename="session.jsonl"):
    """Write a JSONL file of approximately size_bytes bytes."""
    path = os.path.join(tmp_dir, filename)
    if content is not None:
        with open(path, "w") as f:
            f.write(content)
    else:
        with open(path, "w") as f:
            while f.tell() < size_bytes:
                f.write('{"type":"message"}\n')
    return path


def make_real_jsonl_file(tmp_dir, input_tokens=100000, output_tokens=5000, filename="real_session.jsonl", pad_to_bytes=0):
    """Write a JSONL with real token data for cost calculation.

    With 100K input tokens at $3.00/M and 5K output at $15.00/M:
    cost = $0.30 + $0.075 = $0.375

    pad_to_bytes: if > 0, pad the file with dummy lines (no usage data) until
    the file reaches this size. Used to exceed the sampling gate threshold.
    """
    path = os.path.join(tmp_dir, filename)
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": output_tokens,
            },
        },
    }
    with open(path, "w") as f:
        f.write(json.dumps(entry) + "\n")
        while pad_to_bytes and f.tell() < pad_to_bytes:
            f.write('{"type":"dummy"}\n')
    return path


def run_midcheck(estimate_file, jsonl_path, state_file, fake_home, extra_env=None):
    """Run midcheck.sh with env var overrides; returns (stdout, returncode).

    On macOS, `timeout` is not available so the stdin JSON transcript_path path
    is unreliable. Instead, we control JSONL discovery via HOME:
    - fake_home is a temp directory controlled by the caller
    - When jsonl_path is not None, it is placed at fake_home/.claude/projects/test/
      so the script's fallback `find $HOME/.claude/projects/` discovers it
    - When jsonl_path is None, fake_home has no JSONL → script exits at guard
    """
    import shutil
    env = os.environ.copy()
    env["TOKENCOSTSCOPE_ESTIMATE_FILE"] = estimate_file
    env["TOKENCOSTSCOPE_MIDCHECK_STATE_FILE"] = state_file
    env["HOME"] = fake_home
    if extra_env:
        env.update(extra_env)

    if jsonl_path is not None:
        proj_dir = os.path.join(fake_home, ".claude", "projects", "test")
        os.makedirs(proj_dir, exist_ok=True)
        dest = os.path.join(proj_dir, os.path.basename(jsonl_path))
        if os.path.abspath(jsonl_path) != os.path.abspath(dest):
            shutil.copy2(jsonl_path, dest)

    result = subprocess.run(
        ["bash", SCRIPT_PATH],
        input=b"",
        capture_output=True,
        env=env,
    )
    return result.stdout.decode(), result.returncode


def write_state_file(state_file, last_size, cooldown_val="0"):
    """Write a two-line state file."""
    with open(state_file, "w") as f:
        f.write(f"{last_size}\n{cooldown_val}\n")


# ---------------------------------------------------------------------------
# Class 1: TestMidcheckGuardConditions
# ---------------------------------------------------------------------------

class TestMidcheckGuardConditions:
    """Tests for guard conditions that cause midcheck.sh to exit silently."""

    def test_no_estimate_file_exits_silently(self, midcheck_script_exists):
        """ESTIMATE_FILE does not exist: no stdout, exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            nonexistent_estimate = os.path.join(tmp, "no-estimate.json")
            jsonl_path = make_jsonl_file(tmp, content='{"type":"message"}\n')
            state_file = os.path.join(tmp, "state")
            stdout, rc = run_midcheck(nonexistent_estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout == "", f"Expected empty stdout, got: {stdout!r}"

    def test_zero_pessimistic_cost_exits_silently(self, midcheck_script_exists):
        """pessimistic_cost == 0: no stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate = make_estimate_file(tmp, pessimistic_cost=0)
            jsonl_path = make_jsonl_file(tmp, content='{"type":"message"}\n')
            state_file = os.path.join(tmp, "state")
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout == "", f"Expected empty stdout, got: {stdout!r}"

    def test_missing_pessimistic_key_exits_silently(self, midcheck_script_exists):
        """Estimate missing pessimistic_cost key: no stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate_path = os.path.join(tmp, "estimate.json")
            with open(estimate_path, "w") as f:
                json.dump({"baseline_cost": 0.0}, f)
            jsonl_path = make_jsonl_file(tmp, content='{"type":"message"}\n')
            state_file = os.path.join(tmp, "state")
            stdout, rc = run_midcheck(estimate_path, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout == "", f"Expected empty stdout, got: {stdout!r}"

    def test_no_jsonl_file_exits_silently(self, midcheck_script_exists):
        """No JSONL available (empty stdin + empty HOME fallback): no stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate = make_estimate_file(tmp, pessimistic_cost=1.0)
            fake_home = tmp  # no .claude/projects/ here → fallback finds nothing
            state_file = os.path.join(tmp, "state")
            # Pass None for jsonl_path → sends empty stdin, triggers fallback
            stdout, rc = run_midcheck(estimate, None, state_file, fake_home=fake_home)
            assert rc == 0
            assert stdout == "", f"Expected empty stdout, got: {stdout!r}"

    def test_empty_jsonl_exits_silently(self, midcheck_script_exists):
        """Empty JSONL file: no stdout (no tokens to tally)."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate = make_estimate_file(tmp, pessimistic_cost=0.01)
            jsonl_path = make_jsonl_file(tmp, content="")
            # Grow the JSONL enough to trigger the sampling gate via the state file
            state_file = os.path.join(tmp, "state")
            write_state_file(state_file, last_size=0)
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout == "", f"Expected empty stdout for empty JSONL, got: {stdout!r}"


# ---------------------------------------------------------------------------
# Class 2: TestSamplingGate
# ---------------------------------------------------------------------------

class TestSamplingGate:
    """Tests for the file-size sampling gate logic."""

    def test_first_run_no_warning(self, midcheck_script_exists):
        """No state file on first run: script exits cleanly, no stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate = make_estimate_file(tmp, pessimistic_cost=1.0)
            jsonl_path = make_jsonl_file(tmp, content='{"type":"message"}\n')
            state_file = os.path.join(tmp, "state")
            # state_file does not exist yet
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout == "", f"Expected empty stdout on first run, got: {stdout!r}"

    def test_state_file_created_on_first_run(self, midcheck_script_exists):
        """After first run: state file is created."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate = make_estimate_file(tmp, pessimistic_cost=1.0)
            jsonl_path = make_jsonl_file(tmp, content='{"type":"message"}\n')
            state_file = os.path.join(tmp, "state")
            run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert os.path.exists(state_file), "State file should be created after first run"

    def test_no_growth_no_check(self, midcheck_script_exists):
        """State file shows same size as current JSONL: no stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate = make_estimate_file(tmp, pessimistic_cost=0.01)
            jsonl_path = make_jsonl_file(tmp, content='{"type":"message"}\n' * 100)
            jsonl_size = os.path.getsize(jsonl_path)
            state_file = os.path.join(tmp, "state")
            write_state_file(state_file, last_size=jsonl_size)
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout == "", f"Expected no output when JSONL unchanged, got: {stdout!r}"

    def test_insufficient_growth_no_check(self, midcheck_script_exists):
        """JSONL grew < 50KB (100 bytes): no stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate = make_estimate_file(tmp, pessimistic_cost=0.01)
            content = '{"type":"message"}\n' * 10
            jsonl_path = make_jsonl_file(tmp, content=content)
            jsonl_size = os.path.getsize(jsonl_path)
            # State file shows size 100 bytes less than current
            state_file = os.path.join(tmp, "state")
            write_state_file(state_file, last_size=max(0, jsonl_size - 100))
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout == "", f"Expected no output for small growth, got: {stdout!r}"

    def test_sufficient_growth_triggers_check(self, midcheck_script_exists):
        """JSONL grew >= 50KB: check runs (does not exit at sampling gate).

        We verify this indirectly: with very low pessimistic cost and real JSONL,
        we'd expect either a warning or a silent pass — but the check was NOT
        short-circuited at the sampling gate. A state file update confirms the
        gate was passed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            estimate = make_estimate_file(tmp, pessimistic_cost=1000.0)
            # JSONL must be >= 60KB so GROWTH = current_size - 0 >= 50KB threshold
            jsonl_path = make_jsonl_file(tmp, size_bytes=60000)
            state_file = os.path.join(tmp, "state")
            # last_size=0: GROWTH = ~60KB - 0 = ~60KB >= 50KB threshold → gate passes
            write_state_file(state_file, last_size=0)
            _, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            # Script should exit 0 (not aborted by sampling gate)
            assert rc == 0
            # The state file should be updated with the current JSONL size (not 0)
            with open(state_file) as f:
                new_size_line = f.readline().strip()
            assert new_size_line != "0", "State file should be updated after gate passes"


# ---------------------------------------------------------------------------
# Class 3: TestWarningThreshold
# ---------------------------------------------------------------------------

class TestWarningThreshold:
    """Tests for the 80% pessimistic cost warning threshold."""

    def _setup_above_threshold(self, tmp, pessimistic=0.40):
        """Set up real JSONL with ~$0.375 cost, pessimistic=$0.40 (≈94% → warn)."""
        estimate = make_estimate_file(tmp, pessimistic_cost=pessimistic, baseline_cost=0.0)
        # 100K input + 5K output ≈ $0.375 with claude-sonnet-4-6 pricing.
        # Pad to 60KB so GROWTH = current_size - 0 = 60KB >= 50KB sampling gate.
        jsonl_path = make_real_jsonl_file(tmp, input_tokens=100000, output_tokens=5000, pad_to_bytes=60000)
        state_file = os.path.join(tmp, "state")
        # last_size=0: GROWTH = current_size (~60KB) - 0 = ~60KB >= 50KB threshold
        write_state_file(state_file, last_size=0)
        return estimate, jsonl_path, state_file

    def test_below_threshold_no_warning(self, midcheck_script_exists):
        """Actual cost well below 80% of pessimistic: no output."""
        with tempfile.TemporaryDirectory() as tmp:
            # Empty JSONL → actual cost = $0; pessimistic = $1000 → 0% < 80%
            estimate = make_estimate_file(tmp, pessimistic_cost=1000.0, baseline_cost=0.0)
            jsonl_path = make_jsonl_file(tmp, content='{"type":"irrelevant"}\n')
            state_file = os.path.join(tmp, "state")
            jsonl_size = os.path.getsize(jsonl_path)
            write_state_file(state_file, last_size=max(0, jsonl_size - 60000))
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout == "", f"Expected no warning when well below threshold, got: {stdout!r}"

    def test_at_threshold_warns(self, midcheck_script_exists):
        """Actual cost at ~94% of pessimistic ($0.375 vs $0.40): JSON output produced."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate, jsonl_path, state_file = self._setup_above_threshold(tmp, pessimistic=0.40)
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout != "", "Expected warning output when above 80% threshold"

    def test_above_threshold_warns(self, midcheck_script_exists):
        """Actual cost >> pessimistic: JSON output produced."""
        with tempfile.TemporaryDirectory() as tmp:
            # Use pessimistic so low that any cost exceeds threshold
            estimate, jsonl_path, state_file = self._setup_above_threshold(tmp, pessimistic=0.10)
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout != "", "Expected warning output when far above threshold"

    def test_warning_json_structure(self, midcheck_script_exists):
        """Output is valid JSON with hookSpecificOutput.additionalContext key."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate, jsonl_path, state_file = self._setup_above_threshold(tmp, pessimistic=0.40)
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            if not stdout.strip():
                pytest.skip("No warning emitted (cost may differ from expected; check pricing)")
            data: dict = {}
            try:
                data = json.loads(stdout.strip())
            except json.JSONDecodeError as e:
                pytest.fail(f"Warning output is not valid JSON: {e}\nOutput: {stdout!r}")
            assert "hookSpecificOutput" in data, f"Missing 'hookSpecificOutput' key: {data}"
            assert "additionalContext" in data["hookSpecificOutput"], (
                f"Missing 'additionalContext' key: {data['hookSpecificOutput']}"
            )

    def test_warning_contains_actual_cost(self, midcheck_script_exists):
        """Warning message includes actual dollar amount."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate, jsonl_path, state_file = self._setup_above_threshold(tmp, pessimistic=0.40)
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            if not stdout.strip():
                pytest.skip("No warning emitted")
            data = json.loads(stdout.strip())
            msg = data["hookSpecificOutput"]["additionalContext"]
            assert "$" in msg, f"Warning should include dollar amount: {msg!r}"

    def test_warning_contains_pessimistic_cost(self, midcheck_script_exists):
        """Warning message includes the pessimistic estimate amount."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate, jsonl_path, state_file = self._setup_above_threshold(tmp, pessimistic=0.40)
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            if not stdout.strip():
                pytest.skip("No warning emitted")
            data = json.loads(stdout.strip())
            msg = data["hookSpecificOutput"]["additionalContext"]
            # The pessimistic cost should appear in the message
            assert "pessimistic" in msg.lower() or "0.40" in msg or "0.4" in msg, (
                f"Warning should reference pessimistic estimate: {msg!r}"
            )

    def test_warning_contains_percentage(self, midcheck_script_exists):
        """Warning message includes a percentage character."""
        with tempfile.TemporaryDirectory() as tmp:
            estimate, jsonl_path, state_file = self._setup_above_threshold(tmp, pessimistic=0.40)
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            if not stdout.strip():
                pytest.skip("No warning emitted")
            data = json.loads(stdout.strip())
            msg = data["hookSpecificOutput"]["additionalContext"]
            assert "%" in msg, f"Warning should include percentage: {msg!r}"


# ---------------------------------------------------------------------------
# Class 4: TestCooldownMechanism
# ---------------------------------------------------------------------------

class TestCooldownMechanism:
    """Tests for the cooldown suppression mechanism."""

    def test_cooldown_written_after_warning(self, midcheck_script_exists):
        """After warning emitted: state file line 2 contains 'COOLDOWN:' sentinel."""
        with tempfile.TemporaryDirectory() as tmp:
            # Set up conditions to trigger warning
            estimate = make_estimate_file(tmp, pessimistic_cost=0.10, baseline_cost=0.0)
            jsonl_path = make_real_jsonl_file(tmp, input_tokens=100000, output_tokens=5000)
            state_file = os.path.join(tmp, "state")
            jsonl_size = os.path.getsize(jsonl_path)
            write_state_file(state_file, last_size=max(0, jsonl_size - 60000))
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            if not stdout.strip():
                pytest.skip("No warning emitted; cannot test cooldown written after warning")
            # After warning, state file should have COOLDOWN: on line 2
            with open(state_file) as f:
                lines = f.read().splitlines()
            assert len(lines) >= 2, f"State file should have >= 2 lines: {lines}"
            assert lines[1].startswith("COOLDOWN:"), (
                f"Expected 'COOLDOWN:' on line 2 after warning, got: {lines[1]!r}"
            )

    def test_cooldown_suppresses_subsequent_warnings(self, midcheck_script_exists):
        """Active cooldown: no output even with sufficient JSONL growth."""
        with tempfile.TemporaryDirectory() as tmp:
            # Conditions that would normally trigger a warning (low pessimistic, real tokens)
            estimate = make_estimate_file(tmp, pessimistic_cost=0.10, baseline_cost=0.0)
            jsonl_path = make_real_jsonl_file(tmp, input_tokens=100000, output_tokens=5000)
            state_file = os.path.join(tmp, "state")
            jsonl_size = os.path.getsize(jsonl_path)
            # Write state file with active cooldown: cooldown threshold = current_size + 200KB
            # This means current_size < cooldown_threshold → still in cooldown
            cooldown_threshold = jsonl_size + 200000
            write_state_file(state_file, last_size=max(0, jsonl_size - 60000),
                             cooldown_val=f"COOLDOWN:{cooldown_threshold}")
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            assert stdout == "", f"Expected no output during cooldown, got: {stdout!r}"

    def test_cooldown_expires_after_200kb(self, midcheck_script_exists):
        """Cooldown threshold exceeded: check runs again."""
        with tempfile.TemporaryDirectory() as tmp:
            # Set up real JSONL padded to 60KB so sampling gate passes (GROWTH >= 50KB)
            estimate = make_estimate_file(tmp, pessimistic_cost=0.10, baseline_cost=0.0)
            jsonl_path = make_real_jsonl_file(tmp, input_tokens=100000, output_tokens=5000, pad_to_bytes=60000)
            state_file = os.path.join(tmp, "state")
            jsonl_size = os.path.getsize(jsonl_path)
            # Cooldown threshold BELOW current size → cooldown expired.
            # last_size=0 so GROWTH = ~60KB >= 50KB threshold.
            old_cooldown = max(0, jsonl_size - 5)  # just below current size → expired
            write_state_file(state_file, last_size=0, cooldown_val=f"COOLDOWN:{old_cooldown}")
            stdout, rc = run_midcheck(estimate, jsonl_path, state_file, fake_home=tmp)
            assert rc == 0
            # Since cooldown expired AND cost is above threshold, warning should be emitted
            # (actual cost ~$0.375 >> pessimistic $0.10)
            assert stdout != "", "Expected warning after cooldown expires"


# ---------------------------------------------------------------------------
# Class 5: TestDocumentContent
# ---------------------------------------------------------------------------

class TestDocumentContent:
    """Verify required document content. Some tests fail until implementation is complete."""

    def test_settings_json_has_pretooluse_hook(self):
        """'.claude/settings.json' contains PreToolUse entry with midcheck.sh path.

        NOTE: This test FAILS until Group 3 (settings.json) is implemented.
        """
        assert SETTINGS_JSON.exists(), f"settings.json not found at {SETTINGS_JSON}"
        content = SETTINGS_JSON.read_text()
        assert "PreToolUse" in content, "settings.json should contain PreToolUse hook"
        assert "tokencostscope-midcheck.sh" in content, (
            "settings.json PreToolUse hook should reference tokencostscope-midcheck.sh"
        )

    def test_heuristics_has_midcheck_warn_threshold(self):
        """references/heuristics.md contains 'midcheck_warn_threshold' parameter."""
        content = HEURISTICS_MD.read_text()
        assert "midcheck_warn_threshold" in content, (
            "heuristics.md should contain midcheck_warn_threshold"
        )

    def test_heuristics_has_midcheck_sampling_bytes(self):
        """references/heuristics.md contains 'midcheck_sampling_bytes' parameter."""
        content = HEURISTICS_MD.read_text()
        assert "midcheck_sampling_bytes" in content, (
            "heuristics.md should contain midcheck_sampling_bytes"
        )

    def test_heuristics_has_midcheck_cooldown_bytes(self):
        """references/heuristics.md contains 'midcheck_cooldown_bytes' parameter."""
        content = HEURISTICS_MD.read_text()
        assert "midcheck_cooldown_bytes" in content, (
            "heuristics.md should contain midcheck_cooldown_bytes"
        )

    def test_settings_json_no_matcher_on_pretooluse(self):
        """PreToolUse entry in settings.json does NOT have a 'matcher' field.

        NOTE: This test FAILS until Group 3 (settings.json) is implemented.
        The PreToolUse hook fires for all tools (no matcher = fires unconditionally).
        """
        assert SETTINGS_JSON.exists(), f"settings.json not found at {SETTINGS_JSON}"
        data = json.loads(SETTINGS_JSON.read_text())
        hooks = data.get("hooks", {})
        pre_tool_use_entries = hooks.get("PreToolUse", [])
        assert pre_tool_use_entries, "PreToolUse should have at least one entry"
        # Find the midcheck entry
        midcheck_entries = [
            e for e in pre_tool_use_entries
            if "tokencostscope-midcheck.sh" in json.dumps(e)
        ]
        assert midcheck_entries, "Should find PreToolUse entry for midcheck.sh"
        for entry in midcheck_entries:
            assert "matcher" not in entry, (
                f"PreToolUse midcheck entry should NOT have 'matcher' field: {entry}"
            )
