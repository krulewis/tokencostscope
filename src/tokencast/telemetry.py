"""Opt-in anonymous telemetry for tokencast.

OFF by default. Requires explicit opt-in via the ``--telemetry`` CLI flag or
``TOKENCAST_TELEMETRY=1`` environment variable.

Collected metrics (NO PII, NO project names, NO file paths, NO cost amounts):
  - session_count     — total number of calibration history records
  - mean_accuracy     — mean actual/expected ratio over recent history
  - calibrated_factors — count of active calibration factor entries
  - client_name       — MCP client identifier string (from MCP init, if set)
  - framework         — "mcp" (always, for the MCP server path)

Fire-and-forget: telemetry is sent on a background thread so it never blocks
the calling code. Failures are silently discarded.

The endpoint URL is configured via ``TOKENCAST_TELEMETRY_URL``. If the env var
is unset or empty, metrics are collected but never transmitted (safe default).
"""

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# First-run message — printed to stderr once when telemetry is enabled
# ---------------------------------------------------------------------------

FIRST_RUN_MESSAGE = """\
[tokencast] Anonymous usage telemetry is enabled.
Collected: session count, mean accuracy ratio, number of calibrated factors,
           client name, framework identifier.
NOT collected: project names, file paths, cost amounts, or any personal data.
To opt out: remove the --telemetry flag or unset TOKENCAST_TELEMETRY=1.
"""

# Module-level event so the first-run message is only printed once per process.
# threading.Event.set() is atomic — safe to call from multiple threads without
# an explicit lock.
_first_run_message_shown = threading.Event()


def _show_first_run_message_once() -> None:
    """Print the first-run consent notice to stderr exactly once per process."""
    if not _first_run_message_shown.is_set():
        _first_run_message_shown.set()
        import sys
        print(FIRST_RUN_MESSAGE, file=sys.stderr, end="")


# ---------------------------------------------------------------------------
# Telemetry enabled check
# ---------------------------------------------------------------------------


def is_enabled(telemetry_enabled: bool = False) -> bool:
    """Return True when telemetry is explicitly opted in.

    Checks (in order):
    1. The ``telemetry_enabled`` argument (from ``ServerConfig``).
    2. The ``TOKENCAST_TELEMETRY`` environment variable (``"1"`` → enabled).

    Args:
        telemetry_enabled: Value from ServerConfig (set via ``--telemetry``
            flag at server startup).

    Returns:
        True if telemetry is opted in, False otherwise.
    """
    if telemetry_enabled:
        return True
    return os.environ.get("TOKENCAST_TELEMETRY", "").strip() == "1"


# ---------------------------------------------------------------------------
# Metric collection helpers
# ---------------------------------------------------------------------------


def _count_calibrated_factors(factors: dict) -> int:
    """Count the number of active (non-identity) factor entries in factors dict.

    Counts top-level scalar float/int entries that are not exactly 1.0, as
    well as entries under ``signature_factors``.

    Args:
        factors: The loaded factors.json content dict.

    Returns:
        Integer count of calibrated factor entries.
    """
    if not isinstance(factors, dict):
        return 0

    count = 0
    skip_keys = {"signature_factors"}

    for key, value in factors.items():
        if key in skip_keys:
            continue
        if isinstance(value, (int, float)) and float(value) != 1.0:
            count += 1

    # Count per-signature factors
    sig_factors = factors.get("signature_factors", {})
    if isinstance(sig_factors, dict):
        for value in sig_factors.values():
            if isinstance(value, (int, float)) and float(value) != 1.0:
                count += 1

    return count


