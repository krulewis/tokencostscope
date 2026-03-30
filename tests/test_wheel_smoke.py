"""Wheel smoke tests — installed-package behavior (Category 1, SC-1a).

These tests build the tokencast wheel, install it into a fresh virtual
environment, and verify that the package is importable and functional.

They are marked @pytest.mark.slow and excluded from the default pytest
invocation (via addopts in pyproject.toml).  Run them explicitly with:

    pytest tests/test_wheel_smoke.py -m slow -v

Or via the CI wheel-smoke job which sets WHEEL_DIR and runs:

    pytest tests/test_wheel_smoke.py tests/test_package_manifest.py -m slow -v

HC-1 verification note
----------------------
test_wheel_tool_call_works is the test that catches the pre-0.1.3 scripts/
packaging bug.  A bare ``import tokencast`` passes even on broken code because
_load_status_module() is lazy (called on first tool invocation, not on import).

IMPORTANT: this test calls _load_status_module() DIRECTLY rather than via
get_calibration_status() because get_calibration_status() wraps the loader in
an ``except Exception`` handler that swallows FileNotFoundError and returns a
"no_data" dict with exit code 0 — meaning the test would pass on broken code.
Direct invocation bypasses the wrapper so the test correctly fails on pre-0.1.3
code (scripts/ absent) and passes on post-0.1.3 code (module importable).

After merging this PR (before the 0.1.3 fix merges), run:
    pytest tests/test_wheel_smoke.py -m slow -v
and confirm test_wheel_tool_call_works exits non-zero with a message
referencing scripts/tokencast-status.py or a related module.
"""

import subprocess
import sys
from pathlib import Path

import pytest


def _make_venv(tmp_path: Path) -> Path:
    """Create a fresh venv in tmp_path/smokeenv and return its Path."""
    venv_dir = tmp_path / "smokeenv"
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"venv creation failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return venv_dir


def _venv_python(venv_dir: Path) -> str:
    """Return the path to the Python interpreter inside the venv.

    Uses bin/python on Linux/macOS (CI runs ubuntu-latest; local macOS also
    uses bin/).  Windows is not supported by this test suite.
    """
    return str(venv_dir / "bin" / "python")


def _install_wheel(venv_dir: Path, wheel_path: Path) -> None:
    """Install the wheel (non-editable) into the venv.

    Uses ``python -m pip install`` rather than invoking bin/pip directly for
    robustness across environments where the pip shim may not be present.
    """
    venv_python = _venv_python(venv_dir)
    result = subprocess.run(
        [venv_python, "-m", "pip", "install", str(wheel_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"pip install failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.slow
def test_wheel_imports_cleanly(wheel_path, tmp_path):
    """tokencast.__version__ is accessible after installing the wheel.

    This is the basic import smoke test (SC-1a steps 1-4).
    A bare import does NOT trigger the lazy scripts/ load — see
    test_wheel_tool_call_works for the HC-1 validation.
    """
    venv_dir = _make_venv(tmp_path)
    _install_wheel(venv_dir, wheel_path)
    python = _venv_python(venv_dir)

    result = subprocess.run(
        [python, "-c", "import tokencast; print(tokencast.__version__)"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"import tokencast failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # The printed version should be a non-empty semver string.
    assert result.stdout.strip(), (
        f"tokencast.__version__ printed empty string; stdout: {result.stdout!r}"
    )


@pytest.mark.slow
def test_wheel_mcp_server_importable(wheel_path, tmp_path):
    """tokencast_mcp.server.build_server is importable after installing the wheel.

    This validates that the tokencast_mcp package is present in the wheel
    and its server module is importable (SC-1a step 5).
    """
    venv_dir = _make_venv(tmp_path)
    _install_wheel(venv_dir, wheel_path)
    python = _venv_python(venv_dir)

    result = subprocess.run(
        [python, "-c", "from tokencast_mcp.server import build_server; print('ok')"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"from tokencast_mcp.server import build_server failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert result.stdout.strip() == "ok"


@pytest.mark.slow
def test_wheel_tool_call_works(wheel_path, tmp_path):
    """_load_status_module() executes without error in an installed wheel (HC-1).

    This is the HC-1 validation test for the pre-0.1.3 scripts/ packaging bug.

    Calls _load_status_module() directly (not via get_calibration_status) because
    get_calibration_status() wraps the loader in except Exception, swallowing
    FileNotFoundError on pre-0.1.3 code. HC-1 requires the test to FAIL on broken
    code. Direct invocation raises FileNotFoundError immediately on pre-0.1.3 code
    (scripts/ absent from the installed wheel's site-packages) and succeeds on
    post-0.1.3 code (tokencast_status module importable directly).

    On pre-0.1.3 code this test MUST FAIL with a FileNotFoundError or
    ModuleNotFoundError referencing the scripts/ directory or tokencast-status.py.
    If it passes on broken code, the test is not testing what it claims and must
    be revised before merge.
    """
    venv_dir = _make_venv(tmp_path)
    _install_wheel(venv_dir, wheel_path)
    python = _venv_python(venv_dir)

    # Calls _load_status_module() directly (not via get_calibration_status) because
    # get_calibration_status() wraps the loader in except Exception, swallowing
    # FileNotFoundError on pre-0.1.3 code. HC-1 requires the test to FAIL on broken
    # code. Direct invocation raises FileNotFoundError immediately when scripts/ is
    # absent from the installed wheel, and succeeds on post-0.1.3 code where the
    # module is importable as a proper package module.
    snippet = (
        "from tokencast.api import _load_status_module; "
        "_load_status_module(); "
        "print('ok')"
    )
    result = subprocess.run(
        [python, "-c", snippet],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"_load_status_module() raised an error in the installed wheel.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}\n\n"
        f"If this is the pre-0.1.3 codebase, this failure is expected (HC-1 "
        f"confirmed). After the 0.1.3 fix merges, this test must pass."
    )
    assert result.stdout.strip() == "ok"
