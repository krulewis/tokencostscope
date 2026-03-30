"""Package manifest invariant tests (Category 2, SC-2a and SC-2b).

These tests inspect the built wheel's zip contents to assert that:
- All required source modules are present in the wheel (SC-2a).
- No source file inside the wheel uses the broken parent.parent.parent / "scripts"
  path traversal pattern that caused the 0.1.3 packaging bug (SC-2b).

They are marked @pytest.mark.slow and excluded from the default pytest
invocation.  Run them explicitly with:

    pytest tests/test_package_manifest.py -m slow -v

SC-2b design note
-----------------
The FORBIDDEN_PATTERN regex catches the exact code pattern that causes the
0.1.3 failure: pathlib.Path(__file__).resolve().parent.parent.parent / "scripts".
On the pre-0.1.3 codebase this pattern appears in:
  - src/tokencast/api.py (multiple occurrences)
  - src/tokencast/estimation_engine.py (1 occurrence)
  - src/tokencast/telemetry.py (1 occurrence)
After the 0.1.3 fix these occurrences must drop to zero.  This test asserts
that invariant and will fail on pre-0.1.3 code (expected — confirms HC-1).

Manifest maintenance note (SC-2a)
----------------------------------
REQUIRED_MODULES lists every source file the package needs at runtime.
When adding a new module to src/tokencast/ or src/tokencast_mcp/, you MUST
add it to REQUIRED_MODULES.  This is intentional — the list is a conscious
manifest, not an auto-generated snapshot.
"""

import re
import zipfile

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Every .py file that must be present in the installed wheel.
# Adding a new module requires a deliberate update to this list.
#
REQUIRED_MODULES = [
    "tokencast/__init__.py",
    "tokencast/api.py",
    "tokencast/calibration_store.py",
    "tokencast/estimation_engine.py",
    "tokencast/file_measurement.py",
    "tokencast/parse_last_estimate.py",
    "tokencast/pricing.py",
    "tokencast/heuristics.py",
    "tokencast/session_recorder.py",
    "tokencast/step_names.py",
    "tokencast/telemetry.py",
    "tokencast/tokencast_status.py",
    "tokencast/update_factors.py",
    "tokencast_mcp/__init__.py",
    "tokencast_mcp/__main__.py",
    "tokencast_mcp/config.py",
    "tokencast_mcp/server.py",
]

# At least one .py file must exist under this path prefix.
REQUIRED_TOOL_DIR_PREFIX = "tokencast_mcp/tools/"

# SC-2b: No file inside the wheel may contain this pattern.
# This is the path traversal that breaks when scripts/ is absent from the
# installed wheel.  After the 0.1.3 fix this count must be zero.
FORBIDDEN_PATTERN = re.compile(r"parent\.parent\.parent.*scripts")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_required_modules_present(wheel_path):
    """All required source modules are present in the wheel zip (SC-2a).

    The wheel format (PEP 427) stores files under a
    ``{name}-{version}.dist-info/`` prefix or directly at the package root.
    We match by checking that the wheel namelist contains an entry whose
    path ends with each required module suffix (e.g. ends with
    "tokencast/__init__.py").  This is resilient to version string changes in
    the wheel filename prefix.
    """
    with zipfile.ZipFile(wheel_path) as whl:
        names = whl.namelist()

    missing = []
    for required in REQUIRED_MODULES:
        # Wheel entries look like "tokencast/__init__.py" (no version prefix
        # for pure-Python wheels built with hatchling).  Check both exact
        # match and suffix match to be resilient.
        if not any(n == required or n.endswith("/" + required) for n in names):
            missing.append(required)

    assert not missing, (
        f"The following required modules are absent from the wheel:\n"
        + "\n".join(f"  - {m}" for m in missing)
        + f"\n\nAll wheel entries:\n"
        + "\n".join(f"  {n}" for n in sorted(names))
    )

    # Assert at least one .py file exists under tokencast_mcp/tools/.
    tool_files = [
        n for n in names
        if (n.startswith(REQUIRED_TOOL_DIR_PREFIX) or
            ("/" + REQUIRED_TOOL_DIR_PREFIX) in n)
        and n.endswith(".py")
    ]
    assert tool_files, (
        f"No .py files found under '{REQUIRED_TOOL_DIR_PREFIX}' in the wheel.\n"
        f"Expected at least one tool handler. Wheel entries:\n"
        + "\n".join(f"  {n}" for n in sorted(names))
    )


@pytest.mark.slow
def test_no_scripts_path_references(wheel_path):
    """No source file in the wheel uses the broken parent.parent.parent/scripts
    path traversal pattern (SC-2b).

    This is the invariant that would have caught the 0.1.3 bug.  The pattern
    ``parent.parent.parent.*scripts`` identifies code that resolves a path
    relative to __file__ by climbing three parent directories to reach the
    repo root and then descending into scripts/.  This path is invalid inside
    an installed wheel because the repo root does not exist in site-packages.

    On the pre-0.1.3 codebase this test MUST FAIL (multiple occurrences across
    api.py, estimation_engine.py, telemetry.py).  After the 0.1.3 fix it
    must pass with zero matches.
    """
    violations = []  # list of (zip_entry_name, line_number, line_text)

    with zipfile.ZipFile(wheel_path) as whl:
        for entry in whl.namelist():
            if not entry.endswith(".py"):
                continue
            try:
                source = whl.read(entry).decode("utf-8", errors="replace")
            except Exception as exc:
                # Unreadable file — flag it but don't crash the test.
                violations.append((entry, 0, f"<could not read: {exc}>"))
                continue
            for lineno, line in enumerate(source.splitlines(), start=1):
                if FORBIDDEN_PATTERN.search(line):
                    violations.append((entry, lineno, line.strip()))

    assert not violations, (
        f"Found {len(violations)} occurrence(s) of the forbidden "
        f"parent.parent.parent/scripts path traversal pattern in the wheel.\n"
        f"These will cause FileNotFoundError when the package is installed "
        f"(not run from the repo checkout).\n\n"
        + "\n".join(
            f"  {entry}:{lineno}: {text}"
            for entry, lineno, text in violations
        )
    )
