# Run with: /usr/bin/python3 -m pytest tests/test_telemetry.py
# Do NOT use bare 'python3 -m pytest' -- Homebrew Python 3.14 does not have pytest.
"""Tests for opt-in anonymous telemetry (US-PM.01).

Covers:
- Telemetry OFF by default (no flag, no env var)
- Telemetry ON with config flag
- Telemetry ON with TOKENCAST_TELEMETRY=1 env var
- No PII in collected data (no project names, file paths, cost amounts)
- Timeout behavior — hangs never block the caller
- Graceful failure when endpoint is unreachable
- First-run message shown exactly once
- record_event is a no-op when disabled
- parse_args --telemetry flag propagates to ServerConfig
- PostHog payload shape and routing
- Install ID creation and persistence
"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
_src_root = Path(__file__).resolve().parent.parent / "src"
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

from tokencast import telemetry  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_records(n: int) -> list:
    """Build n minimal history records with expected and actual costs."""
    return [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "expected_cost": 1.0,
            "actual_cost": 1.1,
            "ratio": 1.1,
        }
        for _ in range(n)
    ]


def _make_factors(global_val: float = 0.9, with_sig: bool = False) -> dict:
    """Build a minimal factors dict."""
    d: dict = {"global": global_val}
    if with_sig:
        d["signature_factors"] = {"research+engineer": 0.85}
    return d


# ---------------------------------------------------------------------------
# Unit tests: is_enabled
# ---------------------------------------------------------------------------


class TestIsEnabled(unittest.TestCase):
    def test_disabled_by_default(self):
        """Telemetry is OFF when neither flag nor env var is set."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENCAST_TELEMETRY", None)
            self.assertFalse(telemetry.is_enabled(telemetry_enabled=False))

    def test_enabled_by_flag(self):
        """Telemetry is ON when telemetry_enabled=True is passed."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENCAST_TELEMETRY", None)
            self.assertTrue(telemetry.is_enabled(telemetry_enabled=True))

    def test_enabled_by_env_var(self):
        """Telemetry is ON when TOKENCAST_TELEMETRY=1 is set."""
        with patch.dict(os.environ, {"TOKENCAST_TELEMETRY": "1"}):
            self.assertTrue(telemetry.is_enabled(telemetry_enabled=False))

    def test_env_var_zero_disables(self):
        """TOKENCAST_TELEMETRY=0 does not enable telemetry."""
        with patch.dict(os.environ, {"TOKENCAST_TELEMETRY": "0"}):
            self.assertFalse(telemetry.is_enabled(telemetry_enabled=False))

    def test_env_var_empty_disables(self):
        """TOKENCAST_TELEMETRY= (empty) does not enable telemetry."""
        with patch.dict(os.environ, {"TOKENCAST_TELEMETRY": ""}):
            self.assertFalse(telemetry.is_enabled(telemetry_enabled=False))

    def test_flag_wins_even_when_env_absent(self):
        """Flag=True enables telemetry regardless of env var absence."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENCAST_TELEMETRY", None)
            self.assertTrue(telemetry.is_enabled(telemetry_enabled=True))


# ---------------------------------------------------------------------------
# Unit tests: collect_metrics — no PII
# ---------------------------------------------------------------------------