def _compute_mean_accuracy(records: list) -> Optional[float]:
    """Compute mean actual/expected ratio over recent history records.

    Uses up to the last 10 records. Returns None when no valid records exist.

    Args:
        records: List of history record dicts (from history.jsonl).

    Returns:
        Mean ratio as a float, or None if not computable.
    """
    if not records:
        return None

    recent = records[-10:] if len(records) > 10 else records
    ratios = []
    for r in recent:
        ratio = r.get("ratio")
        if ratio is not None:
            try:
                ratios.append(float(ratio))
                continue
            except (TypeError, ValueError):
                pass
        actual = r.get("actual_cost")
        expected = r.get("expected_cost")
        if actual is not None and expected is not None:
            try:
                denom = max(float(expected), 0.001)
                ratios.append(float(actual) / denom)
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def collect_metrics(
    records: list,
    factors: dict,
    client_name: Optional[str] = None,
    framework: str = "mcp",
) -> dict:
    """Collect anonymous usage metrics.

    Never includes PII, project names, file paths, or cost amounts.

    Args:
        records: History records list (from calibration_store.read_history).
        factors: Factors dict (from calibration_store.read_factors).
        client_name: MCP client identifier (e.g. "claude-code", "cursor").
            May be None when not available.
        framework: Source framework string. Defaults to "mcp".

    Returns:
        Dict with keys: ``session_count``, ``mean_accuracy``,
        ``calibrated_factors``, ``client_name``, ``framework``,
        ``collected_at`` (ISO 8601 UTC).
    """
    return {
        "session_count": len(records) if records else 0,
        "mean_accuracy": _compute_mean_accuracy(records),
        "calibrated_factors": _count_calibrated_factors(factors),
        "client_name": client_name,
        "framework": framework,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# HTTP sender (background thread, fire-and-forget)
# ---------------------------------------------------------------------------

TELEMETRY_TIMEOUT_SECONDS = 2.0


def _send_payload(url: str, payload: dict, timeout: float = TELEMETRY_TIMEOUT_SECONDS) -> None:
    """POST a JSON payload to *url* with a *timeout*-second deadline.

    Called exclusively on a background daemon thread. All exceptions are
    swallowed so that telemetry failures never surface to the caller.

    Args:
        url: Endpoint URL to POST to.
        payload: Dict to JSON-encode and send.
        timeout: HTTP request timeout in seconds.
    """
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "tokencast-telemetry/1",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout):
            pass  # response body discarded
    except Exception:
        # Fail silently — telemetry must never affect server operation
        pass


def send_metrics(
    metrics: dict,
    endpoint_url: Optional[str] = None,
) -> None:
    """Send *metrics* to *endpoint_url* in a background daemon thread.

    Does nothing (no-op) when *endpoint_url* is None or empty — metrics
    are collected but not transmitted until an endpoint is configured.

    Args:
        metrics: Dict produced by :func:`collect_metrics`.
        endpoint_url: Target URL. When None/empty, the call is a no-op.
    """
    url = endpoint_url or os.environ.get("TOKENCAST_TELEMETRY_URL", "").strip()
    if not url:
        return  # No endpoint configured — collect but don't send

    t = threading.Thread(target=_send_payload, args=(url, metrics), daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# High-level record-and-send helper
# ---------------------------------------------------------------------------


def record_event(
    event_type: str,
    *,
    telemetry_enabled: bool = False,
    calibration_dir: Optional[str] = None,
    client_name: Optional[str] = None,
    framework: str = "mcp",
    endpoint_url: Optional[str] = None,
) -> None:
    """Collect metrics and fire-and-forget send them if telemetry is enabled.

    This is the primary call site for MCP tool handlers. It:
    1. Checks opt-in state (returns immediately if not opted in)
    2. Prints the first-run message on first call
    3. Loads calibration data (history + factors) from disk
    4. Collects anonymous metrics
    5. Sends to the configured endpoint in a background thread

    All failures are silently discarded — telemetry must never raise.

    Args:
        event_type: Logical event name (e.g. "estimate_cost", "report_session").
        telemetry_enabled: Whether telemetry was enabled via CLI flag / config.
        calibration_dir: Path to calibration directory. When None, defaults to
            ``~/.tokencast/calibration``.
        client_name: MCP client identifier string, if available.
        framework: Framework / source identifier. Defaults to "mcp".
        endpoint_url: Override for telemetry endpoint URL. If None, reads from
            ``TOKENCAST_TELEMETRY_URL`` env var.
    """
    try:
        if not is_enabled(telemetry_enabled):
            return

        _show_first_run_message_once()

        # Resolve calibration paths
        import pathlib

        if calibration_dir is not None:
            cal_path = pathlib.Path(calibration_dir)
        else:
            cal_path = pathlib.Path.home() / ".tokencast" / "calibration"

        history_path = str(cal_path / "history.jsonl")
        factors_path = str(cal_path / "factors.json")

        # Load calibration data — gracefully handle missing files
        records: list = []
        factors: dict = {}
        try:
            import importlib.util
            scripts_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts"
            cs_path = scripts_dir / "calibration_store.py"
            spec = importlib.util.spec_from_file_location("calibration_store", cs_path)
            if spec is not None and spec.loader is not None:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                records = mod.read_history(history_path)
                factors = mod.read_factors(factors_path)
        except Exception:
            pass  # Degrade gracefully — metrics will show zeros

        metrics = collect_metrics(
            records=records,
            factors=factors,
            client_name=client_name,
            framework=framework,
        )
        metrics["event_type"] = event_type

        send_metrics(metrics, endpoint_url=endpoint_url)

    except Exception:
        # Unconditional safety net — telemetry must never raise or crash
        pass
