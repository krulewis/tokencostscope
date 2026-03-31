"""Opt-out anonymous telemetry for tokencast.

ON by default. Disable via ``--no-telemetry`` CLI flag,
``TOKENCAST_TELEMETRY=0`` environment variable, or by calling the
``disable_telemetry`` MCP tool (creates ``~/.tokencast/no-telemetry``).
Note: ``TOKENCAST_TELEMETRY=1`` overrides ``--no-telemetry`` and the
no-telemetry file (env var is highest priority).

Collected metrics (NO PII, NO project names, NO file paths, NO cost amounts):
  - session_count      — total number of calibration history records
  - mean_accuracy      — mean actual/expected ratio over recent history
  - calibrated_factors — count of active calibration factor entries
  - client_name        — MCP client identifier string (from MCP init, if set)
  - framework          — "mcp" (always, for the MCP server path)
  - tool_name          — MCP tool name that triggered the event
  - tokencast_version  — installed package version string

Fire-and-forget: telemetry is sent on a background thread so it never blocks
the calling code. Failures are silently discarded.

Telemetry is sent to PostHog (US region, https://us.i.posthog.com).
TOKENCAST_TELEMETRY_URL is ignored — the endpoint is fixed at build time.
"""

import json
import logging
import os
import pathlib
import threading
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# PostHog ingest endpoint — US region, fixed at build time.
_POSTHOG_ENDPOINT = "https://us.i.posthog.com/capture/"
_POSTHOG_API_KEY = "phc_BVby7HgLhjC5eV2Byntse8wyULpXsDnGbsTqD2AaXxWD"

# Path to the persistent install ID file
_INSTALL_ID_PATH = pathlib.Path.home() / ".tokencast" / "install_id"
_NO_TELEMETRY_PATH = pathlib.Path.home() / ".tokencast" / "no-telemetry"

# ---------------------------------------------------------------------------
# First-run message — printed to stderr once when telemetry is enabled
# ---------------------------------------------------------------------------

FIRST_RUN_MESSAGE = """\
[tokencast] Telemetry Notice
Anonymous usage telemetry is ON by default.
Collected: session count, mean accuracy, calibrated factors, client name, tool name, version.
NOT collected: project names, file paths, cost amounts, or any personal data.
To disable permanently: call the disable_telemetry tool
To disable via CLI: --no-telemetry flag
To disable via env: TOKENCAST_TELEMETRY=0
Note: TOKENCAST_TELEMETRY=1 overrides --no-telemetry and the no-telemetry file.
Info: https://github.com/krulewis/tokencast/wiki/Configuration#telemetry
"""

# Module-level event so the first-run message is only printed once per process.
# threading.Event.set() is atomic — safe to call from multiple threads without
# an explicit lock.
_first_run_message_shown = threading.Event()

# Module-level install ID cache. Set once and never mutated after that.
# Multiple threads may race to populate this on first call, but the race is
# benign: both threads read the same file and assign the same value, and the
# value is immutable once written. No lock is needed.
_install_id_cache: Optional[str] = None


def _show_first_run_message_once() -> None:
    """Print the first-run consent notice to stderr once per process.

    May print twice under heavy thread contention (benign — cosmetic only).
    """
    if not _first_run_message_shown.is_set():
        _first_run_message_shown.set()
        import sys
        print(FIRST_RUN_MESSAGE, file=sys.stderr, end="")


def _get_or_create_install_id() -> str:
    """Return a persistent install UUID, creating it on first call.

    The ID is stored in ``~/.tokencast/install_id`` and used as the PostHog
    ``distinct_id``. It contains no personal information — it is a random
    UUID4 generated locally.

    Atomic write pattern (H2):
      1. Write to ``install_id.tmp.<pid>``
      2. ``os.rename()`` to ``install_id``
      3. If rename fails (OSError — another process won the race), read the
         winner's file instead.

    Empty or non-UUID4 files are treated as corrupt and regenerated (M4).

    Thread safety (H3): multiple threads may race to set ``_install_id_cache``
    on first call, but the race is benign — both threads read the same file
    and assign the same immutable value. No lock is needed.
    """
    global _install_id_cache
    if _install_id_cache is not None:
        return _install_id_cache

    id_path = _INSTALL_ID_PATH
    id_path.parent.mkdir(parents=True, exist_ok=True)

    # Attempt to read and validate an existing file
    if id_path.exists():
        try:
            raw = id_path.read_text(encoding="utf-8").strip()
            uuid.UUID(raw, version=4)  # raises ValueError if invalid
            _install_id_cache = raw
            return _install_id_cache
        except (OSError, ValueError):
            pass  # Fall through to regenerate

    # Generate a new UUID and write atomically
    new_id = str(uuid.uuid4())
    tmp_path = id_path.parent / f"install_id.tmp.{os.getpid()}"
    try:
        tmp_path.write_text(new_id, encoding="utf-8")
        os.rename(str(tmp_path), str(id_path))
        _install_id_cache = new_id
    except OSError:
        # Another process won the race — read their value if possible
        try:
            raw = id_path.read_text(encoding="utf-8").strip()
            uuid.UUID(raw, version=4)
            _install_id_cache = raw
        except (OSError, ValueError):
            # Last resort: use the in-memory value without persisting
            _install_id_cache = new_id
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    return _install_id_cache