class TestCollectMetrics(unittest.TestCase):
    def test_returns_required_keys(self):
        records = _make_records(3)
        factors = _make_factors()
        m = telemetry.collect_metrics(records, factors)
        for key in (
            "session_count",
            "mean_accuracy",
            "calibrated_factors",
            "client_name",
            "framework",
            "collected_at",
        ):
            self.assertIn(key, m, f"Missing key: {key}")

    def test_session_count_correct(self):
        records = _make_records(5)
        m = telemetry.collect_metrics(records, {})
        self.assertEqual(m["session_count"], 5)

    def test_session_count_zero_on_empty(self):
        m = telemetry.collect_metrics([], {})
        self.assertEqual(m["session_count"], 0)

    def test_no_project_names(self):
        """No project_name or project_dir field must appear in collected metrics."""
        records = [
            {
                "expected_cost": 1.0,
                "actual_cost": 1.0,
                "project_name": "secret-project",
                "project_dir": "/home/user/secret",
            }
        ]
        m = telemetry.collect_metrics(records, {})
        for key in m:
            self.assertNotIn("project", key.lower(), f"Found project-related key: {key}")

    def test_no_file_paths(self):
        """No file path data must appear in collected metrics."""
        records = [{"actual_cost": 1.0, "file_paths": ["/etc/passwd"]}]
        m = telemetry.collect_metrics(records, {})
        payload_str = json.dumps(m)
        self.assertNotIn("/etc/passwd", payload_str)
        self.assertNotIn("file_paths", payload_str)

    def test_no_cost_amounts(self):
        """No raw cost values must appear in the collected metrics."""
        records = _make_records(2)
        # actual_cost 1.1 must NOT appear verbatim in the payload
        m = telemetry.collect_metrics(records, {})
        payload_str = json.dumps(m)
        # mean_accuracy IS allowed (it's a ratio, not a dollar amount)
        # But actual_cost / expected_cost raw values must not be exposed
        self.assertNotIn("actual_cost", payload_str)
        self.assertNotIn("expected_cost", payload_str)

    def test_client_name_passed_through(self):
        m = telemetry.collect_metrics([], {}, client_name="cursor")
        self.assertEqual(m["client_name"], "cursor")

    def test_client_name_none_when_not_provided(self):
        m = telemetry.collect_metrics([], {})
        self.assertIsNone(m["client_name"])

    def test_framework_defaults_to_mcp(self):
        m = telemetry.collect_metrics([], {})
        self.assertEqual(m["framework"], "mcp")

    def test_framework_custom(self):
        m = telemetry.collect_metrics([], {}, framework="cursor-extension")
        self.assertEqual(m["framework"], "cursor-extension")

    def test_collected_at_is_iso8601(self):
        from datetime import datetime
        m = telemetry.collect_metrics([], {})
        # Should not raise
        ts_str = m["collected_at"]
        # Strip trailing Z and parse
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        datetime.fromisoformat(ts_str)

    def test_mean_accuracy_computed(self):
        records = [
            {"expected_cost": 1.0, "actual_cost": 2.0},  # ratio 2.0
            {"expected_cost": 1.0, "actual_cost": 1.0},  # ratio 1.0
        ]
        m = telemetry.collect_metrics(records, {})
        self.assertAlmostEqual(m["mean_accuracy"], 1.5, places=5)

    def test_mean_accuracy_none_on_empty(self):
        m = telemetry.collect_metrics([], {})
        self.assertIsNone(m["mean_accuracy"])

    def test_calibrated_factors_counts_non_identity(self):
        # global=0.9 (non-identity) → count 1
        m = telemetry.collect_metrics([], {"global": 0.9})
        self.assertEqual(m["calibrated_factors"], 1)

    def test_calibrated_factors_ignores_identity(self):
        # global=1.0 is identity → count 0
        m = telemetry.collect_metrics([], {"global": 1.0})
        self.assertEqual(m["calibrated_factors"], 0)

    def test_calibrated_factors_counts_signature_factors(self):
        factors = {
            "global": 0.9,
            "signature_factors": {"sig1": 0.8, "sig2": 1.0},
        }
        # global(0.9) + sig1(0.8) = 2 (sig2 is identity 1.0)
        m = telemetry.collect_metrics([], factors)
        self.assertEqual(m["calibrated_factors"], 2)

    def test_calibrated_factors_zero_on_empty(self):
        m = telemetry.collect_metrics([], {})
        self.assertEqual(m["calibrated_factors"], 0)


# ---------------------------------------------------------------------------
# Unit tests: _compute_mean_accuracy
# ---------------------------------------------------------------------------


class TestComputeMeanAccuracy(unittest.TestCase):
    def test_uses_ratio_field_when_present(self):
        records = [{"ratio": 1.5}, {"ratio": 0.5}]
        result = telemetry._compute_mean_accuracy(records)
        self.assertAlmostEqual(result, 1.0, places=5)

    def test_falls_back_to_cost_fields(self):
        records = [{"expected_cost": 2.0, "actual_cost": 1.0}]
        result = telemetry._compute_mean_accuracy(records)
        self.assertAlmostEqual(result, 0.5, places=5)

    def test_uses_last_10_records(self):
        """Only the last 10 of 15 records are used."""
        # First 5: ratio 10.0, last 10: ratio 1.0
        records = [{"ratio": 10.0}] * 5 + [{"ratio": 1.0}] * 10
        result = telemetry._compute_mean_accuracy(records)
        self.assertAlmostEqual(result, 1.0, places=5)

    def test_returns_none_on_empty(self):
        self.assertIsNone(telemetry._compute_mean_accuracy([]))

    def test_skips_malformed_records(self):
        records = [{"ratio": "bad"}, {"ratio": 2.0}]
        result = telemetry._compute_mean_accuracy(records)
        self.assertAlmostEqual(result, 2.0, places=5)


