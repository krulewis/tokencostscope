"""Version consistency test (Category 5, SC-5a and SC-5b).

Asserts that the three Python version strings that must stay in sync are
actually equal:
  - pyproject.toml  [project] version
  - tokencast.__version__
  - tokencast_mcp.__version__

This test is NOT marked @pytest.mark.slow.  It does not build a wheel.
It runs in the default pytest invocation alongside the rest of the fast suite.

Intentionally independent versions (DO NOT add checks for these)
----------------------------------------------------------------
- SKILL.md carries version "2.1.0" — this is the algorithm reference doc
  version, which follows its own independent semver and is NOT the PyPI
  package version.  Do not add an assertion comparing SKILL.md to pyproject.toml.
- server.json carries a "version" field for the MCP Registry manifest.
  It is updated independently from PyPI releases.  Do not check it here.
"""

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _extract_version_from_file(path: Path) -> str:
    """Extract __version__ = "X.Y.Z" from a Python source file using regex.

    Reads the file directly to avoid importing the module, which prevents
    import-time side effects (e.g. tokencast_mcp imports from mcp.server,
    which may not be installed in all environments — the same reason MCP
    tests are conditionally skipped elsewhere in the suite).

    Raises AssertionError if the version line is not found.
    """
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, (
        f"Could not find '__version__ = \"...\"' in {path}. "
        f"Check that the file has a __version__ assignment."
    )
    return match.group(1)


def _extract_pyproject_version() -> str:
    """Extract the [project] version from pyproject.toml using regex.

    Uses regex rather than tomllib/tomli to avoid a dev dependency.
    The version line format ``version = "X.Y.Z"`` is stable TOML and
    sufficient for a single-field extraction.

    Assumes the first version = "..." line in pyproject.toml is the [project]
    version.  If a [tool.*] section ever adds a version line above [project],
    restrict this search to the text between the [project] header and the next
    [...] section header.

    Raises AssertionError if the version line is not found.
    """
    pyproject_path = REPO_ROOT / "pyproject.toml"
    text = pyproject_path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, (
        f"Could not find 'version = \"...\"' in {pyproject_path}. "
        f"Check that pyproject.toml has a [project] version entry."
    )
    return match.group(1)


def test_pyproject_version_matches_package():
    """pyproject.toml version == tokencast.__version__ == tokencast_mcp.__version__.

    Reads __init__.py files directly (not via import) to avoid triggering
    import-time side effects such as ``from mcp.server import Server`` in
    tokencast_mcp, which would cause a ModuleNotFoundError in environments
    where the mcp package is not installed.

    Intentionally independent versions are excluded — see module docstring.
    """
    pyproject_version = _extract_pyproject_version()

    tokencast_init = REPO_ROOT / "src" / "tokencast" / "__init__.py"
    tokencast_mcp_init = REPO_ROOT / "src" / "tokencast_mcp" / "__init__.py"

    tokencast_version = _extract_version_from_file(tokencast_init)
    tokencast_mcp_version = _extract_version_from_file(tokencast_mcp_init)

    assert pyproject_version == tokencast_version, (
        f"pyproject.toml version ({pyproject_version!r}) does not match "
        f"tokencast.__version__ ({tokencast_version!r}). "
        f"Update src/tokencast/__init__.py or pyproject.toml to match."
    )
    assert pyproject_version == tokencast_mcp_version, (
        f"pyproject.toml version ({pyproject_version!r}) does not match "
        f"tokencast_mcp.__version__ ({tokencast_mcp_version!r}). "
        f"Update src/tokencast_mcp/__init__.py or pyproject.toml to match."
    )
