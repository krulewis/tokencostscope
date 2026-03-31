# Run with: /usr/bin/python3 -m pytest tests/test_estimate_step_resolution.py -v
"""Tests for step name resolution in the estimation engine.

Part 1: Integration tests — compute_estimate with agent alias names must
produce non-zero estimates. A $0.00 estimate when steps are provided is
always a bug (caught by these tests).

Part 2: Unit tests — failure modes around user-entered step names:
null/empty values, long strings, special characters, unicode, whitespace,
case variations, duplicates, SQL injection attempts, etc.
"""

import sys
import tempfile
import unittest
import warnings
from pathlib import Path

_src_root = Path(__file__).resolve().parent.parent / "src"
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

from tokencast.estimation_engine import compute_estimate, _resolve_steps  # noqa: E402
from tokencast.step_names import (  # noqa: E402
    DEFAULT_AGENT_TO_STEP,
    resolve_step_name,
    CANONICAL_STEP_NAMES,
    PR_REVIEW_LOOP_NAME,
)
from tokencast import heuristics  # noqa: E402


# ---------------------------------------------------------------------------
# Part 1: Integration tests — $0.00 is always wrong when steps are provided
# ---------------------------------------------------------------------------


class TestEstimateNeverZeroWithValidSteps(unittest.TestCase):
    """compute_estimate must produce non-zero costs when valid steps are given.

    A $0.00 estimate with recognized steps is the bug that silently broke
    all MCP tool calls before the alias resolution fix.
    """

    def _estimate(self, steps, **kwargs):
        params = {
            "size": kwargs.get("size", "M"),
            "files": kwargs.get("files", 3),
            "complexity": kwargs.get("complexity", "medium"),
            "steps": steps,
            "review_cycles": kwargs.get("review_cycles", 2),
        }
        return compute_estimate(params)

    def test_agent_aliases_produce_nonzero_estimate(self):
        """Agent alias names (qa, implementer, staff-reviewer) must resolve."""
        result = self._estimate(["qa", "implementer", "staff-reviewer"])
        self.assertGreater(result["estimate"]["expected"], 0.0)

    def test_canonical_names_produce_nonzero_estimate(self):
        """Canonical PIPELINE_STEPS names must produce non-zero estimates."""
        result = self._estimate(["QA", "Implementation", "Staff Review"])
        self.assertGreater(result["estimate"]["expected"], 0.0)

    def test_mixed_alias_and_canonical(self):
        """Mix of aliases and canonical names both resolve correctly."""
        result = self._estimate(["qa", "Implementation", "staff-reviewer"])
        self.assertGreater(result["estimate"]["expected"], 0.0)
        # Should have 3 steps + PR Review Loop
        step_names = [s["name"] for s in result["steps"]]
        self.assertIn("QA", step_names)
        self.assertIn("Implementation", step_names)
        self.assertIn("Staff Review", step_names)

    def test_all_agent_aliases_resolve(self):
        """Every alias in DEFAULT_AGENT_TO_STEP that maps to a PIPELINE_STEPS
        key must produce a non-zero estimate."""
        for alias, canonical in DEFAULT_AGENT_TO_STEP.items():
            if canonical not in heuristics.PIPELINE_STEPS:
                continue  # skip aliases for steps without token budgets
            result = self._estimate([alias])
            self.assertGreater(
                result["estimate"]["expected"],
                0.0,
                f"Alias {alias!r} (→ {canonical!r}) produced $0.00",
            )

    def test_all_pipeline_steps_produce_nonzero(self):
        """Every key in PIPELINE_STEPS must produce a non-zero single-step estimate."""
        for step_name in heuristics.PIPELINE_STEPS:
            result = self._estimate([step_name])
            self.assertGreater(
                result["estimate"]["expected"],
                0.0,
                f"Step {step_name!r} produced $0.00",
            )

    def test_underscore_aliases_resolve(self):
        """Underscore-separated aliases (staff_reviewer, docs_updater) resolve."""
        result = self._estimate(["staff_reviewer"])
        step_names = [s["name"] for s in result["steps"]]
        self.assertIn("Staff Review", step_names)

    def test_hyphen_aliases_resolve(self):
        """Hyphen-separated aliases (staff-reviewer, engineer-initial) resolve."""
        result = self._estimate(["staff-reviewer"])
        step_names = [s["name"] for s in result["steps"]]
        self.assertIn("Staff Review", step_names)

    def test_case_insensitive_aliases(self):
        """Aliases are case-insensitive: QA, Qa, qA, qa all resolve."""
        for variant in ["QA", "Qa", "qA", "qa"]:
            result = self._estimate([variant])
            self.assertGreater(
                result["estimate"]["expected"],
                0.0,
                f"Case variant {variant!r} produced $0.00",
            )

    def test_estimate_with_no_steps_uses_all(self):
        """No steps override → uses all PIPELINE_STEPS → non-zero."""
        result = self._estimate(None)
        self.assertGreater(result["estimate"]["expected"], 0.0)
        # Should include all pipeline steps
        step_names = [s["name"] for s in result["steps"]]
        for name in heuristics.PIPELINE_STEPS:
            self.assertIn(name, step_names)