# ---------------------------------------------------------------------------
# Unit tests: send_metrics — fire-and-forget, always sends to PostHog
# ---------------------------------------------------------------------------


class TestSendMetrics(unittest.TestCase):
    def test_sends_to_explicit_url(self):
        """send_metrics fires a background thread and sends to _POSTHOG_ENDPOINT."""
        sent_events = []
        send_done = threading.Event()

        fake_install_id = str(uuid.uuid4())

        def fake_send(url, payload, timeout=telemetry.TELEMETRY_TIMEOUT_SECONDS):
            sent_events.append({"url": url, "payload": payload})
            send_done.set()

        metrics = {
            "event_type": "estimate_cost",
            "install_id": fake_install_id,
            "collected_at": "2026-01-01T00:00:00+00:00",
        }

        with patch.object(telemetry, "_send_payload", side_effect=fake_send):
            telemetry.send_metrics(metrics)
            send_done.wait(timeout=2.0)

        self.assertEqual(len(sent_events), 1)
        self.assertEqual(sent_events[0]["url"], telemetry._POSTHOG_ENDPOINT)

    def test_graceful_failure_unreachable_endpoint(self):
        """send_metrics swallows URLError silently."""
        import urllib.error
        done = threading.Event()

        def failing_send(url, payload, timeout=telemetry.TELEMETRY_TIMEOUT_SECONDS):
            done.set()
            raise urllib.error.URLError("connection refused")

        fake_install_id = str(uuid.uuid4())
        metrics = {
            "event_type": "estimate_cost",
            "install_id": fake_install_id,
            "collected_at": "2026-01-01T00:00:00+00:00",
        }

        with patch.object(telemetry, "_send_payload", side_effect=failing_send):
            # Must not raise
            telemetry.send_metrics(metrics)
            done.wait(timeout=3.0)
        # Reaching here without an unhandled exception means the test passes.


# ---------------------------------------------------------------------------
# Unit tests: timeout — slow endpoint does not block
# ---------------------------------------------------------------------------


class TestTimeoutBehavior(unittest.TestCase):
    def test_caller_not_blocked_by_slow_endpoint(self):
        """Fire-and-forget: caller returns immediately even if send takes time."""
        slow_send_started = threading.Event()

        def slow_send(url, payload, timeout=2.0):
            slow_send_started.set()
            time.sleep(5)  # Simulate a very slow endpoint

        with patch.object(telemetry, "_send_payload", side_effect=slow_send):
            start = time.time()
            telemetry.send_metrics({"x": 1})
            elapsed = time.time() - start

        # send_metrics should return almost immediately (< 0.5s)
        self.assertLess(elapsed, 0.5)


# ---------------------------------------------------------------------------
# Unit tests: record_event — disabled path is a true no-op
# ---------------------------------------------------------------------------


