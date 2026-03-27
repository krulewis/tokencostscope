"""Tests for pipeline enforcement hooks in .claude/hooks/."""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"


def _run_hook(hook_name, payload, env_overrides=None, tmp_dir=None):
    """Run a hook script with a JSON payload via subprocess."""
    hook_path = str(HOOKS_DIR / hook_name)
    if tmp_dir is None:
        tmp_dir = tempfile.mkdtemp()
    # Start from clean env — remove TOKENCAST_SKIP_GATE unless explicitly set by test
    env = {k: v for k, v in os.environ.items() if k != "TOKENCAST_SKIP_GATE"}
    env["TMPDIR"] = tmp_dir
    if env_overrides:
        env.update(env_overrides)
    result = subprocess.run(
        ["bash", hook_path],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env=env,
    )
    return result


class TestEstimateGate(unittest.TestCase):
    """Tests for estimate-gate.sh — PreToolUse hook (Agent matcher)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.calib_dir = os.path.join(self.tmp_dir, "calibration")
        os.makedirs(self.calib_dir)
        self.env = {"CALIBRATION_DIR": self.calib_dir}

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _payload(self, agent_type="implementer"):
        return {
            "tool_name": "Agent",
            "tool_input": {
                "subagent_type": agent_type,
                "prompt": "implement feature",
                "description": "test",
            },
        }

    def test_missing_estimate_blocks_implementer(self):
        """T1: No active-estimate.json -> exit 2 for all impl agents."""
        for agent in ["implementer", "qa", "debugger"]:
            result = _run_hook(
                "estimate-gate.sh", self._payload(agent), self.env, self.tmp_dir
            )
            self.assertEqual(result.returncode, 2, f"{agent} should be blocked")
            self.assertIn(b"BLOCKED", result.stderr)

    def test_fresh_estimate_allows_implementer(self):
        """T2: Fresh active-estimate.json -> exit 0."""
        est_file = os.path.join(self.calib_dir, "active-estimate.json")
        with open(est_file, "w") as f:
            json.dump({"version": "test"}, f)
        result = _run_hook("estimate-gate.sh", self._payload(), self.env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)

    def test_planning_agent_passes_without_estimate(self):
        """T3: Planning agents (researcher, architect, etc.) pass freely."""
        for agent in ["researcher", "architect", "engineer", "pm"]:
            result = _run_hook(
                "estimate-gate.sh", self._payload(agent), self.env, self.tmp_dir
            )
            self.assertEqual(result.returncode, 0, f"{agent} should pass freely")

    def test_skip_gate_bypasses_block(self):
        """T4: TOKENCAST_SKIP_GATE=1 bypasses the block."""
        env = {**self.env, "TOKENCAST_SKIP_GATE": "1"}
        result = _run_hook("estimate-gate.sh", self._payload(), env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)

    def test_stale_estimate_blocks_implementer(self):
        """T5: Estimate older than 24h -> exit 2."""
        est_file = os.path.join(self.calib_dir, "active-estimate.json")
        with open(est_file, "w") as f:
            json.dump({"version": "test"}, f)
        # Set mtime to 25 hours ago
        old_time = time.time() - (25 * 3600)
        os.utime(est_file, (old_time, old_time))
        result = _run_hook("estimate-gate.sh", self._payload(), self.env, self.tmp_dir)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"stale", result.stderr)

    def test_xs_size_marker_bypasses_gate(self):
        """T6: XS size marker file -> exit 0 even without estimate."""
        marker = os.path.join(self.tmp_dir, "size-marker")
        with open(marker, "w") as f:
            f.write("XS")
        env = {**self.env, "TOKENCAST_SIZE_MARKER": marker}
        result = _run_hook("estimate-gate.sh", self._payload(), env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)

    def test_s_size_marker_bypasses_gate(self):
        """T6b: S size marker file -> exit 0 even without estimate."""
        marker = os.path.join(self.tmp_dir, "size-marker")
        with open(marker, "w") as f:
            f.write("S")
        env = {**self.env, "TOKENCAST_SIZE_MARKER": marker}
        result = _run_hook("estimate-gate.sh", self._payload(), env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)

    def test_path_derivation_without_calibration_dir(self):
        """Verify hook derives calibration path from its own directory."""
        # Do NOT set CALIBRATION_DIR — let the hook use dirname-based derivation
        # The hook is at .claude/hooks/ — two levels up is project root
        # So it will look for <project_root>/calibration/active-estimate.json
        calib_dir = str(REPO_ROOT / "calibration")
        est_file = os.path.join(calib_dir, "active-estimate.json")
        had_file = os.path.exists(est_file)
        try:
            os.makedirs(calib_dir, exist_ok=True)
            with open(est_file, "w") as f:
                json.dump({"version": "path-test"}, f)
            # Run WITHOUT CALIBRATION_DIR override
            result = _run_hook(
                "estimate-gate.sh", self._payload(), tmp_dir=self.tmp_dir
            )
            self.assertEqual(result.returncode, 0)
        finally:
            # Clean up only if we created the file
            if not had_file and os.path.exists(est_file):
                os.remove(est_file)

    def test_m_size_marker_does_not_bypass(self):
        """T6c: M size marker -> gate still applies (exit 2 without estimate)."""
        marker = os.path.join(self.tmp_dir, "size-marker")
        with open(marker, "w") as f:
            f.write("M")
        env = {**self.env, "TOKENCAST_SIZE_MARKER": marker}
        result = _run_hook("estimate-gate.sh", self._payload(), env, self.tmp_dir)
        self.assertEqual(result.returncode, 2)


class TestValidateAgentType(unittest.TestCase):
    """Tests for validate-agent-type.sh — PreToolUse hook (Agent matcher)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _payload(self, agent_type, tool_name="Agent"):
        return {
            "tool_name": tool_name,
            "tool_input": {
                "subagent_type": agent_type,
                "prompt": "test",
                "description": "test",
            },
        }

    def test_known_agent_passes(self):
        """T7: Known agent types pass validation."""
        for agent in [
            "implementer",
            "qa",
            "debugger",
            "researcher",
            "architect",
            "engineer",
            "pm",
            "staff-reviewer",
            "frontend-designer",
            "docs-updater",
            "code-reviewer",
            "explorer",
            "playwright-qa",
        ]:
            result = _run_hook(
                "validate-agent-type.sh", self._payload(agent), tmp_dir=self.tmp_dir
            )
            self.assertEqual(result.returncode, 0, f"{agent} should be allowed")

    def test_unknown_agent_blocked(self):
        """T8: Unknown agent type -> exit 2."""
        result = _run_hook(
            "validate-agent-type.sh",
            self._payload("gpt-researcher"),
            tmp_dir=self.tmp_dir,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"not a custom agent", result.stderr)

    def test_non_agent_tool_passes(self):
        """Non-Agent tool calls are not validated."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
        }
        result = _run_hook(
            "validate-agent-type.sh", payload, tmp_dir=self.tmp_dir
        )
        self.assertEqual(result.returncode, 0)

    def test_skip_gate_bypasses(self):
        """TOKENCAST_SKIP_GATE=1 bypasses validation."""
        result = _run_hook(
            "validate-agent-type.sh",
            self._payload("gpt-researcher"),
            {"TOKENCAST_SKIP_GATE": "1"},
            self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0)

    def test_sr_pm_is_allowed(self):
        """sr-pm is in the whitelist (defined in global agents)."""
        result = _run_hook(
            "validate-agent-type.sh", self._payload("sr-pm"), tmp_dir=self.tmp_dir
        )
        self.assertEqual(result.returncode, 0)

    def test_empty_agent_type_fails_open(self):
        """Empty subagent_type -> fail-open (exit 0)."""
        payload = {
            "tool_name": "Agent",
            "tool_input": {"prompt": "test", "description": "test"},
        }
        result = _run_hook(
            "validate-agent-type.sh", payload, tmp_dir=self.tmp_dir
        )
        self.assertEqual(result.returncode, 0)


class TestBranchGuard(unittest.TestCase):
    """Tests for branch-guard.sh — PreToolUse hook (Bash matcher)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.fake_bin = os.path.join(self.tmp_dir, "bin")
        os.makedirs(self.fake_bin)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_fake_git(self, branch="main"):
        """Create a fake git that returns a specific branch name."""
        fake_git = os.path.join(self.fake_bin, "git")
        with open(fake_git, "w") as f:
            f.write(
                f'#!/bin/sh\n'
                f'if [ "$1" = "branch" ] && [ "$2" = "--show-current" ]; then '
                f'echo "{branch}"; exit 0; fi\n'
                f'exit 0\n'
            )
        os.chmod(fake_git, 0o755)
        return {"PATH": f"{self.fake_bin}:{os.environ.get('PATH', '')}"}

    def _payload(self, command):
        return {
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }

    def test_commit_on_main_blocked(self):
        """T9: git commit on main -> exit 2."""
        env = self._make_fake_git("main")
        result = _run_hook(
            "branch-guard.sh", self._payload("git commit -m 'test'"), env, self.tmp_dir
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"BLOCKED", result.stderr)
        self.assertIn(b"main", result.stderr)

    def test_commit_on_feature_branch_passes(self):
        """Commit on feature branch -> exit 0."""
        env = self._make_fake_git("feature-xyz")
        result = _run_hook(
            "branch-guard.sh", self._payload("git commit -m 'test'"), env, self.tmp_dir
        )
        self.assertEqual(result.returncode, 0)

    def test_commit_message_with_git_push_not_triggered(self):
        """T10: Commit message containing 'git push' should not trigger push gate."""
        env = self._make_fake_git("feature-xyz")
        result = _run_hook(
            "branch-guard.sh",
            self._payload('git commit -m "do not git push to main"'),
            env,
            self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0)

    def test_single_quoted_commit_message_with_git_push(self):
        """HIGH-3 fix: Single-quoted commit message with 'git push' not triggered."""
        env = self._make_fake_git("feature-xyz")
        result = _run_hook(
            "branch-guard.sh",
            self._payload("git commit -m 'ensure we never git push to main'"),
            env,
            self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0)

    def test_push_without_marker_blocked(self):
        """T11: git push without review marker -> exit 2."""
        env = self._make_fake_git("feature-xyz")
        result = _run_hook(
            "branch-guard.sh", self._payload("git push origin feature-xyz"), env, self.tmp_dir
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"BLOCKED: Push requires", result.stderr)

    def test_push_with_marker_passes_and_consumes(self):
        """T12: Push with marker file -> exit 0, marker consumed."""
        env = self._make_fake_git("feature-xyz")
        # We need to create the marker at the path the hook will look for.
        # The hook uses $PPID which will be our Python process PID.
        # Run a probe first to discover the PPID the hook will use.
        probe = subprocess.run(
            ["bash", "-c", "echo $PPID"],
            capture_output=True,
            env={**os.environ, "TMPDIR": self.tmp_dir},
        )
        ppid = probe.stdout.decode().strip()
        marker = os.path.join(self.tmp_dir, f"tokencast-push-reviewed-{ppid}")
        with open(marker, "w") as f:
            f.write("")
        result = _run_hook(
            "branch-guard.sh", self._payload("git push origin feature-xyz"), env, self.tmp_dir
        )
        self.assertEqual(result.returncode, 0)
        # Marker should be consumed
        self.assertFalse(os.path.exists(marker), "Marker should be consumed after push")

    def test_non_git_command_passes(self):
        """Non-git commands pass through."""
        result = _run_hook(
            "branch-guard.sh", self._payload("echo hello"), tmp_dir=self.tmp_dir
        )
        self.assertEqual(result.returncode, 0)

    def test_skip_gate_bypasses(self):
        """TOKENCAST_SKIP_GATE=1 bypasses commit-on-main block."""
        env = {**self._make_fake_git("main"), "TOKENCAST_SKIP_GATE": "1"}
        result = _run_hook(
            "branch-guard.sh", self._payload("git commit -m 'test'"), env, self.tmp_dir
        )
        self.assertEqual(result.returncode, 0)