# ---------------------------------------------------------------------------
# Part 2: Unit tests — failure modes around user-entered step names
# ---------------------------------------------------------------------------


class TestResolveStepNameFailureModes(unittest.TestCase):
    """resolve_step_name must handle garbage input without crashing."""

    def test_empty_string(self):
        """Empty string resolves to itself (unknown), no crash."""
        canonical, warn = resolve_step_name("")
        self.assertEqual(canonical, "")
        self.assertIsNone(warn)

    def test_whitespace_only(self):
        """Whitespace-only string strips to empty, no crash."""
        canonical, warn = resolve_step_name("   ")
        self.assertEqual(canonical, "")
        self.assertIsNone(warn)

    def test_leading_trailing_whitespace(self):
        """Leading/trailing whitespace is stripped before lookup."""
        canonical, warn = resolve_step_name("  qa  ")
        self.assertEqual(canonical, "QA")

    def test_tab_and_newline_whitespace(self):
        """Tabs and newlines in step name are stripped."""
        canonical, warn = resolve_step_name("\tqa\n")
        self.assertEqual(canonical, "QA")

    def test_very_long_string(self):
        """Very long string (10K chars) does not crash or hang."""
        long_name = "a" * 10_000
        canonical, warn = resolve_step_name(long_name)
        self.assertEqual(canonical, long_name)
        self.assertIsNone(warn)

    def test_unicode_characters(self):
        """Unicode characters don't crash resolution."""
        canonical, warn = resolve_step_name("研究者")
        self.assertEqual(canonical, "研究者")
        self.assertIsNone(warn)

    def test_emoji(self):
        """Emoji in step name doesn't crash — passes through as unknown."""
        canonical, warn = resolve_step_name("🔧implementer")
        self.assertEqual(canonical, "🔧implementer")
        self.assertIsNone(warn)

    def test_special_characters_semicolons(self):
        """Semicolons, pipes, ampersands don't crash."""
        for char in [";", "|", "&", "&&", "||", "$", "`"]:
            canonical, warn = resolve_step_name(f"qa{char}rm -rf /")
            self.assertIsInstance(canonical, str)

    def test_sql_injection_attempt(self):
        """SQL injection strings pass through harmlessly."""
        canonical, warn = resolve_step_name("'; DROP TABLE steps; --")
        self.assertIsInstance(canonical, str)
        self.assertIsNone(warn)

    def test_path_traversal_attempt(self):
        """Path traversal strings pass through harmlessly."""
        canonical, warn = resolve_step_name("../../etc/passwd")
        self.assertIsInstance(canonical, str)

    def test_null_bytes(self):
        """Null bytes in string don't crash."""
        canonical, warn = resolve_step_name("qa\x00injected")
        self.assertIsInstance(canonical, str)

    def test_json_injection(self):
        """JSON-like strings don't crash."""
        canonical, warn = resolve_step_name('{"key": "value"}')
        self.assertIsInstance(canonical, str)

    def test_html_tags(self):
        """HTML in step name passes through harmlessly."""
        canonical, warn = resolve_step_name("<script>alert(1)</script>")
        self.assertIsInstance(canonical, str)

    def test_numeric_string(self):
        """Pure numeric string resolves to itself (unknown)."""
        canonical, warn = resolve_step_name("12345")
        self.assertEqual(canonical, "12345")
        self.assertIsNone(warn)

    def test_pr_review_loop_special_case(self):
        """PR Review Loop is a derived step — returns with warning."""
        canonical, warn = resolve_step_name("PR Review Loop")
        self.assertEqual(canonical, PR_REVIEW_LOOP_NAME)
        self.assertEqual(warn, "pr_review_loop_is_derived")

    def test_canonical_name_case_sensitive(self):
        """Canonical names match case-sensitively (step 3 in resolution)."""
        # "QA" is canonical → matches
        canonical, warn = resolve_step_name("QA")
        self.assertEqual(canonical, "QA")
        # "qa" matches via alias (step 2), not canonical (step 3)
        canonical2, _ = resolve_step_name("qa")
        self.assertEqual(canonical2, "QA")

    def test_unknown_name_passes_through(self):
        """Unknown names are accepted as-is per protocol."""
        canonical, warn = resolve_step_name("totally_made_up_step")
        self.assertEqual(canonical, "totally_made_up_step")
        self.assertIsNone(warn)