class TestRecordEvent(unittest.TestCase):
    def test_noop_when_disabled(self):
        """record_event does nothing when telemetry is disabled."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENCAST_TELEMETRY", None)
            sent = []

            def fake_send(metrics):
                sent.append(metrics)

            with patch.object(telemetry, "send_metrics", side_effect=fake_send):
                telemetry.record_event(
                    "estimate_cost",
                    telemetry_enabled=False,
                )
                time.sleep(0.05)
                self.assertEqual(sent, [])

    def test_sends_when_enabled_by_flag(self):
        """record_event triggers send_metrics when telemetry_enabled=True."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENCAST_TELEMETRY", None)
            sent = []
            done = threading.Event()

            def fake_send(metrics, **kwargs):
                sent.append(metrics)
                done.set()

            with patch.object(telemetry, "send_metrics", side_effect=fake_send):
                with tempfile.TemporaryDirectory() as tmp:
                    telemetry.record_event(
                        "estimate_cost",
                        telemetry_enabled=True,
                        calibration_dir=tmp,
                    )
                    # record_event calls send_metrics synchronously inside a
                    # try/except (not on its own thread) — no wait needed.
                    time.sleep(0.05)

        self.assertTrue(len(sent) >= 1)
        m = sent[0]
        self.assertIn("event_type", m)
        self.assertEqual(m["event_type"], "estimate_cost")

    def test_sends_when_enabled_by_env_var(self):
        """record_event triggers send_metrics when TOKENCAST_TELEMETRY=1."""
        with patch.dict(os.environ, {"TOKENCAST_TELEMETRY": "1"}):
            sent = []

            def fake_send(metrics):
                sent.append(metrics)

            with patch.object(telemetry, "send_metrics", side_effect=fake_send):
                with tempfile.TemporaryDirectory() as tmp:
                    telemetry.record_event(
                        "report_session",
                        telemetry_enabled=False,
                        calibration_dir=tmp,
                    )
                    time.sleep(0.05)

        self.assertTrue(len(sent) >= 1)

    def test_event_type_included_in_payload(self):
        """event_type field is added to the metrics dict before send."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENCAST_TELEMETRY", None)
            captured = []

            def fake_send(metrics):
                captured.append(dict(metrics))

            with patch.object(telemetry, "send_metrics", side_effect=fake_send):
                with tempfile.TemporaryDirectory() as tmp:
                    telemetry.record_event(
                        "report_session",
                        telemetry_enabled=True,
                        calibration_dir=tmp,
                    )
                    time.sleep(0.05)

        self.assertTrue(len(captured) >= 1)
        self.assertEqual(captured[0].get("event_type"), "report_session")

    def test_never_raises_on_bad_calibration_dir(self):
        """record_event is fail-silent even with a nonexistent calibration dir."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENCAST_TELEMETRY", None)
            # Prevent real HTTP while ensuring the function runs without raising
            with patch.object(telemetry, "_send_payload", side_effect=lambda url, p: None):
                # Should not raise
                telemetry.record_event(
                    "estimate_cost",
                    telemetry_enabled=True,
                    calibration_dir="/nonexistent/path/that/does/not/exist",
                )

    def test_client_name_passed_through(self):
        """client_name is forwarded to collect_metrics."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENCAST_TELEMETRY", None)
            captured_metrics = []

            def fake_send(metrics):
                captured_metrics.append(dict(metrics))

            with patch.object(telemetry, "send_metrics", side_effect=fake_send):
                with tempfile.TemporaryDirectory() as tmp:
                    telemetry.record_event(
                        "estimate_cost",
                        telemetry_enabled=True,
                        calibration_dir=tmp,
                        client_name="cursor",
                    )
                    time.sleep(0.05)

        self.assertTrue(len(captured_metrics) >= 1)
        self.assertEqual(captured_metrics[0].get("client_name"), "cursor")


# ---------------------------------------------------------------------------
# Unit tests: first-run message
# ---------------------------------------------------------------------------


class TestFirstRunMessage(unittest.TestCase):
    def setUp(self):
        # Reset module-level Event before each test
        telemetry._first_run_message_shown.clear()

    def tearDown(self):
        # Clean up Event after each test
        telemetry._first_run_message_shown.clear()

    def test_message_shown_on_first_call(self):
        import io
        fake_stderr = io.StringIO()
        with patch("sys.stderr", fake_stderr):
            telemetry._show_first_run_message_once()
        output = fake_stderr.getvalue()
        self.assertIn("anonymous", output.lower())
        self.assertIn("session count", output.lower())
        self.assertIn("opt out", output.lower())
        self.assertIn("posthog", output.lower())

    def test_message_shown_only_once(self):
        import io
        fake_stderr = io.StringIO()
        with patch("sys.stderr", fake_stderr):
            telemetry._show_first_run_message_once()
            telemetry._show_first_run_message_once()
            telemetry._show_first_run_message_once()
        output = fake_stderr.getvalue()
        # Message appears exactly once
        self.assertEqual(output.count("Anonymous"), 1)

    def test_flag_set_after_first_show(self):
        import io
        fake_stderr = io.StringIO()
        with patch("sys.stderr", fake_stderr):
            telemetry._show_first_run_message_once()
        self.assertTrue(telemetry._first_run_message_shown.is_set())


# ---------------------------------------------------------------------------
# Integration: --telemetry CLI flag in server parse_args
# ---------------------------------------------------------------------------


class TestServerParseArgsTelemetry(unittest.TestCase):
    def test_telemetry_false_by_default(self):
        """--telemetry flag is absent → telemetry_enabled=False."""
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp not available")

        from tokencast_mcp.server import parse_args

        args = parse_args([])
        self.assertFalse(args.telemetry)

    def test_telemetry_true_with_flag(self):
        """--telemetry flag present → telemetry_enabled=True."""
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp not available")

        from tokencast_mcp.server import parse_args

        args = parse_args(["--telemetry"])
        self.assertTrue(args.telemetry)

    def test_telemetry_propagates_to_server_config(self):
        """parse_args --telemetry propagates into ServerConfig.telemetry_enabled."""
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp not available")

        from tokencast_mcp.config import ServerConfig
        from tokencast_mcp.server import parse_args

        args = parse_args(["--telemetry"])
        config = ServerConfig.from_args(
            calibration_dir=None,
            project_dir=None,
            telemetry_enabled=args.telemetry,
        )
        self.assertTrue(config.telemetry_enabled)

    def test_no_telemetry_flag_gives_false_config(self):
        """Without --telemetry, ServerConfig.telemetry_enabled is False."""
        try:
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp not available")

        from tokencast_mcp.config import ServerConfig
        from tokencast_mcp.server import parse_args

        args = parse_args([])
        config = ServerConfig.from_args(
            calibration_dir=None,
            project_dir=None,
            telemetry_enabled=args.telemetry,
        )
        self.assertFalse(config.telemetry_enabled)


# ---------------------------------------------------------------------------
# Integration: server call_tool fires telemetry for the right tool names
# ---------------------------------------------------------------------------


class TestServerTelemetryIntegration(unittest.TestCase):
    def test_server_call_tool_fires_telemetry_for_estimate_cost(self):
        """build_server's call_tool dispatcher calls record_event for estimate_cost."""
        try:
            import asyncio
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp not available")

        from tokencast_mcp.config import ServerConfig
        from tokencast_mcp.server import build_server

        recorded = []

        def fake_record_event(event_type, **kwargs):
            recorded.append(event_type)

        with patch.object(telemetry, "record_event", side_effect=fake_record_event):
            with tempfile.TemporaryDirectory() as tmp:
                config = ServerConfig.from_args(
                    calibration_dir=tmp,
                    project_dir=None,
                    telemetry_enabled=True,
                )
                server = build_server(config)

                async def _call_tool():
                    from mcp.types import CallToolRequest, CallToolRequestParams
                    handler = server.request_handlers[CallToolRequest]
                    result = await handler(
                        CallToolRequest(
                            method="tools/call",
                            params=CallToolRequestParams(
                                name="estimate_cost",
                                arguments={"size": "XS", "files": 1, "complexity": "low"},
                            ),
                        )
                    )
                    return result

                asyncio.run(_call_tool())

        self.assertIn("estimate_cost", recorded)

    def test_telemetry_not_triggered_for_get_cost_history(self):
        """record_event is NOT called for get_cost_history (not a calibration tool)."""
        try:
            import asyncio
            import mcp  # noqa: F401
        except ImportError:
            self.skipTest("mcp not available")

        from tokencast_mcp.config import ServerConfig

        recorded = []

        def fake_record_event(event_type, **kwargs):
            recorded.append(event_type)

        with patch.object(telemetry, "record_event", side_effect=fake_record_event):
            with tempfile.TemporaryDirectory() as tmp:
                config = ServerConfig.from_args(
                    calibration_dir=tmp,
                    project_dir=None,
                    telemetry_enabled=True,
                )
                from tokencast_mcp.tools.get_cost_history import handle_get_cost_history

                async def _run():
                    return await handle_get_cost_history({}, config)

                import asyncio
                asyncio.run(_run())

        # get_cost_history should NOT have triggered telemetry
        self.assertNotIn("get_cost_history", recorded)


