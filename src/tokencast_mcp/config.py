"""ServerConfig dataclass — runtime configuration for the tokencast MCP server."""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ServerConfig:
    """Holds resolved runtime configuration for the tokencast MCP server.

    All paths are absolute. Use :meth:`from_args` to construct from raw CLI
    argument strings. Call :meth:`ensure_dirs` at server startup to create
    required directories on disk.
    """

    calibration_dir: Path
    project_dir: Optional[Path]
    no_cta: bool = False
    # Mutable per-server-session state: True once the CTA has been shown.
    cta_shown: bool = field(default=False, repr=False)
    telemetry_enabled: bool = True
    client_name: Optional[str] = None
    # Claude Max plan tier for quota-percentage output ("5x", "20x", or None).
    max_plan: Optional[str] = None

    # ------------------------------------------------------------------
    # Derived paths (not fields — computed on demand)
    # ------------------------------------------------------------------

    @property
    def history_path(self) -> Path:
        return self.calibration_dir / "history.jsonl"

    @property
    def factors_path(self) -> Path:
        return self.calibration_dir / "factors.json"

    @property
    def active_estimate_path(self) -> Path:
        return self.calibration_dir / "active-estimate.json"

    @property
    def last_estimate_path(self) -> Path:
        return self.calibration_dir / "last-estimate.md"

    # ------------------------------------------------------------------
    # Constructor
    # ------------------------------------------------------------------

    @classmethod
    def from_args(
        cls,
        calibration_dir: Optional[str],
        project_dir: Optional[str],
        no_cta: bool = False,
        telemetry_enabled: bool = True,
        max_plan: Optional[str] = None,
    ) -> "ServerConfig":
        """Build a ServerConfig from raw CLI argument strings.

        Args:
            calibration_dir: Raw ``--calibration-dir`` value, or ``None``.
            project_dir: Raw ``--project-dir`` value, or ``None``.
            no_cta: When ``True``, suppress the team-sharing waitlist CTA.
            telemetry_enabled: When ``True``, opt in to anonymous telemetry.
            max_plan: Claude Max plan tier (``"5x"``, ``"20x"``, or ``None``).
                Also read from the ``TOKENCAST_MAX_PLAN`` environment variable
                when not provided as a CLI argument.

        Returns:
            A fully resolved :class:`ServerConfig`.
        """
        if project_dir is not None:
            resolved_project_dir: Optional[Path] = Path(project_dir).expanduser().resolve()
        else:
            resolved_project_dir = None

        if calibration_dir is not None:
            resolved_calibration_dir = Path(calibration_dir).expanduser().resolve()
        elif resolved_project_dir is not None:
            resolved_calibration_dir = resolved_project_dir / "calibration"
        else:
            resolved_calibration_dir = Path.home() / ".tokencast" / "calibration"

        # CLI arg takes precedence; fall back to env var.
        if max_plan is None:
            max_plan = os.environ.get("TOKENCAST_MAX_PLAN") or None

        if max_plan is not None:
            from tokencast_mcp.max_plan import VALID_MAX_PLANS
            if max_plan not in VALID_MAX_PLANS:
                print(
                    f"[tokencast] Warning: TOKENCAST_MAX_PLAN={max_plan!r} is not a valid "
                    f"plan tier. Expected one of: {sorted(VALID_MAX_PLANS)}. Ignoring.",
                    file=sys.stderr,
                )
                max_plan = None

        return cls(
            calibration_dir=resolved_calibration_dir,
            project_dir=resolved_project_dir,
            no_cta=no_cta,
            telemetry_enabled=telemetry_enabled,
            max_plan=max_plan,
        )

    def ensure_dirs(self) -> None:
        """Create calibration_dir on disk if it does not exist.

        Call this at server startup, not during config construction, so that
        tests can build a ServerConfig without touching the filesystem.
        """
        self.calibration_dir.mkdir(parents=True, exist_ok=True)
