"""Drift detection tests for pricing.py and heuristics.py.

These tests parse references/pricing.md and references/heuristics.md at test
time and assert that every value in the Python modules exactly matches the
markdown source. A test failure means the markdown was updated without updating
the corresponding Python module (or vice versa).

If the markdown files are missing, tests fail with FileNotFoundError — no silent
skip.
"""

import re
import sys
import unittest
from pathlib import Path

# Insert src/ onto the path so we can import tokencast submodules directly.
_SRC_DIR = str(Path(__file__).parent.parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import tokencast.pricing as pricing
import tokencast.heuristics as heuristics

_REFERENCES_DIR = Path(__file__).parent.parent / "references"


def _strip_number(s: str) -> str:
    """Remove commas, % suffix, x suffix, and leading $ from a number string."""
    s = s.strip().lstrip("$")
    s = s.rstrip("%").rstrip("x")
    s = s.replace(",", "")
    return s.strip()


class TestPricingDrift(unittest.TestCase):
    """Verify pricing.py matches references/pricing.md."""

    @classmethod
    def setUpClass(cls):
        cls.pricing_text = (_REFERENCES_DIR / "pricing.md").read_text()
        cls.pricing_lines = cls.pricing_text.splitlines()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def test_last_updated(self):
        """pricing.LAST_UPDATED matches last_updated: line in pricing.md."""
        value = self._parse_metadata("last_updated")
        self.assertIsNotNone(value, "Could not find 'last_updated:' in pricing.md")
        self.assertEqual(
            pricing.LAST_UPDATED,
            value,
            f"pricing.LAST_UPDATED={pricing.LAST_UPDATED!r} != markdown {value!r}",
        )

    def test_staleness_warning_days(self):
        """pricing.STALENESS_WARNING_DAYS matches staleness_warning_days: in pricing.md."""
        value = self._parse_metadata("staleness_warning_days")
        self.assertIsNotNone(value, "Could not find 'staleness_warning_days:' in pricing.md")
        self.assertEqual(
            pricing.STALENESS_WARNING_DAYS,
            int(value),
            f"pricing.STALENESS_WARNING_DAYS={pricing.STALENESS_WARNING_DAYS} != markdown {value}",
        )

    # ------------------------------------------------------------------
    # Model prices
    # ------------------------------------------------------------------

    def test_model_prices(self):
        """pricing.MODEL_PRICES matches each model block in pricing.md."""
        models = self._parse_model_prices()
        self.assertTrue(models, "No model price blocks found in pricing.md")
        for model_id, fields in models.items():
            self.assertIn(
                model_id,
                pricing.MODEL_PRICES,
                f"Model {model_id!r} found in markdown but missing from pricing.MODEL_PRICES",
            )
            for field, md_value in fields.items():
                py_value = pricing.MODEL_PRICES[model_id][field]
                self.assertAlmostEqual(
                    py_value,
                    md_value,
                    places=4,
                    msg=f"MODEL_PRICES[{model_id!r}][{field!r}]: Python={py_value} != markdown={md_value}",
                )

    # ------------------------------------------------------------------
    # Step → model mapping
    # ------------------------------------------------------------------

    def test_step_model_map(self):
        """pricing.STEP_MODEL_MAP matches the Pipeline Step → Model Mapping table."""
        short_to_model = {
            "Sonnet": pricing.MODEL_SONNET,
            "Opus":   pricing.MODEL_OPUS,
            "Haiku":  pricing.MODEL_HAIKU,
        }
        rows = self._parse_step_model_table()
        self.assertTrue(rows, "No rows found in Pipeline Step → Model Mapping table")
        for step_name, model_short in rows.items():
            # Strip parentheticals like "(Opus for L-size changes)"
            model_short_clean = re.sub(r"\s*\(.*?\)", "", model_short).strip()
            # Extract just the first word (e.g. "Sonnet" from "Sonnet (Opus …)")
            first_word = model_short_clean.split()[0]
            expected_model = short_to_model.get(first_word)
            self.assertIsNotNone(
                expected_model,
                f"Unknown model shorthand {first_word!r} for step {step_name!r}",
            )
            self.assertIn(
                step_name,
                pricing.STEP_MODEL_MAP,
                f"Step {step_name!r} in markdown but missing from pricing.STEP_MODEL_MAP",
            )
            self.assertEqual(
                pricing.STEP_MODEL_MAP[step_name],
                expected_model,
                f"STEP_MODEL_MAP[{step_name!r}]: Python={pricing.STEP_MODEL_MAP[step_name]!r} != markdown→{expected_model!r}",
            )

    # ------------------------------------------------------------------
    # Cache hit rates
    # ------------------------------------------------------------------

    def test_cache_hit_rates(self):
        """pricing.CACHE_HIT_RATES matches Cache Hit Rate table in pricing.md."""
        rates = self._parse_cache_hit_rates()
        self.assertTrue(rates, "No rows found in Cache Hit Rate table")
        for band, md_rate in rates.items():
            self.assertIn(
                band,
                pricing.CACHE_HIT_RATES,
                f"Band {band!r} in markdown but missing from pricing.CACHE_HIT_RATES",
            )
            self.assertAlmostEqual(
                pricing.CACHE_HIT_RATES[band],
                md_rate,
                places=4,
                msg=f"CACHE_HIT_RATES[{band!r}]: Python={pricing.CACHE_HIT_RATES[band]} != markdown={md_rate}",
            )

    def test_cross_module_band_keys(self):
        """pricing.CACHE_HIT_RATES and heuristics.BAND_MULTIPLIERS have the same band keys."""
        cache_keys = set(pricing.CACHE_HIT_RATES.keys())
        band_keys = set(heuristics.BAND_MULTIPLIERS.keys())
        self.assertEqual(
            cache_keys,
            band_keys,
            f"Band key mismatch: CACHE_HIT_RATES keys={cache_keys} vs BAND_MULTIPLIERS keys={band_keys}",
        )

    # ------------------------------------------------------------------
    # Private parsing helpers
    # ------------------------------------------------------------------

    def _parse_metadata(self, key: str):
        """Parse a top-level 'key: value' line from pricing.md."""
        for line in self.pricing_lines:
            m = re.match(rf"^{re.escape(key)}:\s*(.+)$", line.strip())
            if m:
                return m.group(1).strip()
        return None

    def _parse_model_prices(self):
        """Parse all ### claude-* model blocks, returning {model_id: {field: float}}."""
        result = {}
        current_model = None
        for line in self.pricing_lines:
            m_header = re.match(r"^###\s+(claude-\S+)", line)
            if m_header:
                current_model = m_header.group(1).strip()
                result[current_model] = {}
                continue
            if current_model:
                m_field = re.match(r"^-\s+(input|cache_read|cache_write|output):\s*\$?([\d.]+)", line.strip())
                if m_field:
                    field = m_field.group(1)
                    value = float(m_field.group(2))
                    result[current_model][field] = value
        return result

    def _parse_step_model_table(self):
        """Parse the Pipeline Step → Model Mapping table, returning {step_name: model_short}."""
        result = {}
        in_table = False
        for line in self.pricing_lines:
            if "Pipeline Step" in line and "Model" in line and "|" in line:
                in_table = True
                continue
            if in_table:
                if line.strip().startswith("|---"):
                    continue
                if not line.strip().startswith("|"):
                    break
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2:
                    step_name = parts[0].strip()
                    model_val = parts[1].strip()
                    if step_name:
                        result[step_name] = model_val
        return result

    def _parse_cache_hit_rates(self):
        """Parse Cache Hit Rate table, returning {band_lower: float}."""
        result = {}
        in_table = False
        for line in self.pricing_lines:
            if "Cache Hit Rate" in line and "|" in line and "Band" in line:
                in_table = True
                continue
            if in_table:
                if line.strip().startswith("|---"):
                    continue
                if not line.strip().startswith("|"):
                    break
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2:
                    band = parts[0].strip().lower()
                    rate_str = _strip_number(parts[1])
                    if band and rate_str:
                        result[band] = float(rate_str) / 100.0
        return result


class TestHeuristicsDrift(unittest.TestCase):
    """Verify heuristics.py matches references/heuristics.md."""

    @classmethod
    def setUpClass(cls):
        cls.heuristics_text = (_REFERENCES_DIR / "heuristics.md").read_text()
        cls.heuristics_lines = cls.heuristics_text.splitlines()

    # ------------------------------------------------------------------
    # Activity tokens
    # ------------------------------------------------------------------

    def test_activity_tokens(self):
        """heuristics.ACTIVITY_TOKENS matches Activity Token Estimates table."""
        # Markdown activity → Python key mapping
        name_map = {
            "File read":            "file_read",
            "File write (new)":     "file_write_new",
            "File edit":            "file_edit",
            "Test write":           "test_write",
            "Code review pass":     "code_review_pass",
            "Research/exploration": "research_exploration",
            "Planning step":        "planning_step",
            "Grep/search":          "grep_search",
            "Shell command":        "shell_command",
            "Conversation turn":    "conversation_turn",
        }
        rows = self._parse_activity_tokens_table()
        self.assertTrue(rows, "No rows found in Activity Token Estimates table")
        for md_name, (md_input, md_output) in rows.items():
            py_key = name_map.get(md_name)
            self.assertIsNotNone(py_key, f"No Python key mapping for markdown activity {md_name!r}")
            self.assertIn(
                py_key,
                heuristics.ACTIVITY_TOKENS,
                f"Activity {py_key!r} missing from heuristics.ACTIVITY_TOKENS",
            )
            py_input = heuristics.ACTIVITY_TOKENS[py_key]["input"]
            py_output = heuristics.ACTIVITY_TOKENS[py_key]["output"]
            self.assertEqual(
                py_input,
                md_input,
                f"ACTIVITY_TOKENS[{py_key!r}]['input']: Python={py_input} != markdown={md_input}",
            )
            self.assertEqual(
                py_output,
                md_output,
                f"ACTIVITY_TOKENS[{py_key!r}]['output']: Python={py_output} != markdown={md_output}",
            )

    # ------------------------------------------------------------------
    # Complexity multipliers
    # ------------------------------------------------------------------

    def test_complexity_multipliers(self):
        """heuristics.COMPLEXITY_MULTIPLIERS matches Complexity Multipliers table."""
        rows = self._parse_simple_table("Complexity Multipliers", ["Complexity", "Multiplier"])
        self.assertTrue(rows, "No rows found in Complexity Multipliers table")
        for md_key, md_value in rows.items():
            py_key = md_key.lower()
            self.assertIn(py_key, heuristics.COMPLEXITY_MULTIPLIERS,
                          f"Complexity {py_key!r} missing from heuristics.COMPLEXITY_MULTIPLIERS")
            self.assertAlmostEqual(
                heuristics.COMPLEXITY_MULTIPLIERS[py_key],
                md_value,
                places=4,
                msg=f"COMPLEXITY_MULTIPLIERS[{py_key!r}]: Python={heuristics.COMPLEXITY_MULTIPLIERS[py_key]} != markdown={md_value}",
            )

    # ------------------------------------------------------------------
    # Band multipliers
    # ------------------------------------------------------------------

    def test_band_multipliers(self):
        """heuristics.BAND_MULTIPLIERS matches Confidence Band Multipliers table."""
        rows = self._parse_simple_table("Confidence Band Multipliers", ["Band", "Multiplier"])
        self.assertTrue(rows, "No rows found in Confidence Band Multipliers table")
        for md_key, md_value in rows.items():
            py_key = md_key.lower()
            self.assertIn(py_key, heuristics.BAND_MULTIPLIERS,
                          f"Band {py_key!r} missing from heuristics.BAND_MULTIPLIERS")
            self.assertAlmostEqual(
                heuristics.BAND_MULTIPLIERS[py_key],
                md_value,
                places=4,
                msg=f"BAND_MULTIPLIERS[{py_key!r}]: Python={heuristics.BAND_MULTIPLIERS[py_key]} != markdown={md_value}",
            )

    # ------------------------------------------------------------------
    # PR Review Loop defaults
    # ------------------------------------------------------------------

    def test_pr_review_loop_defaults(self):
        """heuristics.PR_REVIEW_LOOP matches PR Review Loop Defaults table."""
        rows = self._parse_keyed_table("PR Review Loop Defaults")
        self.assertIn("review_cycles_default", rows,
                      "review_cycles_default not found in PR Review Loop Defaults table")
        self.assertIn("review_decay_factor", rows,
                      "review_decay_factor not found in PR Review Loop Defaults table")
        self.assertEqual(
            heuristics.PR_REVIEW_LOOP["review_cycles_default"],
            int(rows["review_cycles_default"]),
            f"PR_REVIEW_LOOP['review_cycles_default']: Python={heuristics.PR_REVIEW_LOOP['review_cycles_default']} != markdown={rows['review_cycles_default']}",
        )
        self.assertAlmostEqual(
            heuristics.PR_REVIEW_LOOP["review_decay_factor"],
            float(rows["review_decay_factor"]),
            places=4,
            msg=f"PR_REVIEW_LOOP['review_decay_factor']: Python={heuristics.PR_REVIEW_LOOP['review_decay_factor']} != markdown={rows['review_decay_factor']}",
        )

    # ------------------------------------------------------------------
    # Parallel accounting
    # ------------------------------------------------------------------

    def test_parallel_accounting(self):
        """heuristics.PARALLEL_ACCOUNTING matches Parallel Agent Accounting table."""
        rows = self._parse_keyed_table("Parallel Agent Accounting")
        self.assertTrue(rows, "No rows found in Parallel Agent Accounting table")
        for key in ("parallel_input_discount", "parallel_cache_rate_reduction", "parallel_cache_rate_floor"):
            self.assertIn(key, rows, f"{key} not found in Parallel Agent Accounting table")
            md_value = float(rows[key])
            py_value = heuristics.PARALLEL_ACCOUNTING[key]
            self.assertAlmostEqual(
                py_value,
                md_value,
                places=4,
                msg=f"PARALLEL_ACCOUNTING[{key!r}]: Python={py_value} != markdown={md_value}",
            )

    # ------------------------------------------------------------------
    # Per-step calibration
    # ------------------------------------------------------------------

    def test_per_step_calibration(self):
        """heuristics.PER_STEP_CALIBRATION matches Per-Step Calibration table."""
        rows = self._parse_keyed_table("Per-Step Calibration")
        self.assertIn("per_step_min_samples", rows,
                      "per_step_min_samples not found in Per-Step Calibration table")
        md_value = int(rows["per_step_min_samples"])
        py_value = heuristics.PER_STEP_CALIBRATION["per_step_min_samples"]
        self.assertEqual(
            py_value,
            md_value,
            f"PER_STEP_CALIBRATION['per_step_min_samples']: Python={py_value} != markdown={md_value}",
        )

    # ------------------------------------------------------------------
    # File size bracket boundaries
    # ------------------------------------------------------------------

    def test_file_size_brackets_boundaries(self):
        """heuristics.FILE_SIZE_BRACKETS boundaries match heuristics.md tunable values."""
        # Parse inline boundary value lines
        small_max = self._parse_inline_value(r"file_size_small_max\s*=\s*(\d+)")
        large_min = self._parse_inline_value(r"file_size_large_min\s*=\s*(\d+)")
        meas_cap  = self._parse_inline_value(r"file_measurement_cap\s*=\s*(\d+)")

        self.assertIsNotNone(small_max, "file_size_small_max not found in heuristics.md")
        self.assertIsNotNone(large_min, "file_size_large_min not found in heuristics.md")
        self.assertIsNotNone(meas_cap,  "file_measurement_cap not found in heuristics.md")

        self.assertEqual(
            heuristics.FILE_SIZE_BRACKETS["small_max_lines"],
            int(small_max),
            f"FILE_SIZE_BRACKETS['small_max_lines']: Python={heuristics.FILE_SIZE_BRACKETS['small_max_lines']} != markdown={small_max}",
        )
        self.assertEqual(
            heuristics.FILE_SIZE_BRACKETS["large_min_lines"],
            int(large_min),
            f"FILE_SIZE_BRACKETS['large_min_lines']: Python={heuristics.FILE_SIZE_BRACKETS['large_min_lines']} != markdown={large_min}",
        )
        self.assertEqual(
            heuristics.FILE_SIZE_BRACKETS["measurement_cap"],
            int(meas_cap),
            f"FILE_SIZE_BRACKETS['measurement_cap']: Python={heuristics.FILE_SIZE_BRACKETS['measurement_cap']} != markdown={meas_cap}",
        )

    # ------------------------------------------------------------------
    # File size bracket token values
    # ------------------------------------------------------------------

    def test_file_size_bracket_token_values(self):
        """heuristics.FILE_SIZE_BRACKETS['brackets'] matches File Size Brackets table."""
        rows = self._parse_file_size_bracket_table()
        self.assertTrue(rows, "No rows found in File Size Brackets table")
        bracket_name_map = {"Small": "small", "Medium": "medium", "Large": "large"}
        for md_bracket, fields in rows.items():
            py_bracket = bracket_name_map.get(md_bracket)
            self.assertIsNotNone(py_bracket, f"Unknown bracket name {md_bracket!r}")
            py_entry = heuristics.FILE_SIZE_BRACKETS["brackets"].get(py_bracket)
            self.assertIsNotNone(py_entry,
                                 f"Bracket {py_bracket!r} missing from FILE_SIZE_BRACKETS['brackets']")
            for field, md_value in fields.items():
                py_value = py_entry[field]
                self.assertEqual(
                    py_value,
                    md_value,
                    f"FILE_SIZE_BRACKETS['brackets'][{py_bracket!r}][{field!r}]: Python={py_value} != markdown={md_value}",
                )

    # ------------------------------------------------------------------
    # Time decay
    # ------------------------------------------------------------------

    def test_time_decay(self):
        """heuristics.TIME_DECAY matches Time-Based Decay table."""
        rows = self._parse_keyed_table("Time-Based Decay")
        self.assertIn("decay_halflife_days", rows,
                      "decay_halflife_days not found in Time-Based Decay table")
        md_value = int(rows["decay_halflife_days"])
        py_value = heuristics.TIME_DECAY["decay_halflife_days"]
        self.assertEqual(
            py_value,
            md_value,
            f"TIME_DECAY['decay_halflife_days']: Python={py_value} != markdown={md_value}",
        )

    # ------------------------------------------------------------------
    # Per-signature calibration
    # ------------------------------------------------------------------

    def test_per_signature_calibration(self):
        """heuristics.PER_SIGNATURE_CALIBRATION matches Per-Signature Calibration table."""
        rows = self._parse_keyed_table("Per-Signature Calibration")
        self.assertIn("per_signature_min_samples", rows,
                      "per_signature_min_samples not found in Per-Signature Calibration table")
        md_value = int(rows["per_signature_min_samples"])
        py_value = heuristics.PER_SIGNATURE_CALIBRATION["per_signature_min_samples"]
        self.assertEqual(
            py_value,
            md_value,
            f"PER_SIGNATURE_CALIBRATION['per_signature_min_samples']: Python={py_value} != markdown={md_value}",
        )

    # ------------------------------------------------------------------
    # Mid-session tracking
    # ------------------------------------------------------------------

    def test_mid_session_tracking(self):
        """heuristics.MID_SESSION_TRACKING matches Mid-Session Cost Tracking table."""
        rows = self._parse_keyed_table("Mid-Session Cost Tracking")
        self.assertTrue(rows, "No rows found in Mid-Session Cost Tracking table")

        checks = {
            "midcheck_warn_threshold":  (float, heuristics.MID_SESSION_TRACKING["midcheck_warn_threshold"]),
            "midcheck_sampling_bytes":  (int,   heuristics.MID_SESSION_TRACKING["midcheck_sampling_bytes"]),
            "midcheck_cooldown_bytes":  (int,   heuristics.MID_SESSION_TRACKING["midcheck_cooldown_bytes"]),
        }
        for key, (cast_fn, py_value) in checks.items():
            self.assertIn(key, rows, f"{key} not found in Mid-Session Cost Tracking table")
            md_value = cast_fn(rows[key])
            if cast_fn is float:
                self.assertAlmostEqual(
                    py_value,
                    md_value,
                    places=4,
                    msg=f"MID_SESSION_TRACKING[{key!r}]: Python={py_value} != markdown={md_value}",
                )
            else:
                self.assertEqual(
                    py_value,
                    md_value,
                    f"MID_SESSION_TRACKING[{key!r}]: Python={py_value} != markdown={md_value}",
                )

    # ------------------------------------------------------------------
    # Private parsing helpers
    # ------------------------------------------------------------------

    def _find_section_lines(self, heading_keyword: str):
        """Return lines starting from the first line whose content contains heading_keyword."""
        found = False
        result = []
        for line in self.heuristics_lines:
            if not found:
                if heading_keyword in line and line.strip().startswith("#"):
                    found = True
                    result.append(line)
            else:
                result.append(line)
        return result

    def _parse_activity_tokens_table(self):
        """Parse the Activity Token Estimates table.

        Returns {activity_name: (input_int, output_int)}.
        input values are the first number token in the cell (before any parenthetical).
        """
        result = {}
        in_table = False
        for line in self.heuristics_lines:
            if "Activity Token Estimates" in line:
                in_table = True
                continue
            if in_table:
                if line.strip().startswith("|---"):
                    continue
                if not line.strip().startswith("|"):
                    if result:
                        break
                    continue
                # Skip header row
                if "Activity" in line and "Input Tokens" in line:
                    continue
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 3:
                    activity = parts[0].strip()
                    # Extract the leading integer (first run of digits, ignoring commas)
                    input_m = re.search(r"([\d,]+)", parts[1])
                    output_m = re.search(r"([\d,]+)", parts[2])
                    if activity and input_m and output_m:
                        input_val = int(input_m.group(1).replace(",", ""))
                        output_val = int(output_m.group(1).replace(",", ""))
                        result[activity] = (input_val, output_val)
        return result

    def _parse_simple_table(self, heading: str, col_names: list):
        """Parse a simple two-column table (key, numeric value) after a heading.

        Returns {key_lower: float}.
        """
        result = {}
        in_table = False
        key_col = col_names[0]
        val_col = col_names[1]
        section_lines = self._find_section_lines(heading)
        for line in section_lines:
            if key_col in line and val_col in line and "|" in line:
                in_table = True
                continue
            if in_table:
                if line.strip().startswith("|---"):
                    continue
                if not line.strip().startswith("|"):
                    if result:
                        break
                    continue
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2:
                    key = parts[0].strip()
                    val_str = _strip_number(parts[1])
                    if key and val_str:
                        result[key] = float(val_str)
        return result

    def _parse_keyed_table(self, heading: str):
        """Parse a Parameter/Value table under heading, returning {parameter: value_str}."""
        result = {}
        in_table = False
        section_lines = self._find_section_lines(heading)
        for line in section_lines:
            if "Parameter" in line and "Value" in line and "|" in line:
                in_table = True
                continue
            if in_table:
                if line.strip().startswith("|---"):
                    continue
                if not line.strip().startswith("|"):
                    if result:
                        break
                    continue
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 2:
                    key = parts[0].strip()
                    val = _strip_number(parts[1])
                    if key and val:
                        result[key] = val
        return result

    def _parse_inline_value(self, pattern: str):
        """Search all lines for a regex pattern, return the first captured group or None."""
        for line in self.heuristics_lines:
            m = re.search(pattern, line)
            if m:
                return m.group(1)
        return None

    def _parse_file_size_bracket_table(self):
        """Parse the File Size Brackets table.

        Returns {bracket_name: {"file_read_input": int, "file_edit_input": int}}.
        """
        result = {}
        in_table = False
        for line in self.heuristics_lines:
            if "File Size Brackets" in line and line.strip().startswith("#"):
                in_table = False  # section heading — wait for table
                continue
            if in_table or ("Bracket" in line and "File Read Input" in line and "|" in line):
                if "Bracket" in line and "File Read Input" in line:
                    in_table = True
                    continue
                if line.strip().startswith("|---"):
                    continue
                if not line.strip().startswith("|"):
                    if result:
                        break
                    continue
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 4:
                    bracket = parts[0].strip()
                    read_m = re.search(r"([\d,]+)", parts[2])
                    edit_m = re.search(r"([\d,]+)", parts[3])
                    if bracket and read_m and edit_m:
                        result[bracket] = {
                            "file_read_input": int(read_m.group(1).replace(",", "")),
                            "file_edit_input": int(edit_m.group(1).replace(",", "")),
                        }
        return result


if __name__ == "__main__":
    unittest.main()