# ---------------------------------------------------------------------------
# Unit tests: _count_calibrated_factors edge cases
# ---------------------------------------------------------------------------


class TestCountCalibratedFactors(unittest.TestCase):
    def test_empty_dict(self):
        self.assertEqual(telemetry._count_calibrated_factors({}), 0)

    def test_non_dict_returns_zero(self):
        self.assertEqual(telemetry._count_calibrated_factors(None), 0)  # type: ignore
        self.assertEqual(telemetry._count_calibrated_factors("bad"), 0)  # type: ignore

    def test_multiple_non_identity_factors(self):
        factors = {"global": 0.9, "M": 1.1, "S": 1.0}
        # global(0.9) + M(1.1) = 2; S(1.0) is identity
        self.assertEqual(telemetry._count_calibrated_factors(factors), 2)

    def test_signature_factors_with_identity_mixed(self):
        factors = {
            "global": 1.0,
            "signature_factors": {"a": 0.8, "b": 1.0, "c": 0.95},
        }
        # global=identity; sig a(0.8) + c(0.95) = 2
        self.assertEqual(telemetry._count_calibrated_factors(factors), 2)


# ---------------------------------------------------------------------------
# Unit tests: _get_tokencast_version
# ---------------------------------------------------------------------------


class TestGetTokencastVersion(unittest.TestCase):
    def test_returns_version_string(self):
        """_get_tokencast_version returns a non-empty string."""
        result = telemetry._get_tokencast_version()
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_returns_unknown_on_import_failure(self):
        """_get_tokencast_version returns 'unknown' when tokencast cannot be imported."""
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "tokencast":
                raise ImportError("mocked import failure")
            # Use the real __import__ for everything else
            import builtins
            return builtins.__import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = telemetry._get_tokencast_version()

        self.assertEqual(result, "unknown")