class TestResolveStepsFailureModes(unittest.TestCase):
    """_resolve_steps must handle garbage in the steps list gracefully."""

    def test_empty_list(self):
        """Empty steps list → returns all PIPELINE_STEPS (fallback)."""
        result = _resolve_steps("M", [])
        self.assertEqual(result, list(heuristics.PIPELINE_STEPS.keys()))

    def test_none_steps(self):
        """None steps → returns all PIPELINE_STEPS (fallback)."""
        result = _resolve_steps("M", None)
        self.assertEqual(result, list(heuristics.PIPELINE_STEPS.keys()))

    def test_all_unknown_steps_returns_empty(self):
        """All unrecognized step names → empty list with warnings."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _resolve_steps("M", ["bogus1", "bogus2", "bogus3"])
        self.assertEqual(result, [])
        self.assertGreaterEqual(len(w), 1)

    def test_mix_of_valid_and_invalid(self):
        """Valid steps kept, invalid dropped with warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _resolve_steps("M", ["qa", "bogus", "implementer"])
        self.assertEqual(result, ["QA", "Implementation"])
        self.assertEqual(len(w), 1)
        self.assertIn("bogus", str(w[0].message))

    def test_duplicate_steps_preserved(self):
        """Duplicate step names are kept (caller's intent)."""
        result = _resolve_steps("M", ["qa", "qa"])
        self.assertEqual(result, ["QA", "QA"])

    def test_empty_string_in_list(self):
        """Empty string in steps list → dropped with warning, no crash."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _resolve_steps("M", ["", "qa"])
        self.assertEqual(result, ["QA"])
        self.assertEqual(len(w), 1)
        self.assertIn("''", str(w[0].message))

    def test_whitespace_in_list(self):
        """Whitespace-only entries → dropped with warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _resolve_steps("M", ["  ", "qa"])
        self.assertEqual(result, ["QA"])

    def test_special_characters_in_list(self):
        """Special characters → dropped with warning, no crash."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _resolve_steps("M", ["qa;rm -rf /", "implementer"])
        self.assertEqual(result, ["Implementation"])

    def test_very_long_step_name_in_list(self):
        """10K character string → dropped with warning, no crash."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _resolve_steps("M", ["a" * 10_000, "qa"])
        self.assertEqual(result, ["QA"])

    def test_single_valid_step(self):
        """Single valid alias resolves correctly."""
        result = _resolve_steps("M", ["researcher"])
        self.assertEqual(result, ["Research Agent"])