class TestInlineEditGuard(unittest.TestCase):
    """Tests for inline-edit-guard.sh — PostToolUse hook (Edit|Write matcher)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _payload(self, file_path, agent_type=None):
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": file_path},
            "tool_output": "ok",
        }
        if agent_type:
            payload["agent_type"] = agent_type
        return payload

    def test_sub_agent_context_suppressed(self):
        """T13: agent_type present -> suppressed (sub-agent context)."""
        result = _run_hook(
            "inline-edit-guard.sh",
            self._payload("/project/src/foo.py", agent_type="implementer"),
            tmp_dir=self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_three_unique_code_files_warns(self):
        """T14: 3 unique code file edits -> warning output."""
        files = [
            "/project/src/a.py",
            "/project/src/b.py",
            "/project/src/c.py",
        ]
        for f in files:
            result = _run_hook(
                "inline-edit-guard.sh", self._payload(f), tmp_dir=self.tmp_dir
            )
        # The third invocation should produce the warning
        self.assertIn(b"DELEGATION GUARD", result.stdout)
        self.assertIn(b"3", result.stdout)

    def test_docs_path_not_counted(self):
        """T15: docs/ paths are not counted as code files."""
        files = [
            "/project/docs/a.md",
            "/project/docs/b.md",
            "/project/docs/c.md",
            "/project/docs/d.md",
        ]
        for f in files:
            result = _run_hook(
                "inline-edit-guard.sh", self._payload(f), tmp_dir=self.tmp_dir
            )
        self.assertNotIn(b"DELEGATION GUARD", result.stdout)

    def test_skip_gate_bypasses(self):
        """TOKENCAST_SKIP_GATE=1 suppresses output."""
        result = _run_hook(
            "inline-edit-guard.sh",
            self._payload("/project/src/foo.py"),
            {"TOKENCAST_SKIP_GATE": "1"},
            self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_references_path_not_counted(self):
        """references/ paths are not code paths."""
        result = _run_hook(
            "inline-edit-guard.sh",
            self._payload("/project/references/heuristics.md"),
            tmp_dir=self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(b"DELEGATION GUARD", result.stdout)

    def test_scripts_path_counted(self):
        """scripts/ paths are counted as code paths."""
        files = [
            "/project/scripts/a.py",
            "/project/scripts/b.py",
            "/project/scripts/c.sh",
        ]
        for f in files:
            result = _run_hook(
                "inline-edit-guard.sh", self._payload(f), tmp_dir=self.tmp_dir
            )
        self.assertIn(b"DELEGATION GUARD", result.stdout)

    def test_second_edit_same_file_not_double_counted(self):
        """Same file edited twice -> count stays at 1."""
        for _ in range(3):
            result = _run_hook(
                "inline-edit-guard.sh",
                self._payload("/project/src/same.py"),
                tmp_dir=self.tmp_dir,
            )
        # Only 1 unique file, should not warn
        self.assertNotIn(b"DELEGATION GUARD", result.stdout)


class TestPipelineGate(unittest.TestCase):
    """Tests for pipeline-gate.sh — UserPromptSubmit hook."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_short_prompt_suppressed(self):
        """T16: Short prompt ('yes') -> no output."""
        payload = {"prompt": "yes"}
        result = _run_hook("pipeline-gate.sh", payload, tmp_dir=self.tmp_dir)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(b"PIPELINE GATE", result.stdout)

    def test_long_prompt_injects_reminder(self):
        """T17: Long prompt -> PIPELINE GATE table injected."""
        payload = {"prompt": "implement the new authentication feature for users"}
        result = _run_hook("pipeline-gate.sh", payload, tmp_dir=self.tmp_dir)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"PIPELINE GATE", result.stdout)
        self.assertIn(b"estimate-gate.sh", result.stdout)

    def test_resets_inline_edit_counter(self):
        """T18: Resets inline-edit-guard unique_files.txt."""
        # Pre-populate the unique_files.txt at the path the hook will use
        # The hook uses PPID — discover it via probe
        probe = subprocess.run(
            ["bash", "-c", "echo $PPID"],
            capture_output=True,
            env={**os.environ, "TMPDIR": self.tmp_dir},
        )
        ppid = probe.stdout.decode().strip()
        session_dir = os.path.join(self.tmp_dir, f"tokencast-unique-files-{ppid}")
        os.makedirs(session_dir, exist_ok=True)
        unique_file = os.path.join(session_dir, "unique_files.txt")
        with open(unique_file, "w") as f:
            f.write("/project/src/a.py\n/project/src/b.py\n")

        payload = {"prompt": "implement the new authentication feature for users"}
        _run_hook("pipeline-gate.sh", payload, tmp_dir=self.tmp_dir)

        # unique_files.txt should be deleted
        self.assertFalse(
            os.path.exists(unique_file), "unique_files.txt should be deleted"
        )

    def test_skip_gate_suppresses_output(self):
        """TOKENCAST_SKIP_GATE=1 suppresses output."""
        payload = {"prompt": "implement the new authentication feature for users"}
        result = _run_hook(
            "pipeline-gate.sh",
            payload,
            {"TOKENCAST_SKIP_GATE": "1"},
            self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(b"PIPELINE GATE", result.stdout)


class TestPreCompactReminder(unittest.TestCase):
    """Tests for pre-compact-reminder.sh — PreCompact hook."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_outputs_reminder(self):
        """T19: Outputs pipeline enforcement reminder."""
        result = _run_hook("pre-compact-reminder.sh", {}, tmp_dir=self.tmp_dir)
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"CONTEXT COMPACTED", result.stdout)
        self.assertIn(b"active-estimate.json", result.stdout)

    def test_skip_gate_suppresses(self):
        """TOKENCAST_SKIP_GATE=1 suppresses output."""
        result = _run_hook(
            "pre-compact-reminder.sh",
            {},
            {"TOKENCAST_SKIP_GATE": "1"},
            self.tmp_dir,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")


class TestSkipGate(unittest.TestCase):
    """T20: All hooks pass with TOKENCAST_SKIP_GATE=1."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.env = {"TOKENCAST_SKIP_GATE": "1"}

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_estimate_gate_skip(self):
        payload = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "implementer", "prompt": "t", "description": "t"},
        }
        result = _run_hook("estimate-gate.sh", payload, self.env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)

    def test_validate_agent_type_skip(self):
        payload = {
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "unknown-agent", "prompt": "t", "description": "t"},
        }
        result = _run_hook("validate-agent-type.sh", payload, self.env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)

    def test_branch_guard_skip(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "git commit -m 'test'"}}
        result = _run_hook("branch-guard.sh", payload, self.env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)

    def test_inline_edit_guard_skip(self):
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "/project/src/foo.py"},
            "tool_output": "ok",
        }
        result = _run_hook("inline-edit-guard.sh", payload, self.env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)

    def test_pipeline_gate_skip(self):
        payload = {"prompt": "implement the new authentication feature for users"}
        result = _run_hook("pipeline-gate.sh", payload, self.env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)

    def test_pre_compact_reminder_skip(self):
        result = _run_hook("pre-compact-reminder.sh", {}, self.env, self.tmp_dir)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