# ---------------------------------------------------------------------------
# Unit tests: _get_or_create_install_id
# ---------------------------------------------------------------------------


class TestGetOrCreateInstallId(unittest.TestCase):
    def setUp(self):
        # Reset the module-level cache before each test
        telemetry._install_id_cache = None

    def tearDown(self):
        # Reset the module-level cache after each test
        telemetry._install_id_cache = None

    def test_creates_uuid4_file_on_first_call(self):
        """First call creates ~/.tokencast/install_id with a valid UUID4."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "install_id"
            with patch.object(telemetry, "_INSTALL_ID_PATH", fake_path):
                result = telemetry._get_or_create_install_id()

            self.assertTrue(fake_path.exists())
            stored = fake_path.read_text().strip()
            # Must be a valid UUID4
            parsed = uuid.UUID(stored, version=4)
            self.assertEqual(str(parsed), stored)
            self.assertEqual(result, stored)

    def test_returns_same_id_on_subsequent_calls(self):
        """Two calls (with cache reset between) return the same install ID."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "install_id"
            with patch.object(telemetry, "_INSTALL_ID_PATH", fake_path):
                first = telemetry._get_or_create_install_id()
                # Reset cache to force re-read from disk
                telemetry._install_id_cache = None
                second = telemetry._get_or_create_install_id()

            self.assertEqual(first, second)

    def test_reads_existing_valid_file(self):
        """An existing valid UUID4 file is returned as-is."""
        known_id = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "install_id"
            fake_path.write_text(known_id + "\n")
            with patch.object(telemetry, "_INSTALL_ID_PATH", fake_path):
                result = telemetry._get_or_create_install_id()

        self.assertEqual(result, known_id)

    def test_regenerates_empty_file(self):
        """An empty install_id file triggers generation of a new UUID4."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "install_id"
            fake_path.write_text("")
            with patch.object(telemetry, "_INSTALL_ID_PATH", fake_path):
                result = telemetry._get_or_create_install_id()

            # Must be a valid UUID4
            parsed = uuid.UUID(result, version=4)
            self.assertEqual(str(parsed), result)

    def test_regenerates_garbage_file(self):
        """A corrupt install_id file triggers generation of a new UUID4."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "install_id"
            fake_path.write_text("not-a-uuid")
            with patch.object(telemetry, "_INSTALL_ID_PATH", fake_path):
                result = telemetry._get_or_create_install_id()

            # Must be a valid UUID4
            parsed = uuid.UUID(result, version=4)
            self.assertEqual(str(parsed), result)

    def test_atomic_write_race_condition(self):
        """Concurrent calls from 5 threads all return a valid UUID4 without errors."""
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "install_id"
            results = []
            errors = []

            def call_fn():
                try:
                    # Each thread resets cache so they race on file creation
                    telemetry._install_id_cache = None
                    val = telemetry._get_or_create_install_id()
                    results.append(val)
                except Exception as exc:
                    errors.append(exc)

            with patch.object(telemetry, "_INSTALL_ID_PATH", fake_path):
                threads = [threading.Thread(target=call_fn) for _ in range(5)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=5.0)

        self.assertEqual(len(errors), 0, f"Unexpected errors: {errors}")
        self.assertEqual(len(results), 5)
        for r in results:
            parsed = uuid.UUID(r, version=4)
            self.assertEqual(str(parsed), r)