class TestComputeEstimateStepEdgeCases(unittest.TestCase):
    """compute_estimate handles step name edge cases without crashing."""

    def _estimate(self, steps):
        return compute_estimate({
            "size": "S",
            "files": 1,
            "complexity": "low",
            "steps": steps,
            "review_cycles": 1,
        })

    def test_all_invalid_steps_zero_estimate(self):
        """All unrecognized steps → $0.00 (no steps to compute)."""
        result = self._estimate(["nonexistent1", "nonexistent2"])
        self.assertEqual(result["estimate"]["expected"], 0.0)
        # Metadata should show 0 steps
        self.assertEqual(len([s for s in result["steps"] if s["name"] != "PR Review Loop"]), 0)

    def test_empty_steps_list_uses_defaults(self):
        """Empty list falls back to all PIPELINE_STEPS."""
        result = self._estimate([])
        self.assertGreater(result["estimate"]["expected"], 0.0)

    def test_none_steps_uses_defaults(self):
        """None falls back to all PIPELINE_STEPS."""
        result = compute_estimate({
            "size": "S", "files": 1, "complexity": "low",
        })
        self.assertGreater(result["estimate"]["expected"], 0.0)

    def test_special_chars_in_steps_no_crash(self):
        """Special characters in steps don't crash compute_estimate."""
        result = self._estimate(["<script>", "'; DROP TABLE;", "../../etc"])
        self.assertIn("estimate", result)

    def test_unicode_steps_no_crash(self):
        """Unicode step names don't crash compute_estimate."""
        result = self._estimate(["研究者", "实施者"])
        self.assertIn("estimate", result)


class TestAliasMapConsistency(unittest.TestCase):
    """Validate that alias maps and PIPELINE_STEPS are consistent."""

    def test_all_alias_targets_in_pipeline_steps_or_documented(self):
        """Every canonical name in DEFAULT_AGENT_TO_STEP should either exist
        in PIPELINE_STEPS or be explicitly documented as lacking a budget.

        Aliases pointing to missing PIPELINE_STEPS entries silently produce
        $0.00 — this test catches that drift.
        """
        # Known exceptions: steps that have aliases but no token budget yet
        known_missing = {"Docs Updater", "Frontend Designer"}

        for alias, canonical in DEFAULT_AGENT_TO_STEP.items():
            if canonical in known_missing:
                continue
            self.assertIn(
                canonical,
                heuristics.PIPELINE_STEPS,
                f"Alias {alias!r} → {canonical!r} has no PIPELINE_STEPS entry. "
                f"Either add a token budget in heuristics.py or add to known_missing.",
            )

    def test_all_pipeline_steps_have_at_least_one_alias(self):
        """Every PIPELINE_STEPS key should be reachable via at least one alias
        or by its own canonical name."""
        alias_targets = set(DEFAULT_AGENT_TO_STEP.values())
        unreachable = []
        for step_name in heuristics.PIPELINE_STEPS:
            reachable = (
                step_name in alias_targets
                or step_name.lower() in DEFAULT_AGENT_TO_STEP
            )
            if not reachable:
                unreachable.append(step_name)
        self.assertEqual(
            unreachable,
            [],
            f"PIPELINE_STEPS entries with no alias in DEFAULT_AGENT_TO_STEP: {unreachable}. "
            f"Users can only reach these by exact canonical name.",
        )


class TestDuplicateStepsCostDoubles(unittest.TestCase):
    """Duplicate steps in the override list should increase the estimate."""

    def test_duplicate_step_costs_more_than_single(self):
        """Passing ["qa", "qa"] should produce a higher estimate than ["qa"]."""
        single = compute_estimate({
            "size": "M", "files": 3, "complexity": "medium",
            "steps": ["qa"], "review_cycles": 0,
        })
        double = compute_estimate({
            "size": "M", "files": 3, "complexity": "medium",
            "steps": ["qa", "qa"], "review_cycles": 0,
        })
        self.assertGreater(
            double["estimate"]["expected"],
            single["estimate"]["expected"],
            "Duplicate steps should increase the estimate",
        )


