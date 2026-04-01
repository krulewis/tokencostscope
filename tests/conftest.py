"""Shared pytest fixtures for the tokencast test suite."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def suppress_telemetry():
    """Disable PostHog telemetry for all tests.

    Prevents test runs from sending real events to PostHog, which would
    contaminate Test 3 (report_session adoption) measurement data.
    TOKENCAST_TELEMETRY=0 is the highest-priority opt-out signal in
    telemetry.is_enabled() and overrides all other settings.
    """
    os.environ["TOKENCAST_TELEMETRY"] = "0"

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def wheel_path(tmp_path_factory):
    """Build (or locate) the tokencast wheel and return its Path.

    Resolution order:
    1. If the WHEEL_DIR environment variable is set (used by the CI wheel-smoke
       job which pre-builds the wheel as a separate step), glob for a .whl file
       in that directory and return it.
    2. Otherwise, build the wheel from source using ``python -m build --wheel``
       into a session-scoped temporary directory.

    The fixture is session-scoped so the wheel is built at most once per test
    session regardless of how many tests request it.  When no slow-marked tests
    are selected (the default via addopts), this fixture is never invoked.

    Skips the test (not fails) if:
    - The ``build`` package is not installed and WHEEL_DIR is not set.
    - The wheel build subprocess exits non-zero.
    - No .whl file is found in the output directory.
    """
    # --- Path 1: pre-built wheel provided by CI ---
    wheel_dir = os.environ.get("WHEEL_DIR")
    if wheel_dir:
        matches = list(Path(wheel_dir).glob("*.whl"))
        if len(matches) == 0:
            pytest.skip(f"WHEEL_DIR={wheel_dir!r} set but no .whl file found there")
        if len(matches) > 1:
            pytest.skip(
                f"WHEEL_DIR={wheel_dir!r} contains multiple .whl files: {matches}"
            )
        return matches[0]

    # --- Path 2: build from source ---
    # Verify the build package is available before attempting the subprocess.
    try:
        import importlib.util
        if importlib.util.find_spec("build") is None:
            pytest.skip(
                "The 'build' package is not installed. "
                "Run: pip install 'tokencast[dev]' or pip install build"
            )
    except Exception:
        pytest.skip("Could not verify 'build' package availability")

    out_dir = tmp_path_factory.mktemp("wheel_dist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(
            f"Wheel build failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    matches = list(out_dir.glob("*.whl"))
    if len(matches) == 0:
        pytest.skip(f"Wheel build succeeded but no .whl file found in {out_dir}")
    if len(matches) > 1:
        pytest.skip(f"Wheel build produced multiple .whl files: {matches}")

    return matches[0]
