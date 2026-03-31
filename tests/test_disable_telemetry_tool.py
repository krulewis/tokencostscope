"""Tests for the disable_telemetry MCP tool handler.

Covers:
- File creation (creates ~/.tokencast/no-telemetry)
- Idempotency (calling twice returns success both times)
- Response shape (status, file, message keys)
- Integration with is_enabled() (file causes is_enabled to return False)
- Permission errors return error dict rather than raising
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

_src_root = Path(__file__).resolve().parent.parent / "src"
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="requires Python 3.10+ (mcp dependency)",
)

pytest.importorskip("mcp")

from tokencast import telemetry as _telemetry  # noqa: E402
from tokencast_mcp.config import ServerConfig  # noqa: E402


def _make_config(tmp_dir: str) -> ServerConfig:
    return ServerConfig.from_args(
        calibration_dir=tmp_dir,
        project_dir=None,
        telemetry_enabled=True,
    )


def _get_handler():
    """Import handler lazily so collection succeeds before the module exists."""
    from tokencast_mcp.tools.disable_telemetry import handle_disable_telemetry
    return handle_disable_telemetry


def test_creates_no_telemetry_file():
    """Handler creates the no-telemetry file and returns status='disabled'."""
    handle_disable_telemetry = _get_handler()
    with tempfile.TemporaryDirectory() as tmp:
        fake_path = Path(tmp) / "no-telemetry"
        config = _make_config(tmp)
        with patch.object(_telemetry, "_NO_TELEMETRY_PATH", fake_path):
            result = asyncio.run(handle_disable_telemetry({}, config))
        assert fake_path.exists(), "no-telemetry file was not created"
        assert result["status"] == "disabled"


def test_idempotent():
    """Calling handler twice does not raise and both calls return status='disabled'."""
    handle_disable_telemetry = _get_handler()
    with tempfile.TemporaryDirectory() as tmp:
        fake_path = Path(tmp) / "no-telemetry"
        config = _make_config(tmp)
        with patch.object(_telemetry, "_NO_TELEMETRY_PATH", fake_path):
            result1 = asyncio.run(handle_disable_telemetry({}, config))
            result2 = asyncio.run(handle_disable_telemetry({}, config))
        assert result1["status"] == "disabled"
        assert result2["status"] == "disabled"
        assert fake_path.exists()


def test_response_shape():
    """Handler response contains 'status', 'file', and 'message' keys."""
    handle_disable_telemetry = _get_handler()
    with tempfile.TemporaryDirectory() as tmp:
        fake_path = Path(tmp) / "no-telemetry"
        config = _make_config(tmp)
        with patch.object(_telemetry, "_NO_TELEMETRY_PATH", fake_path):
            result = asyncio.run(handle_disable_telemetry({}, config))
        assert "status" in result
        assert "file" in result
        assert "message" in result
        assert result["file"] == str(fake_path)


def test_is_enabled_respects_file():
    """After tool call, is_enabled() returns False (integration: tool + is_enabled agree)."""
    handle_disable_telemetry = _get_handler()
    with tempfile.TemporaryDirectory() as tmp:
        fake_path = Path(tmp) / "no-telemetry"
        config = _make_config(tmp)
        with patch.object(_telemetry, "_NO_TELEMETRY_PATH", fake_path):
            asyncio.run(handle_disable_telemetry({}, config))
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TOKENCAST_TELEMETRY", None)
                assert _telemetry.is_enabled() is False


def test_permission_error_returns_error_dict():
    """When write fails with OSError, handler returns error dict without raising."""
    handle_disable_telemetry = _get_handler()
    with tempfile.TemporaryDirectory() as tmp:
        fake_path = Path(tmp) / "no-telemetry"
        config = _make_config(tmp)
        with patch.object(_telemetry, "_NO_TELEMETRY_PATH", fake_path):
            with patch("pathlib.Path.write_text", side_effect=OSError("Permission denied")):
                result = asyncio.run(handle_disable_telemetry({}, config))
        assert result["status"] == "error"
        assert "message" in result
        assert result["message"]  # non-empty string