class TestRandomValidStepStrings(unittest.TestCase):
    """Fuzz-style test: random combinations of valid alias strings must always
    resolve to recognized steps and produce non-zero estimates."""

    def test_random_subsets_of_aliases_all_resolve(self):
        """Pick random subsets of valid aliases — all must produce non-zero."""
        import random
        random.seed(42)  # deterministic for CI reproducibility

        # Build list of aliases that map to PIPELINE_STEPS entries
        valid_aliases = [
            alias for alias, canonical in DEFAULT_AGENT_TO_STEP.items()
            if canonical in heuristics.PIPELINE_STEPS
        ]

        for _ in range(20):  # 20 random trials
            k = random.randint(1, len(valid_aliases))
            subset = random.sample(valid_aliases, k)
            result = compute_estimate({
                "size": random.choice(["XS", "S", "M", "L"]),
                "files": random.randint(1, 20),
                "complexity": random.choice(["low", "medium", "high"]),
                "steps": subset,
                "review_cycles": random.randint(0, 4),
            })
            self.assertGreater(
                result["estimate"]["expected"],
                0.0,
                f"Random alias subset {subset} produced $0.00",
            )
            # Verify all steps resolved (no silent drops)
            resolved_names = {s["name"] for s in result["steps"]}
            resolved_names.discard("PR Review Loop")  # auto-added
            expected_canonicals = {
                DEFAULT_AGENT_TO_STEP[a] for a in subset
            }
            self.assertEqual(
                resolved_names,
                expected_canonicals,
                f"Aliases {subset} → expected {expected_canonicals}, got {resolved_names}",
            )

    def test_random_case_mutations_all_resolve(self):
        """Random case mutations of valid aliases must still resolve."""
        import random
        random.seed(99)

        valid_aliases = [
            alias for alias, canonical in DEFAULT_AGENT_TO_STEP.items()
            if canonical in heuristics.PIPELINE_STEPS
        ]

        for alias in valid_aliases:
            # Random case mutation: flip each char's case randomly
            mutated = "".join(
                c.upper() if random.random() > 0.5 else c.lower()
                for c in alias
            )
            canonical, warn = resolve_step_name(mutated)
            expected = DEFAULT_AGENT_TO_STEP[alias]
            self.assertEqual(
                canonical,
                expected,
                f"Case-mutated alias {mutated!r} (from {alias!r}) "
                f"resolved to {canonical!r}, expected {expected!r}",
            )

    def test_aliases_with_random_whitespace_padding(self):
        """Valid aliases with random leading/trailing whitespace still resolve."""
        import random
        random.seed(77)

        valid_aliases = [
            alias for alias, canonical in DEFAULT_AGENT_TO_STEP.items()
            if canonical in heuristics.PIPELINE_STEPS
        ]

        for alias in valid_aliases:
            padding_left = " " * random.randint(0, 5) + "\t" * random.randint(0, 2)
            padding_right = " " * random.randint(0, 5) + "\n" * random.randint(0, 1)
            padded = padding_left + alias + padding_right
            canonical, warn = resolve_step_name(padded)
            expected = DEFAULT_AGENT_TO_STEP[alias]
            self.assertEqual(
                canonical,
                expected,
                f"Padded alias {padded!r} (from {alias!r}) "
                f"resolved to {canonical!r}, expected {expected!r}",
            )


class TestAgentMapOverrideIntegration(unittest.TestCase):
    """Integration test for custom agent-map.json alias resolution."""

    def test_custom_alias_via_agent_map_json(self):
        """A custom alias in agent-map.json resolves to a PIPELINE_STEPS key
        and produces a non-zero estimate."""
        import json
        with tempfile.TemporaryDirectory() as tmp:
            agent_map = {"custom-agent": "QA"}
            (Path(tmp) / "agent-map.json").write_text(json.dumps(agent_map))
            result = compute_estimate(
                {
                    "size": "S", "files": 1, "complexity": "low",
                    "steps": ["custom-agent"], "review_cycles": 0,
                },
                calibration_dir=tmp,
            )
        step_names = [s["name"] for s in result["steps"]]
        self.assertIn("QA", step_names)
        self.assertGreater(result["estimate"]["expected"], 0.0)


if __name__ == "__main__":
    unittest.main()