# ---------------------------------------------------------------------------
# Unit tests: PostHog payload shape
# ---------------------------------------------------------------------------


class TestPostHogPayload(unittest.TestCase):
    def _capture_payload(self, metrics: dict) -> dict:
        """Call send_metrics with a fake _send_payload and return captured data."""
        captured = {}
        done = threading.Event()

        def fake_send(url, payload, timeout=telemetry.TELEMETRY_TIMEOUT_SECONDS):
            captured.update({"url": url, "payload": payload})
            done.set()

        with patch.object(telemetry, "_send_payload", side_effect=fake_send):
            telemetry.send_metrics(metrics)
            done.wait(timeout=2.0)

        return captured

    def _make_metrics(self, event_type: str = "report_session") -> dict:
        """Build a minimal metrics dict as record_event would produce it."""
        return {
            "event_type": event_type,
            "install_id": str(uuid.uuid4()),
            "collected_at": "2026-01-01T12:00:00+00:00",
            "session_count": 5,
            "mean_accuracy": 1.05,
            "calibrated_factors": 2,
            "client_name": "claude-code",
            "framework": "mcp",
        }

    def test_event_field_is_tool_called(self):
        """PostHog payload top-level 'event' key is 'tool_called'."""
        metrics = self._make_metrics("report_session")
        captured = self._capture_payload(metrics)
        self.assertEqual(captured["payload"]["event"], "tool_called")

    def test_tool_name_in_properties(self):
        """PostHog properties contain tool_name equal to the event_type value."""
        metrics = self._make_metrics("report_session")
        captured = self._capture_payload(metrics)
        props = captured["payload"]["properties"]
        self.assertEqual(props["tool_name"], "report_session")

    def test_event_type_not_in_properties(self):
        """Raw 'event_type' key from metrics dict is not forwarded to properties."""
        metrics = self._make_metrics("report_session")
        captured = self._capture_payload(metrics)
        props = captured["payload"]["properties"]
        self.assertNotIn("event_type", props)

    def test_timestamp_in_payload(self):
        """PostHog payload top-level 'timestamp' equals collected_at value."""
        ts = "2026-01-01T12:00:00+00:00"
        metrics = self._make_metrics()
        metrics["collected_at"] = ts
        captured = self._capture_payload(metrics)
        self.assertEqual(captured["payload"]["timestamp"], ts)

    def test_distinct_id_is_install_id(self):
        """PostHog payload top-level 'distinct_id' equals install_id value."""
        iid = str(uuid.uuid4())
        metrics = self._make_metrics()
        metrics["install_id"] = iid
        captured = self._capture_payload(metrics)
        self.assertEqual(captured["payload"]["distinct_id"], iid)

    def test_install_id_not_in_properties(self):
        """Raw 'install_id' key is not forwarded into PostHog properties."""
        metrics = self._make_metrics()
        captured = self._capture_payload(metrics)
        props = captured["payload"]["properties"]
        self.assertNotIn("install_id", props)

    def test_api_key_present(self):
        """PostHog payload contains the tokencast API key."""
        metrics = self._make_metrics()
        captured = self._capture_payload(metrics)
        self.assertEqual(captured["payload"]["api_key"], telemetry._POSTHOG_API_KEY)

    def test_sends_to_posthog_endpoint(self):
        """send_metrics always POSTs to _POSTHOG_ENDPOINT."""
        metrics = self._make_metrics()
        captured = self._capture_payload(metrics)
        self.assertEqual(captured["url"], telemetry._POSTHOG_ENDPOINT)

    def test_env_url_var_is_ignored(self):
        """TOKENCAST_TELEMETRY_URL env var is ignored; PostHog endpoint is always used."""
        metrics = self._make_metrics()
        captured = {}
        done = threading.Event()

        def fake_send(url, payload, timeout=telemetry.TELEMETRY_TIMEOUT_SECONDS):
            captured.update({"url": url, "payload": payload})
            done.set()

        with patch.dict(os.environ, {"TOKENCAST_TELEMETRY_URL": "https://decoy.example.com/t"}):
            with patch.object(telemetry, "_send_payload", side_effect=fake_send):
                telemetry.send_metrics(metrics)
                done.wait(timeout=2.0)

        self.assertEqual(captured["url"], telemetry._POSTHOG_ENDPOINT)
        self.assertNotEqual(captured["url"], "https://decoy.example.com/t")


if __name__ == "__main__":
    unittest.main()