def _get_tokencast_version() -> str:
    """Return the installed tokencast version string, or 'unknown' on failure."""
    try:
        import tokencast
        return tokencast.__version__
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Telemetry enabled check
# ---------------------------------------------------------------------------


def is_enabled(telemetry_enabled: bool = True) -> bool:
    """Return True when telemetry is active.

    Checks four sources in strict priority order. The first source that
    expresses an opinion wins; later sources are not consulted.

    Priority 1 (highest): TOKENCAST_TELEMETRY=0  -->  return False
    Priority 2:           TOKENCAST_TELEMETRY=1  -->  return True
    Priority 3:           ~/.tokencast/no-telemetry file exists  -->  return False
    Priority 4 (lowest):  telemetry_enabled parameter (default=True)

    Note: TOKENCAST_TELEMETRY=1 (priority 2) overrides the no-telemetry file
    (priority 3) and the --no-telemetry flag (which sets telemetry_enabled=False,
    priority 4). Document this precedence clearly in user-facing docs.

    Args:
        telemetry_enabled: Value from ServerConfig. Default True (opt-out model).

    Returns:
        True if telemetry is active, False otherwise.
    """
    env_val = os.environ.get("TOKENCAST_TELEMETRY", "").strip()
    if env_val == "0":
        return False
    if env_val == "1":
        return True
    if _NO_TELEMETRY_PATH.exists():
        return False
    return telemetry_enabled


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

    recent = records[-10:]
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
) -> None:
    """Send *metrics* to PostHog in a background daemon thread.

    Builds a PostHog ``/capture/`` payload with:
      - ``api_key``    — PostHog project token
      - ``event``      — fixed string ``"tool_called"``
      - ``distinct_id`` — persistent install UUID
      - ``timestamp``  — ISO 8601 UTC string from ``metrics["collected_at"]``
      - ``properties`` — all collected metrics, with ``tool_name`` set from
                         the original ``event_type`` value (M8)

    Args:
        metrics: Dict produced by :func:`collect_metrics` with ``event_type``
            and ``install_id`` keys added by :func:`record_event`.
    """
    if _POSTHOG_API_KEY == "phc_PLACEHOLDER":
        logger.debug("PostHog API key is placeholder — events will not be recorded")
        return
    properties = dict(metrics)
    # event_type is not a PostHog property key — remap to tool_name (M8)
    event_type = properties.pop("event_type", "unknown")
    properties["tool_name"] = event_type
    # install_id travels as distinct_id, not in properties
    install_id = properties.pop("install_id", "unknown")

    payload = {
        "api_key": _POSTHOG_API_KEY,
        "event": "tool_called",
        "distinct_id": install_id,
        "timestamp": metrics.get("collected_at", ""),  # M7
        "properties": properties,
    }

    t = threading.Thread(
        target=_send_payload, args=(_POSTHOG_ENDPOINT, payload), daemon=True
    )
    t.start()


# ---------------------------------------------------------------------------
# High-level record-and-send helper
# ---------------------------------------------------------------------------


def record_event(
    event_type: str,
    *,
    telemetry_enabled: bool = True,
    calibration_dir: Optional[str] = None,
    client_name: Optional[str] = None,
    framework: str = "mcp",
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
        telemetry_enabled: Whether telemetry is active (default True — opt-out model).
        calibration_dir: Path to calibration directory. When None, defaults to
            ``~/.tokencast/calibration``.
        client_name: MCP client identifier string, if available.
        framework: Framework / source identifier. Defaults to "mcp".
    """
    try:
        if not is_enabled(telemetry_enabled):
            return

        _show_first_run_message_once()

        # Resolve calibration paths (pathlib now at module level — L10)
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
            from tokencast.calibration_store import read_history, read_factors
            records = read_history(history_path)
            factors = read_factors(factors_path)
        except Exception:
            pass  # Degrade gracefully -- metrics will show zeros

        metrics = collect_metrics(
            records=records,
            factors=factors,
            client_name=client_name,
            framework=framework,
        )
        metrics["event_type"] = event_type
        metrics["install_id"] = _get_or_create_install_id()
        metrics["tokencast_version"] = _get_tokencast_version()

        send_metrics(metrics)

    except Exception:
        # Unconditional safety net — telemetry must never raise or crash
        pass
